"""HTTP/2 (and HTTP/3) probe backed by httpx.

The stdlib `http_probe.probe_once` only does HTTP/1.1. This module is the
modern complement: it uses httpx with httpcore underneath to negotiate HTTP/2
via ALPN (and HTTP/3 if h3 extras are installed).

Tradeoffs vs the stdlib probe:
- ✓ Real HTTP/2 (multiplexing, HPACK header compression, etc.)
- ✓ ALPN negotiation reveals what the server actually supports
- ✗ DNS/TCP/TLS timings are not split (httpx doesn't expose them per-stage
  cleanly). Only total + TTFB approximation are returned.

Use cases:
- Compare HTTP/1.1 vs HTTP/2 on the same URL (httpx handles the negotiation)
- Confirm what protocol the server actually serves
- Measure end-to-end latency for a real modern client (most browsers use
  HTTP/2 since ~2018)

Returns a HttpSample (same dataclass as http_probe), with dns/tcp/tls set to
None and `http_version_used` populated.
"""
from __future__ import annotations

import time

from .http_probe import HttpSample


def _ms(start: float) -> float:
    return (time.monotonic() - start) * 1000.0


def probe_once_modern(
    url: str,
    http_version: str = "2",          # "1.1" | "2" — httpx negotiates via ALPN
    method: str = "GET",
    timeout: float = 10.0,
    sample_idx: int = 0,
    user_agent: str = "mtrgraph-modern/0.1",
    extra_headers: dict | None = None,
    body: bytes | str | None = None,
) -> HttpSample:
    """Single httpx probe. Returns the same HttpSample shape as probe_once."""
    try:
        import httpx
    except ImportError:
        return HttpSample(
            sample_idx, None, None, None, None, None,
            None, None, "httpx not installed (pip install 'httpx[http2]')",
        )

    headers = {"User-Agent": user_agent, "Accept": "*/*"}
    if extra_headers:
        headers.update(extra_headers)

    overall_start = time.monotonic()
    enable_http2 = http_version == "2"

    try:
        # `http2=True` enables ALPN negotiation; server still picks the highest
        # protocol it supports. `http1=True` keeps fallback to HTTP/1.1.
        with httpx.Client(
            http1=True, http2=enable_http2,
            timeout=timeout, verify=True, follow_redirects=False,
        ) as client:
            t0 = time.monotonic()
            # Use streaming to approximate TTFB: timer stops when headers
            # arrive, then we drain the body for the total measurement.
            with client.stream(method, url, headers=headers, content=body) as response:
                ttfb_ms = _ms(t0)
                # Drain body to get a real total_ms (and accurate content length)
                body_size = 0
                for chunk in response.iter_bytes():
                    body_size += len(chunk)
    except Exception as e:
        return HttpSample(
            sample_idx, None, None, None, None, _ms(overall_start),
            None, None, f"httpx: {type(e).__name__}: {e}",
        )

    total_ms = _ms(overall_start)
    resolved_ip = None
    # httpx exposes the network stream via response.extensions; try to fetch
    # the peer address.
    try:
        stream = response.extensions.get("network_stream")
        if stream:
            peer = stream.get_extra_info("remote_addr")
            if peer:
                resolved_ip = peer[0]
    except Exception:
        pass

    # Content-Length: prefer the header (declared), fall back to bytes read
    cl_header = response.headers.get("content-length")
    try:
        content_length = int(cl_header) if cl_header else body_size or None
    except (ValueError, TypeError):
        content_length = body_size or None

    sample = HttpSample(
        sample_idx=sample_idx,
        dns_ms=None, tcp_ms=None, tls_ms=None,
        ttfb_ms=ttfb_ms, total_ms=total_ms,
        status=response.status_code,
        resolved_ip=resolved_ip,
        error=None,
        content_length=content_length,
        content_type=response.headers.get("content-type"),
        content_encoding=response.headers.get("content-encoding"),
        server=response.headers.get("server"),
        cache_status=(response.headers.get("x-cache")
                      or response.headers.get("cf-cache-status")),
        response_headers=dict(response.headers),
        http_version_used=response.http_version,   # "HTTP/1.1" | "HTTP/2"
    )
    return sample


def probe_many_modern(
    url: str,
    count: int = 10,
    http_version: str = "2",
    method: str = "GET",
    timeout: float = 10.0,
    interval: float = 0.5,
    extra_headers: dict | None = None,
) -> list[HttpSample]:
    out = []
    for i in range(count):
        out.append(probe_once_modern(
            url, http_version=http_version, method=method, timeout=timeout,
            sample_idx=i + 1, extra_headers=extra_headers,
        ))
        if i + 1 < count:
            time.sleep(interval)
    return out
