"""Shared helpers across scheduler + executors."""
from __future__ import annotations

import random
from datetime import datetime, timezone


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def next_interval(row) -> int:
    """Returns the delay in seconds to the next scheduled run for a given row."""
    if row["schedule_mode"] == "random":
        lo = int(row["min_interval_s"] or 30)
        hi = int(row["max_interval_s"] or max(lo + 1, 60))
        return random.randint(lo, hi)
    return int(row["interval_s"] or 60)
