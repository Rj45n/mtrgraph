"""HTTP probe runs + samples + baseline."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone


def insert_http_run(
    conn: sqlite3.Connection,
    url: str,
    method: str,
    samples: int,
    label: str | None,
    resolved_ip: str | None,
    status_summary: str | None,
    errors: int,
    tls_meta: dict | None = None,
) -> int:
    """Insert an http_run. `tls_meta` (optional) carries TLS metadata captured
    from the first successful sample (tls_version, tls_cipher, cert_*)."""
    m = tls_meta or {}
    cur = conn.execute(
        """INSERT INTO http_runs(url, method, label, samples, started_at, resolved_ip,
                                 status_summary, errors,
                                 tls_version, tls_cipher, cert_subject_cn,
                                 cert_issuer_cn, cert_not_after, cert_san_count)
           VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (url, method, label, samples,
         datetime.now(timezone.utc).isoformat(timespec="seconds"),
         resolved_ip, status_summary, errors,
         m.get("tls_version"), m.get("tls_cipher"), m.get("cert_subject_cn"),
         m.get("cert_issuer_cn"), m.get("cert_not_after"), m.get("cert_san_count")),
    )
    return cur.lastrowid


def tls_meta_from_samples(samples: list) -> dict:
    """Pull TLS metadata from the first sample that has tls_version set."""
    for s in samples:
        v = getattr(s, "tls_version", None)
        if v:
            return {
                "tls_version": v,
                "tls_cipher": getattr(s, "tls_cipher", None),
                "cert_subject_cn": getattr(s, "cert_subject_cn", None),
                "cert_issuer_cn": getattr(s, "cert_issuer_cn", None),
                "cert_not_after": getattr(s, "cert_not_after", None),
                "cert_san_count": getattr(s, "cert_san_count", None),
            }
    return {}


def finalize_http_run(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute(
        "UPDATE http_runs SET finished_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(timespec="seconds"), run_id),
    )


def insert_http_samples(conn: sqlite3.Connection, run_id: int, samples: list) -> None:
    conn.executemany(
        """INSERT INTO http_samples
           (run_id, sample_idx, dns_ms, tcp_ms, tls_ms, ttfb_ms, total_ms, status, error)
           VALUES(?,?,?,?,?,?,?,?,?)""",
        [
            (run_id, s.sample_idx, s.dns_ms, s.tcp_ms, s.tls_ms,
             s.ttfb_ms, s.total_ms, s.status, s.error)
            for s in samples
        ],
    )


def list_http_runs(conn: sqlite3.Connection, url: str | None = None, limit: int = 50) -> list[sqlite3.Row]:
    if url:
        return conn.execute(
            "SELECT * FROM http_runs WHERE url=? ORDER BY started_at DESC LIMIT ?",
            (url, limit),
        ).fetchall()
    return conn.execute(
        "SELECT * FROM http_runs ORDER BY started_at DESC LIMIT ?", (limit,)
    ).fetchall()


def get_http_run(conn: sqlite3.Connection, run_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM http_runs WHERE id=?", (run_id,)).fetchone()


def get_http_samples(conn: sqlite3.Connection, run_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM http_samples WHERE run_id=? ORDER BY sample_idx", (run_id,)
    ).fetchall()


def delete_http_run(conn: sqlite3.Connection, run_id: int) -> None:
    conn.execute("DELETE FROM http_runs WHERE id=?", (run_id,))


def http_baseline(conn: sqlite3.Connection, url: str, last_n: int = 10) -> dict:
    """Median per-stage avg over the last N http_runs of URL."""
    runs = conn.execute(
        "SELECT id FROM http_runs WHERE url=? ORDER BY started_at DESC LIMIT ?",
        (url, last_n),
    ).fetchall()
    if not runs:
        return {}
    per_stage: dict[str, list[float]] = {s: [] for s in ("dns", "tcp", "tls", "ttfb", "total")}
    for r in runs:
        samples = conn.execute(
            "SELECT dns_ms, tcp_ms, tls_ms, ttfb_ms, total_ms FROM http_samples WHERE run_id=?",
            (r["id"],),
        ).fetchall()
        for stage in per_stage:
            col = f"{stage}_ms"
            vals = [s[col] for s in samples if s[col] is not None]
            if vals:
                per_stage[stage].append(sum(vals) / len(vals))

    def median(values: list[float]) -> float | None:
        if not values:
            return None
        v = sorted(values)
        n = len(v)
        return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2

    return {
        stage: {"avg_ms": median(vals), "n_runs": len(vals)}
        for stage, vals in per_stage.items()
    }
