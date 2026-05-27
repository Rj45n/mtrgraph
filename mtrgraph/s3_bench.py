"""S3 throughput benchmark — N concurrent ops, aggregate stats.

Reveals whether throughput caps are per-connection (would scale with concurrency)
or global (don't scale). Useful to spot rate-limiting or single-flow TCP caps.

Each individual op is stored in s3_runs with a shared label so the dashboard
can group them.
"""
from __future__ import annotations

import os
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path

from . import db, s3_client


@dataclass
class BenchSummary:
    operation: str
    endpoint: str
    bucket: str
    concurrency: int
    total_ops: int
    successful_ops: int
    errors: int
    total_bytes: int
    total_wall_s: float
    throughput_mbps: float
    ops_per_sec: float
    p50_ms: float | None
    p95_ms: float | None
    p99_ms: float | None
    avg_ms: float | None
    min_ms: float | None
    max_ms: float | None
    label: str


def _percentile(values, p):
    if not values:
        return None
    v = sorted(values)
    k = int(round((p / 100.0) * (len(v) - 1)))
    return v[k]


def run_bench(
    operation: str,                 # 'get' | 'put'
    endpoint: str,
    bucket: str,
    access_key: str,
    secret_key: str,
    region: str,
    *,
    key_or_prefix: str = "mtrgraph-bench/",
    concurrency: int = 10,
    count: int = 100,
    object_size_kb: int = 64,
    session_token: str | None = None,
    timeout: float = 30.0,
    label: str = "bench",
    db_path: Path | None = None,
    progress_fn=None,
    track_puts: bool = True,
) -> BenchSummary:
    """Run `count` operations across `concurrency` threads.

    - For PUT: each thread generates a fresh key under `key_or_prefix`.
      Tracked in s3_tracked_objects with schedule_id=0 (so the standard purge
      mechanism still recognizes them as ours).
    - For GET: `key_or_prefix` is the exact key to fetch repeatedly.
    """
    if operation not in ("get", "put"):
        raise ValueError("operation must be 'get' or 'put'")

    if operation == "put":
        prefix = key_or_prefix if key_or_prefix.endswith("/") else key_or_prefix + "/"
        body = os.urandom(object_size_kb * 1024)

    common = dict(
        access_key=access_key, secret_key=secret_key, region=region,
        session_token=session_token, timeout=timeout, label=label,
    )

    def worker(i: int):
        if operation == "put":
            key = f"{prefix}bench-{int(time.time())}-{uuid.uuid4().hex[:8]}-{i}"
            result = s3_client.put_object(endpoint, bucket, key, body, **common)
            result.key = key
        else:
            result = s3_client.get_object(endpoint, bucket, key_or_prefix, **common)
        return result

    wall_start = time.monotonic()
    results = []
    with ThreadPoolExecutor(max_workers=concurrency) as ex:
        futures = [ex.submit(worker, i) for i in range(count)]
        done = 0
        for fut in as_completed(futures):
            r = fut.result()
            results.append(r)
            done += 1
            if progress_fn:
                progress_fn(done, count)
    wall_s = time.monotonic() - wall_start

    successful = [r for r in results if not r.error and r.http_status is not None and 200 <= r.http_status < 400]
    errs = len(results) - len(successful)
    durations = [r.duration_ms for r in successful if r.duration_ms is not None]
    total_bytes = sum(
        (len(body) if operation == "put" else (r.bytes_transferred or 0))
        for r in successful
    ) if successful else 0

    if db_path is not None:
        with db.session(db_path) as conn:
            for r in results:
                db.insert_s3_run(conn, r)
                # If PUT succeeded and we want tracking, register it
                if track_puts and operation == "put" and not r.error \
                        and r.http_status and 200 <= r.http_status < 300:
                    db.track_s3_object(
                        conn, schedule_id=0, endpoint=endpoint,
                        bucket=bucket, key=r.key, size_bytes=len(body),
                    )

    throughput_mbps = (total_bytes / wall_s) / (1024 * 1024) if wall_s > 0 else 0
    return BenchSummary(
        operation=operation,
        endpoint=endpoint,
        bucket=bucket,
        concurrency=concurrency,
        total_ops=len(results),
        successful_ops=len(successful),
        errors=errs,
        total_bytes=total_bytes,
        total_wall_s=wall_s,
        throughput_mbps=throughput_mbps,
        ops_per_sec=len(results) / wall_s if wall_s > 0 else 0,
        p50_ms=_percentile(durations, 50),
        p95_ms=_percentile(durations, 95),
        p99_ms=_percentile(durations, 99),
        avg_ms=sum(durations) / len(durations) if durations else None,
        min_ms=min(durations) if durations else None,
        max_ms=max(durations) if durations else None,
        label=label,
    )
