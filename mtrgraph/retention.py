"""DB retention — delete old rows + reclaim space.

Run periodically (CLI or scheduler thread) to keep the SQLite file from growing
indefinitely. Default retention is 30 days, configurable per table.

Tables managed:
- runs / hops (mtr)            — ON DELETE CASCADE handles hops
- http_runs / http_samples     — ON DELETE CASCADE handles samples
- s3_runs
- tcp_samples
- s3_tracked_objects (deleted) — only the ones already deleted_at IS NOT NULL

Tables NOT managed (no automatic cleanup):
- schedules                    — user-managed, never expires automatically
- s3_tracked_objects (alive)   — represent objects that still exist on remote
                                  buckets; never delete the tracking row while
                                  the object is there (would leak the object).
"""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import db


@dataclass
class RetentionStats:
    runs_deleted: int = 0
    http_runs_deleted: int = 0
    s3_runs_deleted: int = 0
    tcp_samples_deleted: int = 0
    tracked_purged: int = 0
    duration_s: float = 0
    vacuumed: bool = False
    bytes_freed: int = 0
    bytes_after: int = 0


def _delete_older_than(conn, table: str, column: str, cutoff_iso: str) -> int:
    cur = conn.execute(f"DELETE FROM {table} WHERE {column} < ?", (cutoff_iso,))
    return cur.rowcount


def apply_retention(
    db_path: Path,
    max_age_days: int = 30,
    vacuum: bool = True,
    per_table_override: dict[str, int] | None = None,
) -> RetentionStats:
    """Delete rows older than `max_age_days` (override per table if provided)."""
    overrides = per_table_override or {}
    t0 = time.monotonic()
    stats = RetentionStats()

    bytes_before = db_path.stat().st_size if db_path.exists() else 0

    def cutoff(table_key: str) -> str:
        n = overrides.get(table_key, max_age_days)
        return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat(timespec="seconds")

    conn = db._connect(db_path)
    try:
        # mtr: cascade deletes hops
        stats.runs_deleted = _delete_older_than(
            conn, "runs", "started_at", cutoff("runs"),
        )
        # http: cascade deletes http_samples
        stats.http_runs_deleted = _delete_older_than(
            conn, "http_runs", "started_at", cutoff("http_runs"),
        )
        stats.s3_runs_deleted = _delete_older_than(
            conn, "s3_runs", "started_at", cutoff("s3_runs"),
        )
        stats.tcp_samples_deleted = _delete_older_than(
            conn, "tcp_samples", "started_at", cutoff("tcp_samples"),
        )
        # tracked objects that we already deleted from remote: drop their row
        cur = conn.execute(
            "DELETE FROM s3_tracked_objects WHERE deleted_at IS NOT NULL AND deleted_at < ?",
            (cutoff("s3_tracked_objects"),),
        )
        stats.tracked_purged = cur.rowcount
        conn.commit()

        if vacuum:
            # VACUUM cannot run inside a transaction
            conn.isolation_level = None
            conn.execute("VACUUM")
            stats.vacuumed = True
    finally:
        conn.close()

    bytes_after = db_path.stat().st_size if db_path.exists() else 0
    stats.bytes_after = bytes_after
    stats.bytes_freed = max(0, bytes_before - bytes_after)
    stats.duration_s = time.monotonic() - t0
    return stats


def db_stats(db_path: Path) -> dict:
    """Return row counts + file size — used by doctor and the web UI."""
    out: dict = {"size_bytes": db_path.stat().st_size if db_path.exists() else 0}
    conn = db._connect(db_path)
    try:
        for table in ("runs", "hops", "http_runs", "http_samples", "s3_runs",
                       "tcp_samples", "s3_tracked_objects", "schedules"):
            try:
                n = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                out[f"rows_{table}"] = n
            except sqlite3.OperationalError:
                out[f"rows_{table}"] = None
        # oldest entry per main table
        for table, col in [("runs", "started_at"), ("http_runs", "started_at"),
                             ("s3_runs", "started_at"), ("tcp_samples", "started_at")]:
            try:
                r = conn.execute(f"SELECT MIN({col}), MAX({col}) FROM {table}").fetchone()
                if r and r[0]:
                    out[f"oldest_{table}"] = r[0]
                    out[f"newest_{table}"] = r[1]
            except sqlite3.OperationalError:
                pass
    finally:
        conn.close()
    return out


class RetentionTask:
    """Periodic retention runner (background thread). Sleeps `period_h` hours
    between runs. First run happens immediately at startup if `eager` is True
    (useful for first deploy to catch up)."""

    def __init__(self, db_path: Path, max_age_days: int = 30,
                 period_h: float = 24.0, log_fn=print, eager: bool = False):
        self.db_path = db_path
        self.max_age_days = max_age_days
        self.period_s = max(60.0, period_h * 3600.0)
        self.log_fn = log_fn
        self.eager = eager
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="mtrgraph-retention", daemon=True)
        self._thread.start()
        self.log_fn(f"[retention] started — max_age={self.max_age_days}d, every {self.period_s/3600:.1f}h")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        if self.eager:
            self._tick()
        while not self._stop.wait(self.period_s):
            self._tick()

    def _tick(self) -> None:
        try:
            s = apply_retention(self.db_path, max_age_days=self.max_age_days)
            self.log_fn(
                f"[retention] runs={s.runs_deleted} http={s.http_runs_deleted} "
                f"s3={s.s3_runs_deleted} tcp={s.tcp_samples_deleted} "
                f"tracked={s.tracked_purged} freed={s.bytes_freed/1024/1024:.1f}MB "
                f"size={s.bytes_after/1024/1024:.1f}MB in {s.duration_s:.2f}s"
            )
        except Exception as e:
            self.log_fn(f"[retention] error: {e}")
