"""TCP stats samples (from /proc/net/snmp deltas)."""
from __future__ import annotations

from datetime import datetime, timezone


def insert_tcp_sample(conn, *, duration_s: float, retrans_pct: float,
                       retrans_per_s: float, out_per_s: float, in_per_s: float,
                       in_errs_delta: int, estab_resets_delta: int,
                       active_opens_delta: int, label: str | None = None) -> int:
    cur = conn.execute(
        """INSERT INTO tcp_samples(started_at, duration_s, retrans_pct, retrans_per_s,
                                    out_per_s, in_per_s, in_errs_delta,
                                    estab_resets_delta, active_opens_delta, label)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"),
         duration_s, retrans_pct, retrans_per_s, out_per_s, in_per_s,
         in_errs_delta, estab_resets_delta, active_opens_delta, label),
    )
    return cur.lastrowid


def list_tcp_samples(conn, limit: int = 200,
                      start_time: str | None = None,
                      end_time: str | None = None) -> list:
    sql = "SELECT * FROM tcp_samples WHERE 1=1"
    params: list = []
    if start_time:
        sql += " AND started_at >= ?"; params.append(start_time)
    if end_time:
        sql += " AND started_at <= ?"; params.append(end_time)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()
