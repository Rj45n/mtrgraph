"""KPI computation — pure functions, no I/O.

Used by the dashboard to derive higher-order signals from raw run rows.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta, timezone


def _ts(r) -> datetime | None:
    s = r.get("started_at") if isinstance(r, dict) else r["started_at"]
    if not s:
        return None
    try:
        d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d


def _g(r, key):
    """Get a field from a row that can be dict or sqlite3.Row."""
    if isinstance(r, dict):
        return r.get(key)
    try:
        return r[key]
    except (KeyError, IndexError):
        return None


def stddev(values: list[float | None]) -> float | None:
    """Population stddev. Returns None if < 2 non-null values."""
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return None
    mean = sum(clean) / len(clean)
    var = sum((x - mean) ** 2 for x in clean) / len(clean)
    return var ** 0.5


def apdex(values_ms: list[float | None], target_ms: float = 500.0) -> float | None:
    """Apdex score: (satisfied + tolerating/2) / total. Target T, tolerating up to 4T.

    Standard SRE/UX metric: 1.0 = perfect, 0.0 = catastrophic.
    Typical thresholds: ≥0.94 excellent · 0.85-0.93 good · 0.7-0.84 fair · <0.7 poor."""
    clean = [v for v in values_ms if v is not None]
    if not clean:
        return None
    tol = target_ms * 4
    satisfied = sum(1 for v in clean if v <= target_ms)
    tolerating = sum(1 for v in clean if target_ms < v <= tol)
    return (satisfied + tolerating / 2.0) / len(clean)


def failure_modes(rows: list) -> dict[str, int]:
    """Count failures by category.

    Categories: 'ok', 'http_4xx', 'http_5xx', 'dns_error', 'tcp_error',
    'tls_error', 'http_error', 'other_error'."""
    cats = Counter()
    for r in rows:
        err = _g(r, "error")
        status = _g(r, "http_status")
        if err:
            if err.startswith("dns:"):
                cats["dns_error"] += 1
            elif err.startswith("tcp:"):
                cats["tcp_error"] += 1
            elif err.startswith("tls:"):
                cats["tls_error"] += 1
            elif err.startswith("http:"):
                cats["http_error"] += 1
            else:
                cats["other_error"] += 1
        elif status is not None and status >= 500:
            cats["http_5xx"] += 1
        elif status is not None and status >= 400:
            cats["http_4xx"] += 1
        else:
            cats["ok"] += 1
    return dict(cats)


def trend(current_values: list[float | None], past_values: list[float | None]) -> dict:
    """Compare current period avg to past period avg.

    Returns {current, past, delta_pct, direction} where direction is
    'up' / 'down' / 'flat'."""
    cur = [v for v in current_values if v is not None]
    past = [v for v in past_values if v is not None]
    if not cur or not past:
        return {"current": None, "past": None, "delta_pct": None, "direction": "unknown"}
    cur_avg = sum(cur) / len(cur)
    past_avg = sum(past) / len(past)
    if past_avg == 0:
        return {"current": cur_avg, "past": past_avg, "delta_pct": None, "direction": "unknown"}
    delta_pct = (cur_avg - past_avg) / past_avg * 100
    direction = "up" if delta_pct > 5 else "down" if delta_pct < -5 else "flat"
    return {"current": cur_avg, "past": past_avg, "delta_pct": delta_pct, "direction": direction}


def detect_burst(rows: list, n: int = 5, window_s: int = 60) -> dict | None:
    """Detect the most recent burst of N errors within W seconds.

    Returns {count, first_at, last_at, window_s} of the worst burst, or None."""
    err_rows = [r for r in rows if _g(r, "error") or (_g(r, "http_status") and _g(r, "http_status") >= 400)]
    if len(err_rows) < n:
        return None
    err_rows = sorted(err_rows, key=lambda r: _g(r, "started_at"))
    timestamps = [(_ts(r), r) for r in err_rows]
    timestamps = [(t, r) for t, r in timestamps if t]
    if len(timestamps) < n:
        return None
    # Sliding window: find any contiguous slice of size >= n within window_s
    best = None
    for i in range(len(timestamps)):
        for j in range(i + n - 1, len(timestamps)):
            span = (timestamps[j][0] - timestamps[i][0]).total_seconds()
            if span > window_s:
                break
            count = j - i + 1
            if best is None or count > best["count"]:
                best = {
                    "count": count,
                    "first_at": timestamps[i][0].isoformat(timespec="seconds"),
                    "last_at": timestamps[j][0].isoformat(timespec="seconds"),
                    "window_s": int(span),
                }
    return best


def mttr(rows: list, error_threshold_pct: float = 20.0, recovery_threshold_pct: float = 5.0,
         window: int = 10) -> dict | None:
    """Mean time to recovery.

    Walks the timeseries with a sliding window. A 'degraded' state begins when
    error rate in the last `window` runs >= error_threshold_pct, and ends when
    it drops back below recovery_threshold_pct. Returns avg recovery duration
    in seconds, plus event count."""
    if len(rows) < window:
        return None
    sorted_rows = sorted(rows, key=lambda r: _g(r, "started_at"))
    errs = [1 if (_g(r, "error") or (_g(r, "http_status") and _g(r, "http_status") >= 400)) else 0
             for r in sorted_rows]
    durations: list[float] = []
    degraded_since: datetime | None = None
    for i in range(window - 1, len(sorted_rows)):
        slice_ = errs[i - window + 1: i + 1]
        rate = 100 * sum(slice_) / window
        ts = _ts(sorted_rows[i])
        if ts is None:
            continue
        if degraded_since is None and rate >= error_threshold_pct:
            degraded_since = ts
        elif degraded_since is not None and rate <= recovery_threshold_pct:
            durations.append((ts - degraded_since).total_seconds())
            degraded_since = None
    if not durations:
        return {"events": 0, "ongoing": degraded_since is not None}
    return {
        "events": len(durations),
        "avg_recovery_s": sum(durations) / len(durations),
        "max_recovery_s": max(durations),
        "ongoing": degraded_since is not None,
    }


def heatmap_day_hour(rows: list, metric: str = "duration_ms") -> dict:
    """Build a 7×24 matrix of averages (day-of-week × hour).

    Returns {data: [[avg_or_null]*24]*7, count: [[n]*24]*7,
             days: ['Mon', 'Tue', ...], hours: [0..23]}.
    Useful for spotting time-of-day patterns ('it's slow at 14h every day')."""
    days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    buckets = [[[] for _ in range(24)] for _ in range(7)]
    for r in rows:
        t = _ts(r)
        if t is None:
            continue
        v = _g(r, metric)
        if v is None:
            continue
        buckets[t.weekday()][t.hour].append(v)
    avg = [
        [(sum(vals) / len(vals)) if vals else None for vals in row]
        for row in buckets
    ]
    count = [[len(vals) for vals in row] for row in buckets]
    return {"data": avg, "count": count, "days": days, "hours": list(range(24))}


def hop_count_changes(mtr_rows: list) -> dict:
    """Detect route changes (hop count delta between consecutive runs).

    `mtr_rows` is a list of dicts with keys `id`, `started_at`, `hops_count`.
    Returns {changes: [{at, from, to}], total_changes: int, current_hops: int}."""
    sorted_rows = sorted(mtr_rows, key=lambda r: _g(r, "started_at"))
    changes = []
    prev = None
    for r in sorted_rows:
        hc = _g(r, "hops_count")
        if hc is None:
            continue
        if prev is not None and prev != hc:
            changes.append({
                "at": _g(r, "started_at"),
                "from": prev,
                "to": hc,
            })
        prev = hc
    return {
        "changes": changes[-20:],   # most recent 20
        "total_changes": len(changes),
        "current_hops": prev,
    }
