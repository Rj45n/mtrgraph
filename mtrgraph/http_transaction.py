"""Synthetic HTTP transactions — sequence of steps with shared cookie jar.

A transaction is a list of `steps`:
[
  {"method": "GET",  "url": "https://api.example.com/login",  "headers": {...}, "body": "...", "expect_status": [200, 302]},
  {"method": "POST", "url": "https://api.example.com/data", ...},
  {"method": "GET",  "url": "https://api.example.com/logout"},
]

Cookies set by `Set-Cookie` in step N are sent automatically in step N+1 (same
host or any host, simple jar). Each step's timings are stored individually.

Built on http_probe.probe_once() with an extra `Cookie:` header passed via
`extra_headers` — we hack the probe a bit to support that.

This is a MVP — no header extraction (e.g. Bearer from JSON body), no body
templating, no parallelism. Good enough for "login then fetch then logout"
type workflows.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

from .http_probe import HttpSample, probe_once


@dataclass
class StepResult:
    step_idx: int
    method: str
    url: str
    sample: HttpSample
    expected_status: list[int] | None
    ok: bool
    cookies_received: list[str] = field(default_factory=list)


@dataclass
class TransactionResult:
    name: str
    steps: list[StepResult]
    total_ms: float
    success_count: int
    error_count: int

    @property
    def all_ok(self) -> bool:
        return self.error_count == 0


def _parse_set_cookie(value: str) -> tuple[str, str] | None:
    """Extract just (name, value) from a Set-Cookie header — ignores attributes
    (Domain, Path, Expires, etc.). Good enough for a synthetic transaction
    that just needs to round-trip the session cookie."""
    if not value:
        return None
    pair = value.split(";", 1)[0]
    if "=" not in pair:
        return None
    name, _, val = pair.partition("=")
    return name.strip(), val.strip()


def run_transaction(
    name: str,
    steps: list[dict],
    timeout: float = 10.0,
    user_agent: str = "mtrgraph-tx/0.1",
) -> TransactionResult:
    """Execute the steps in sequence, persisting cookies between them.

    Each step dict supports:
    - method: HEAD|GET|POST|PUT|DELETE (default GET)
    - url: required, must be absolute (http(s)://...)
    - headers: optional dict of extra request headers
    - body: optional str (will be UTF-8 encoded)
    - expect_status: optional list of acceptable status codes (default any 2xx/3xx)
    """
    jar: dict[str, str] = {}
    results: list[StepResult] = []
    wall_start = time.monotonic()

    for i, step in enumerate(steps, start=1):
        method = (step.get("method") or "GET").upper()
        url = step.get("url")
        if not url:
            raise ValueError(f"step {i} missing 'url'")

        # Build extra headers: user-provided + Cookie jar (host-agnostic MVP)
        extra = dict(step.get("headers") or {})
        if jar:
            cookie_hdr = "; ".join(f"{k}={v}" for k, v in jar.items())
            extra["Cookie"] = cookie_hdr

        sample = probe_once(
            url, method=method, extra_headers=extra,
            body=step.get("body"), timeout=timeout, user_agent=user_agent,
            sample_idx=i,
        )

        # Extract Set-Cookie headers from response into the jar
        cookies_received: list[str] = []
        for sc in (sample.set_cookie_headers or []):
            pair = _parse_set_cookie(sc)
            if pair:
                jar[pair[0]] = pair[1]
                cookies_received.append(pair[0])

        expected = step.get("expect_status")
        if expected:
            ok = sample.status in expected and sample.error is None
        else:
            ok = sample.error is None and sample.status is not None and 200 <= sample.status < 400

        results.append(StepResult(
            step_idx=i, method=method, url=url,
            sample=sample, expected_status=expected, ok=ok,
            cookies_received=cookies_received,
        ))

        # Bail out on first failure (most realistic — a login fail breaks the chain)
        if not ok:
            break

    wall_ms = (time.monotonic() - wall_start) * 1000.0
    succ = sum(1 for r in results if r.ok)
    err = len(results) - succ
    return TransactionResult(
        name=name, steps=results, total_ms=wall_ms,
        success_count=succ, error_count=err,
    )


