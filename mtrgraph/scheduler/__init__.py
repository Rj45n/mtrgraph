"""Scheduler — background thread that ticks every second and dispatches
per-kind executors (s3, http, mtr, tcp).

Public surface (compat with existing imports):
- ``Scheduler`` (class)
- ``run_now(db_path, sid)`` — one-shot
- ``trigger_auto_mtr(target_ip, db_path, ...)`` — non-blocking auto-MTR

Internal layout:
- ``_common.py``         — now_utc, iso, next_interval helpers
- ``webhooks.py``        — degraded detection + POST to Slack-compat URLs
- ``executors/s3.py``    — execute(config), random_ops, status_with_compare
- ``executors/http.py``  — execute(config)
- ``executors/mtr.py``   — execute(config), trigger_auto_mtr
- ``executors/tcp.py``   — execute(config)
"""
from __future__ import annotations

import json
import threading
from datetime import timedelta
from pathlib import Path

from . import _common
from . import webhooks
from .executors import http as exec_http
from .executors import mtr as exec_mtr
from .executors import s3 as exec_s3
from .executors import tcp as exec_tcp
from .. import db

# Re-export for legacy callers ``from mtrgraph.scheduler import trigger_auto_mtr``
trigger_auto_mtr = exec_mtr.trigger_auto_mtr


def _run_schedule(row, db_path: Path, log_fn) -> None:
    """Dispatcher: runs the matching executor + updates the schedule row +
    fires webhook if status is degraded. Catches all exceptions to avoid
    killing the scheduler thread."""
    sid = row["id"]
    name = row["name"]
    kind = row["kind"]
    try:
        config = json.loads(row["config"])
    except json.JSONDecodeError as e:
        log_fn(f"[sched #{sid} {name}] config JSON invalide: {e}")
        with db.session(db_path) as conn:
            db.update_schedule(
                conn, sid,
                last_status=f"err:bad-config: {e}",
                last_run_at=_common.iso(_common.now_utc()),
                next_run_at=_common.iso(_common.now_utc() + timedelta(seconds=_common.next_interval(row))),
            )
        return

    try:
        if kind == "s3":
            result, status = exec_s3.execute(config, db_path=db_path, schedule_id=sid)
            with db.session(db_path) as conn:
                run_id = db.insert_s3_run(conn, result)
            # auto_mtr default True: even legacy schedules without the field
            # benefit from RTT correlation in the dashboard.
            if config.get("auto_mtr", True) and result.resolved_ip:
                exec_mtr.trigger_auto_mtr(result.resolved_ip, db_path, log_fn)
            status = exec_s3.status_with_compare(result, db_path, config, status)
            _finalize(db_path, sid, row, run_id, status)
            log_fn(f"[sched #{sid} {name}] s3:{config.get('operation')} → {status} (run #{run_id})")
            webhooks.maybe_notify(row, status, run_id, log_fn)

        elif kind == "tcp":
            sample_id, status = exec_tcp.execute(config, db_path)
            _finalize(db_path, sid, row, sample_id, status)
            log_fn(f"[sched #{sid} {name}] tcp → {status} (sample #{sample_id})")
            webhooks.maybe_notify(row, status, sample_id, log_fn)

        elif kind == "mtr":
            run_id, status = exec_mtr.execute(config, db_path)
            _finalize(db_path, sid, row, run_id, status)
            log_fn(f"[sched #{sid} {name}] mtr → {status} (run #{run_id})")
            webhooks.maybe_notify(row, status, run_id, log_fn)

        elif kind == "http":
            samples, summary, errors, resolved_ip = exec_http.execute(config)
            tls_meta = db.tls_meta_from_samples(samples)
            with db.session(db_path) as conn:
                run_id = db.insert_http_run(
                    conn, config["url"], config.get("method", "HEAD"), len(samples),
                    config.get("label", "scheduled"), resolved_ip, summary, errors,
                    tls_meta=tls_meta,
                )
                db.insert_http_samples(conn, run_id, samples)
                db.finalize_http_run(conn, run_id)
            # auto_mtr default True (parity with S3): correlates with dashboard RTT chart
            if config.get("auto_mtr", True) and resolved_ip:
                exec_mtr.trigger_auto_mtr(resolved_ip, db_path, log_fn)
            status = f"err:{errors}/{len(samples)}" if errors else f"ok:{summary}"
            _finalize(db_path, sid, row, run_id, status)
            log_fn(f"[sched #{sid} {name}] http → {status} (run #{run_id})")
            webhooks.maybe_notify(row, status, run_id, log_fn)

        else:
            raise ValueError(f"unknown kind: {kind!r}")
    except Exception as e:
        log_fn(f"[sched #{sid} {name}] EXCEPTION: {e}")
        with db.session(db_path) as conn:
            db.update_schedule(
                conn, sid,
                last_run_at=_common.iso(_common.now_utc()),
                next_run_at=_common.iso(_common.now_utc() + timedelta(seconds=_common.next_interval(row))),
                last_status=f"err:{e}",
            )


def _finalize(db_path: Path, sid: int, row, run_id: int | None, status: str) -> None:
    with db.session(db_path) as conn:
        db.update_schedule(
            conn, sid,
            last_run_at=_common.iso(_common.now_utc()),
            next_run_at=_common.iso(_common.now_utc() + timedelta(seconds=_common.next_interval(row))),
            last_run_id=run_id,
            last_status=status,
        )


def run_now(db_path: Path, sid: int, log_fn=print) -> None:
    """Execute the schedule immediately, outside the normal tick."""
    with db.session(db_path) as conn:
        row = db.get_schedule(conn, sid)
    if not row:
        raise ValueError(f"schedule #{sid} not found")
    _run_schedule(row, db_path, log_fn)


class Scheduler:
    """Background scheduler — ticks every second and runs due schedules."""

    def __init__(self, db_path: Path, log_fn=print, tick_s: float = 1.0):
        self.db_path = db_path
        self.tick_s = tick_s
        self.log_fn = log_fn
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._loop, name="mtrgraph-scheduler", daemon=True)
        self._thread.start()
        self.log_fn("[scheduler] started")

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        try:
            db.init_db(self.db_path)
        except Exception as e:
            self.log_fn(f"[scheduler] init_db failed: {e}")
            return

        while not self._stop.is_set():
            try:
                now_iso = _common.iso(_common.now_utc())
                with db.session(self.db_path) as conn:
                    due = db.due_schedules(conn, now_iso)
                for row in due:
                    if self._stop.is_set():
                        break
                    _run_schedule(row, self.db_path, self.log_fn)
            except Exception as e:
                self.log_fn(f"[scheduler] tick error: {e}")
            self._stop.wait(self.tick_s)
