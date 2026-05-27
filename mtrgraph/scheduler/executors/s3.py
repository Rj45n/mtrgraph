"""S3 executor + random_ops logic + auto-compare to baseline."""
from __future__ import annotations

import os as _os
import random
import time
import uuid
from pathlib import Path

from ... import db, s3_client

# Per-stage thresholds for S3 auto-compare. (warn_ratio, crit_ratio, min_delta_ms)
S3_DEGRADATION = {
    "dns":   (1.5, 3.0, 20),
    "tcp":   (1.5, 3.0, 20),
    "tls":   (1.5, 3.0, 30),
    "ttfb":  (1.5, 3.0, 50),
    "total": (1.5, 3.0, 100),
}


def _s3_degradation(current_ms: float | None, baseline_ms: float | None, stage: str) -> str | None:
    if current_ms is None or baseline_ms is None:
        return None
    warn_ratio, crit_ratio, min_delta = S3_DEGRADATION[stage]
    delta = current_ms - baseline_ms
    if delta < min_delta:
        return None
    if baseline_ms > 0 and current_ms >= baseline_ms * crit_ratio:
        return "critical"
    if baseline_ms > 0 and current_ms >= baseline_ms * warn_ratio:
        return "warning"
    return None


def pick_random_op(pool_size: int, min_pool: int, max_pool: int) -> str:
    """Weighted random S3 op to maintain a self-sustaining pool."""
    if pool_size < min_pool:
        return random.choices(["put", "list"], weights=[0.85, 0.15], k=1)[0]
    if pool_size >= max_pool:
        return random.choices(["delete", "list", "head", "get"],
                              weights=[0.55, 0.15, 0.15, 0.15], k=1)[0]
    return random.choices(
        ["list", "head", "get", "put", "delete"],
        weights=[0.20, 0.20, 0.25, 0.20, 0.15],
        k=1,
    )[0]


def execute(config: dict, db_path: Path | None = None, schedule_id: int | None = None) -> tuple[object, str]:
    """Run a single S3 op. Returns (S3Result, status_str)."""
    random_ops = bool(config.get("random_ops"))
    forced_op = None
    forced_key = None
    track_after_put = False
    delete_tracked_id = None
    prefix = (config.get("prefix") or "").strip()

    if random_ops:
        if not prefix:
            raise ValueError("random_ops requires a non-empty prefix (safety)")
        if not prefix.endswith("/"):
            prefix = prefix + "/"
        if not db_path or schedule_id is None:
            raise ValueError("random_ops requires db_path and schedule_id")
        min_pool = int(config.get("min_pool_size", 5))
        max_pool = int(config.get("max_pool_size", 100))
        with db.session(db_path) as conn:
            tracked = db.list_tracked_alive(conn, schedule_id)
        pool_size = len(tracked)
        forced_op = pick_random_op(pool_size, min_pool, max_pool)
        if forced_op in ("head", "get"):
            if not tracked:
                forced_op = "put"
            else:
                forced_key = random.choice(tracked)["key"]
        elif forced_op == "delete":
            if not tracked:
                forced_op = "put"
            else:
                row = random.choice(tracked)
                forced_key = row["key"]
                delete_tracked_id = row["id"]
        if forced_op == "put":
            forced_key = f"{prefix}probe-{int(time.time())}-{uuid.uuid4().hex[:8]}"
            track_after_put = True

    op = (forced_op or config["operation"]).lower()
    common = dict(
        access_key=config["access_key"],
        secret_key=config["secret_key"],
        region=config.get("region", "us-east-1"),
        session_token=config.get("session_token") or None,
        timeout=float(config.get("timeout", 30.0)),
        label=config.get("label"),
    )
    endpoint = config["endpoint"]
    bucket = config["bucket"]
    key = forced_key if forced_key is not None else config.get("key")
    pool = config.get("keys_pool") or []
    if not random_ops and pool and op in ("head", "get", "delete"):
        key = random.choice(pool)

    if op == "list":
        list_prefix = prefix if random_ops else (config.get("prefix") or "")
        result = s3_client.list_bucket(
            endpoint, bucket,
            prefix=list_prefix,
            max_keys=int(config.get("max_keys") or 1000),
            **common,
        )
    elif op == "head":
        result = s3_client.head_object(endpoint, bucket, key, **common)
    elif op == "get":
        result = s3_client.get_object(endpoint, bucket, key, **common)
    elif op == "put":
        size_kb = config.get("body_size_kb") or config.get("object_size_kb")
        if size_kb:
            body = _os.urandom(int(size_kb) * 1024)
        elif config.get("body_text"):
            body = config["body_text"].encode("utf-8")
        else:
            body = b"mtrgraph scheduler payload"
        if not key:
            key = f"mtrgraph-sched-{int(time.time())}"
        elif "{ts}" in key:
            key = key.replace("{ts}", str(int(time.time())))
        result = s3_client.put_object(
            endpoint, bucket, key, body,
            content_type=config.get("content_type", "application/octet-stream"),
            **common,
        )
        result.key = key
        if random_ops and track_after_put and not result.error \
                and result.http_status is not None and 200 <= result.http_status < 300:
            with db.session(db_path) as conn:
                db.track_s3_object(
                    conn, schedule_id=schedule_id, endpoint=endpoint,
                    bucket=bucket, key=key, size_bytes=len(body),
                )
    elif op == "delete":
        if random_ops and delete_tracked_id is None:
            raise ValueError("safety: refusing to DELETE without a tracked id")
        result = s3_client.delete_object(endpoint, bucket, key, **common)
        if random_ops and delete_tracked_id is not None \
                and not result.error \
                and result.http_status is not None and 200 <= result.http_status < 300:
            with db.session(db_path) as conn:
                db.mark_tracked_deleted(conn, delete_tracked_id)
    else:
        raise ValueError(f"unknown s3 operation: {op!r}")

    op_tag = f"random={op}" if random_ops else op
    if result.error:
        status = f"err:{op_tag}:{result.error[:40]}"
    elif result.http_status is not None:
        if 200 <= result.http_status < 400:
            status = f"ok:{op_tag}:{result.http_status}"
        else:
            status = f"http:{op_tag}:{result.http_status}"
    else:
        status = f"unknown:{op_tag}"
    return result, status


def status_with_compare(result, db_path: Path, config: dict, base_status: str) -> str:
    """Upgrade `base_status` to warning/critical if a stage degrades vs baseline."""
    if not config.get("auto_compare") or not base_status.startswith("ok:"):
        return base_status
    last_n = int(config.get("baseline_n", 10))
    with db.session(db_path) as conn:
        baseline = db.s3_baseline(
            conn, result.endpoint, result.operation, result.bucket, last_n=last_n,
        )
    if not baseline.get("n_runs"):
        return base_status
    current_map = {
        "dns": result.dns_ms, "tcp": result.tcp_ms, "tls": result.tls_ms,
        "ttfb": result.ttfb_ms, "total": result.duration_ms,
    }
    worst_stage = None
    worst_sev = None
    for stage, cur in current_map.items():
        base = baseline.get(stage, {}).get("avg_ms")
        sev = _s3_degradation(cur, base, stage)
        if sev == "critical":
            worst_sev = "critical"
            worst_stage = (stage, cur, base)
            break
        if sev == "warning" and worst_sev != "critical":
            worst_sev = "warning"
            worst_stage = (stage, cur, base)
    if worst_sev:
        stage, cur, base = worst_stage
        return f"{worst_sev}:{stage} {base:.0f}→{cur:.0f}ms"
    return f"ok:vs-baseline({baseline['n_runs']})"
