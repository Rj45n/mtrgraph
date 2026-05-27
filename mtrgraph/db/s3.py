"""S3 runs + baseline (no auth, just persistence of S3 op results)."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def insert_s3_run(conn: sqlite3.Connection, result) -> int:
    """Insert an S3Result and return the row id."""
    cur = conn.execute(
        """INSERT INTO s3_runs(
            started_at, endpoint, bucket, key, operation, label,
            http_status, duration_ms, dns_ms, tcp_ms, tls_ms, ttfb_ms,
            bytes_transferred, resolved_ip, response_summary, error
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            result.endpoint, result.bucket, result.key, result.operation, result.label,
            result.http_status, result.duration_ms,
            result.dns_ms, result.tcp_ms, result.tls_ms, result.ttfb_ms,
            result.bytes_transferred, result.resolved_ip,
            result.response_summary, result.error,
        ),
    )
    return cur.lastrowid


def list_s3_runs(conn, endpoint: str | None = None,
                  operation: str | None = None, limit: int = 100):
    sql = "SELECT * FROM s3_runs WHERE 1=1"
    params: list = []
    if endpoint:
        sql += " AND endpoint=?"
        params.append(endpoint)
    if operation:
        sql += " AND operation=?"
        params.append(operation)
    sql += " ORDER BY started_at DESC LIMIT ?"
    params.append(limit)
    return conn.execute(sql, params).fetchall()


def get_s3_run(conn, run_id: int):
    return conn.execute("SELECT * FROM s3_runs WHERE id=?", (run_id,)).fetchone()


def delete_s3_run(conn, run_id: int) -> None:
    conn.execute("DELETE FROM s3_runs WHERE id=?", (run_id,))


def s3_baseline(conn, endpoint: str, operation: str, bucket: str | None,
                last_n: int = 10) -> dict:
    """Median per-stage on last N successful s3_runs matching (endpoint, op, bucket)."""
    rows = conn.execute(
        """SELECT dns_ms, tcp_ms, tls_ms, ttfb_ms, duration_ms FROM s3_runs
           WHERE endpoint=? AND operation=? AND (bucket IS ? OR bucket=?)
             AND http_status >= 200 AND http_status < 400
           ORDER BY started_at DESC LIMIT ?""",
        (endpoint, operation, bucket, bucket, last_n),
    ).fetchall()
    if not rows:
        return {}

    def median(values):
        v = sorted(x for x in values if x is not None)
        if not v:
            return None
        n = len(v)
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2

    return {
        "dns":   {"avg_ms": median([r["dns_ms"]      for r in rows])},
        "tcp":   {"avg_ms": median([r["tcp_ms"]      for r in rows])},
        "tls":   {"avg_ms": median([r["tls_ms"]      for r in rows])},
        "ttfb":  {"avg_ms": median([r["ttfb_ms"]     for r in rows])},
        "total": {"avg_ms": median([r["duration_ms"] for r in rows])},
        "n_runs": len(rows),
    }
