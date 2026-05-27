"""Database layer — SQLite schema, session management, and per-domain helpers.

This module re-exports all helpers from the per-domain submodules so that legacy
imports like ``from mtrgraph import db; db.insert_run(...)`` keep working
unchanged.

Schema lives here (single source of truth, applied by ``init_db``).
Per-domain helpers live in:

- ``db/mtr.py``       — runs, hops, baselines, RTT lookup
- ``db/http_runs.py`` — http_runs, http_samples, http_baseline
- ``db/s3.py``        — s3_runs, s3_baseline
- ``db/tracked.py``   — s3_tracked_objects
- ``db/tcp.py``       — tcp_samples
- ``db/schedules.py`` — schedules CRUD
- ``db/utils.py``     — pure helpers (proto_label, etc.)
"""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_DB = Path.home() / ".local" / "share" / "mtrgraph" / "mtrgraph.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    target TEXT NOT NULL,
    protocol TEXT NOT NULL DEFAULT 'icmp',
    dst_port INTEGER,
    label TEXT,
    cycles INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    src TEXT
);
CREATE INDEX IF NOT EXISTS idx_runs_target_proto_started
    ON runs(target, protocol, dst_port, started_at);

CREATE TABLE IF NOT EXISTS hops (
    run_id INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    hop_index INTEGER NOT NULL,
    host TEXT,
    loss_pct REAL,
    sent INTEGER,
    last_ms REAL,
    avg_ms REAL,
    best_ms REAL,
    worst_ms REAL,
    stddev_ms REAL,
    PRIMARY KEY (run_id, hop_index)
);
CREATE INDEX IF NOT EXISTS idx_hops_run ON hops(run_id);

CREATE TABLE IF NOT EXISTS http_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    method TEXT NOT NULL DEFAULT 'HEAD',
    label TEXT,
    samples INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    resolved_ip TEXT,
    status_summary TEXT,
    errors INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_http_runs_url_started ON http_runs(url, started_at);

CREATE TABLE IF NOT EXISTS schedules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    kind TEXT NOT NULL,
    config TEXT NOT NULL,
    schedule_mode TEXT NOT NULL,
    interval_s INTEGER,
    min_interval_s INTEGER,
    max_interval_s INTEGER,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    last_run_at TEXT,
    next_run_at TEXT,
    last_run_id INTEGER,
    last_status TEXT,
    webhook_url TEXT
);
CREATE INDEX IF NOT EXISTS idx_schedules_enabled_next ON schedules(enabled, next_run_at);

CREATE TABLE IF NOT EXISTS tcp_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    duration_s REAL NOT NULL,
    retrans_pct REAL,
    retrans_per_s REAL,
    out_per_s REAL,
    in_per_s REAL,
    in_errs_delta INTEGER,
    estab_resets_delta INTEGER,
    active_opens_delta INTEGER,
    label TEXT
);
CREATE INDEX IF NOT EXISTS idx_tcp_samples_started ON tcp_samples(started_at);

CREATE TABLE IF NOT EXISTS s3_tracked_objects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    schedule_id INTEGER NOT NULL,
    endpoint TEXT NOT NULL,
    bucket TEXT NOT NULL,
    key TEXT NOT NULL,
    size_bytes INTEGER,
    created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_s3_tracked_sched_active
    ON s3_tracked_objects(schedule_id, deleted_at);

CREATE TABLE IF NOT EXISTS s3_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    bucket TEXT,
    key TEXT,
    operation TEXT NOT NULL,
    label TEXT,
    http_status INTEGER,
    duration_ms REAL,
    dns_ms REAL,
    tcp_ms REAL,
    tls_ms REAL,
    ttfb_ms REAL,
    bytes_transferred INTEGER DEFAULT 0,
    resolved_ip TEXT,
    response_summary TEXT,
    error TEXT
);
CREATE INDEX IF NOT EXISTS idx_s3_runs_endpoint_started ON s3_runs(endpoint, started_at);
CREATE INDEX IF NOT EXISTS idx_s3_runs_op_started ON s3_runs(operation, started_at);

CREATE TABLE IF NOT EXISTS http_samples (
    run_id INTEGER NOT NULL REFERENCES http_runs(id) ON DELETE CASCADE,
    sample_idx INTEGER NOT NULL,
    dns_ms REAL,
    tcp_ms REAL,
    tls_ms REAL,
    ttfb_ms REAL,
    total_ms REAL,
    status INTEGER,
    error TEXT,
    PRIMARY KEY (run_id, sample_idx)
);
CREATE INDEX IF NOT EXISTS idx_http_samples_run ON http_samples(run_id);
"""


def _connect(path: Path) -> sqlite3.Connection:
    """Open a SQLite connection with WAL mode and sensible PRAGMAs."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA temp_store = MEMORY")
    return conn


def _migrate_add_column(conn, table: str, column: str, sql_type: str) -> None:
    """Idempotent ALTER TABLE ADD COLUMN — silent if column already exists."""
    cols = {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    if column not in cols:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {sql_type}")


def init_db(path: Path = DEFAULT_DB) -> None:
    with _connect(path) as conn:
        conn.executescript(SCHEMA)
        _migrate_add_column(conn, "schedules", "webhook_url", "TEXT")
        # TLS info captured on the first sample of each http_run
        _migrate_add_column(conn, "http_runs", "tls_version", "TEXT")
        _migrate_add_column(conn, "http_runs", "tls_cipher", "TEXT")
        _migrate_add_column(conn, "http_runs", "cert_subject_cn", "TEXT")
        _migrate_add_column(conn, "http_runs", "cert_issuer_cn", "TEXT")
        _migrate_add_column(conn, "http_runs", "cert_not_after", "TEXT")
        _migrate_add_column(conn, "http_runs", "cert_san_count", "INTEGER")


@contextmanager
def session(path: Path = DEFAULT_DB):
    """Context manager: initialise schema, open connection, commit on exit."""
    init_db(path)
    conn = _connect(path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ─── Re-exports from per-domain modules ──────────────────────────────────
# Keep the legacy flat namespace so existing callers don't break.

from .utils import proto_label  # noqa: E402

from .mtr import (  # noqa: E402
    insert_run, finalize_run, insert_hops, list_runs, get_run, get_hops,
    latest_run_id, delete_run, baseline_hops, latest_mtr_rtt_for_ip,
    list_targets, target_series, hop_matrix,
)
from .http_runs import (  # noqa: E402
    insert_http_run, finalize_http_run, insert_http_samples,
    list_http_runs, get_http_run, get_http_samples, delete_http_run,
    http_baseline, tls_meta_from_samples,
)
from .s3 import (  # noqa: E402
    insert_s3_run, list_s3_runs, get_s3_run, delete_s3_run, s3_baseline,
)
from .tracked import (  # noqa: E402
    track_s3_object, list_tracked_alive, count_tracked_alive,
    mark_tracked_deleted, get_tracked_by_key, list_tracked_all,
)
from .tcp import insert_tcp_sample, list_tcp_samples  # noqa: E402
from .schedules import (  # noqa: E402
    list_schedules, get_schedule, insert_schedule, update_schedule,
    delete_schedule, due_schedules,
)
