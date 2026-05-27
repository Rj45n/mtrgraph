"""HTTP probe executor."""
from __future__ import annotations

from ...http_probe import aggregate as http_aggregate
from ...http_probe import probe_many as http_probe_many
from ...http_probe import status_summary as http_status_summary


def execute(config: dict) -> tuple[list, str, int, str | None]:
    """Run an HTTP probe from a schedule config.
    Returns (samples, status_summary, errors, resolved_ip)."""
    url = config["url"]
    method = config.get("method", "HEAD")
    count = int(config.get("count", 5))
    timeout = float(config.get("timeout", 10.0))
    force_ip = config.get("ip") or None
    follow_redirects = bool(config.get("follow_redirects", False))

    samples = http_probe_many(
        url, count=count, method=method, timeout=timeout,
        interval=min(0.5, 2.0), force_ip=force_ip,
        follow_redirects=follow_redirects,
    )
    agg = http_aggregate(samples)
    summary = http_status_summary(agg["status_counts"])
    errors = agg["errors"]
    resolved_ip = next((s.resolved_ip for s in samples if s.resolved_ip), None)
    return samples, summary, errors, resolved_ip
