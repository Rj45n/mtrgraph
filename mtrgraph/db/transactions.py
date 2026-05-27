"""Synthetic HTTP transactions storage."""
from __future__ import annotations

import json
from datetime import datetime, timezone


def insert_tx_run(conn, *, name: str, label: str | None, total_ms: float,
                  steps_count: int, success_count: int, error_count: int,
                  definition: list) -> int:
    cur = conn.execute(
        """INSERT INTO http_tx_runs(name, label, started_at, finished_at, total_ms,
                                     steps_count, success_count, error_count, definition_json)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        (
            name, label,
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            datetime.now(timezone.utc).isoformat(timespec="seconds"),
            total_ms, steps_count, success_count, error_count,
            json.dumps(definition),
        ),
    )
    return cur.lastrowid


def insert_tx_steps(conn, run_id: int, step_results: list) -> None:
    """`step_results` is a list of StepResult-like dicts."""
    conn.executemany(
        """INSERT INTO http_tx_steps
           (run_id, step_idx, method, url, status, ok,
            dns_ms, tcp_ms, tls_ms, ttfb_ms, total_ms, error, cookies_set)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            (run_id, r["step_idx"], r["method"], r["url"], r["status"],
             1 if r["ok"] else 0,
             r["dns_ms"], r["tcp_ms"], r["tls_ms"], r["ttfb_ms"], r["total_ms"],
             r["error"], ",".join(r.get("cookies_received") or []) or None)
            for r in step_results
        ],
    )


def list_tx_runs(conn, name: str | None = None, limit: int = 50):
    if name:
        return conn.execute(
            "SELECT * FROM http_tx_runs WHERE name=? ORDER BY started_at DESC LIMIT ?",
            (name, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM http_tx_runs ORDER BY started_at DESC LIMIT ?", (limit,),
    ).fetchall()


def get_tx_run(conn, run_id: int):
    return conn.execute("SELECT * FROM http_tx_runs WHERE id=?", (run_id,)).fetchone()


def get_tx_steps(conn, run_id: int):
    return conn.execute(
        "SELECT * FROM http_tx_steps WHERE run_id=? ORDER BY step_idx", (run_id,),
    ).fetchall()
