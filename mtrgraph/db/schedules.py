"""Schedules CRUD."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def list_schedules(conn) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM schedules ORDER BY id").fetchall()


def get_schedule(conn, sid: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM schedules WHERE id=?", (sid,)).fetchone()


def insert_schedule(conn, *, name, kind, config, schedule_mode,
                    interval_s=None, min_interval_s=None, max_interval_s=None,
                    enabled=1, webhook_url=None) -> int:
    cur = conn.execute(
        """INSERT INTO schedules(name, kind, config, schedule_mode,
                                  interval_s, min_interval_s, max_interval_s,
                                  enabled, created_at, webhook_url)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            name, kind, config, schedule_mode,
            interval_s, min_interval_s, max_interval_s,
            1 if enabled else 0,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            webhook_url,
        ),
    )
    return cur.lastrowid


def update_schedule(conn, sid: int, **fields) -> None:
    """Update arbitrary fields by keyword."""
    if not fields:
        return
    keys = ", ".join(f"{k}=?" for k in fields)
    conn.execute(f"UPDATE schedules SET {keys} WHERE id=?", (*fields.values(), sid))


def delete_schedule(conn, sid: int) -> None:
    conn.execute("DELETE FROM schedules WHERE id=?", (sid,))


def due_schedules(conn, now_iso: str) -> list[sqlite3.Row]:
    return conn.execute(
        """SELECT * FROM schedules
           WHERE enabled=1 AND (next_run_at IS NULL OR next_run_at <= ?)
           ORDER BY id""",
        (now_iso,),
    ).fetchall()
