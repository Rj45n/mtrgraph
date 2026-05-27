"""MTR runs + hops + baseline + RTT lookup."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone


def insert_run(
    conn: sqlite3.Connection,
    target: str,
    cycles: int,
    label: str | None,
    src: str | None,
    protocol: str = "icmp",
    dst_port: int | None = None,
) -> int:
    cur = conn.execute(
        """INSERT INTO runs(target, protocol, dst_port, label, cycles, started_at, src)
           VALUES(?,?,?,?,?,?,?)""",
        (target, protocol, dst_port, label, cycles,
         datetime.now(timezone.utc).isoformat(timespec="seconds"), src),
    )
    return cur.lastrowid


def finalize_run(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute(
        "UPDATE runs SET finished_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), run_id),
    )


def insert_hops(conn: sqlite3.Connection, run_id: int, hops: list[dict]) -> None:
    conn.executemany(
        """INSERT OR REPLACE INTO hops
           (run_id, hop_index, host, loss_pct, sent, last_ms, avg_ms, best_ms, worst_ms, stddev_ms)
           VALUES(?,?,?,?,?,?,?,?,?,?)""",
        [
            (run_id, h["hop_index"], h["host"], h["loss_pct"], h["sent"],
             h["last_ms"], h["avg_ms"], h["best_ms"], h["worst_ms"], h["stddev_ms"])
            for h in hops
        ],
    )


def list_runs(conn: sqlite3.Connection, target: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
    if target:
        return conn.execute(
            "SELECT * FROM runs WHERE target=? ORDER BY started_at DESC LIMIT ?",
            (target, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM runs ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()


def get_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()


def get_hops(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM hops WHERE run_id=? ORDER BY hop_index", (run_id,)
    ).fetchall()


def latest_run_id(
    conn: sqlite3.Connection,
    target: str,
    protocol: str = "icmp",
    dst_port: int | None = None,
) -> int | None:
    row = conn.execute(
        """SELECT id FROM runs
           WHERE target=? AND protocol=? AND (dst_port IS ? OR dst_port=?)
           ORDER BY started_at DESC LIMIT 1""",
        (target, protocol, dst_port, dst_port),
    ).fetchone()
    return row["id"] if row else None


def baseline_hops(
    conn: sqlite3.Connection,
    target: str,
    protocol: str = "icmp",
    dst_port: int | None = None,
    last_n: int = 10,
) -> dict[int, dict]:
    """Median avg_ms and loss_pct per hop over the last N runs for (target, protocol, dst_port)."""
    rows = conn.execute(
        """SELECT h.hop_index, h.host, h.avg_ms, h.loss_pct
           FROM hops h
           JOIN (
               SELECT id FROM runs
               WHERE target=? AND protocol=? AND (dst_port IS ? OR dst_port=?)
               ORDER BY started_at DESC LIMIT ?
           ) r ON h.run_id = r.id""",
        (target, protocol, dst_port, dst_port, last_n),
    ).fetchall()
    buckets: dict[int, list[sqlite3.Row]] = {}
    for r in rows:
        buckets.setdefault(r["hop_index"], []).append(r)

    def median(values: list[float]) -> float | None:
        v = sorted(x for x in values if x is not None)
        if not v:
            return None
        n = len(v)
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2

    return {
        idx: {
            "host": rows_[-1]["host"],
            "avg_ms": median([r["avg_ms"] for r in rows_]),
            "loss_pct": median([r["loss_pct"] for r in rows_]),
            "samples": len(rows_),
        }
        for idx, rows_ in buckets.items()
    }


def delete_run(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute("DELETE FROM runs WHERE id=?", (run_id,))


def latest_mtr_rtt_for_ip(conn, ip: str, since_minutes: int = 60) -> float | None:
    """Return average RTT to `ip` from the most recent MTR run within `since_minutes`.
    Looks at the last hop of any matching run (target=ip OR ip seen as a hop)."""
    if not ip:
        return None
    cutoff = (
        datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
    ).isoformat(timespec="seconds")
    rows = conn.execute(
        """SELECT r.id FROM runs r
           WHERE (r.target=? OR EXISTS (
                  SELECT 1 FROM hops h WHERE h.run_id=r.id AND h.host=?))
             AND r.started_at >= ?
           ORDER BY r.started_at DESC LIMIT 5""",
        (ip, ip, cutoff),
    ).fetchall()
    for r in rows:
        row = conn.execute(
            "SELECT avg_ms FROM hops WHERE run_id=? ORDER BY hop_index DESC LIMIT 1",
            (r["id"],),
        ).fetchone()
        if row and row["avg_ms"] is not None and row["avg_ms"] > 0:
            return float(row["avg_ms"])
    return None
