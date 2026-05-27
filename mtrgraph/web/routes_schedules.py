"""Schedules CRUD + UI: /schedules, /api/schedules/*."""
from __future__ import annotations

import json as _json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .. import db, s3_client
from ..scheduler import run_now as scheduler_run_now


class ScheduleIn(BaseModel):
    name: str
    kind: str                            # 's3' | 'http' | 'mtr' | 'tcp'
    config: dict
    schedule_mode: str = "fixed"         # 'fixed' | 'random'
    interval_s: int | None = 60
    min_interval_s: int | None = None
    max_interval_s: int | None = None
    enabled: bool = True
    webhook_url: str | None = None


def _validate_schedule(s: ScheduleIn) -> None:
    if s.kind not in ("s3", "http", "mtr", "tcp"):
        raise HTTPException(400, "kind must be 's3', 'http', 'mtr' or 'tcp'")
    if s.schedule_mode not in ("fixed", "random"):
        raise HTTPException(400, "schedule_mode must be 'fixed' or 'random'")
    if s.schedule_mode == "fixed":
        if not s.interval_s or s.interval_s < 5:
            raise HTTPException(400, "interval_s must be >= 5 for fixed mode")
    else:
        if not s.min_interval_s or not s.max_interval_s:
            raise HTTPException(400, "min_interval_s and max_interval_s required for random")
        if s.min_interval_s < 5:
            raise HTTPException(400, "min_interval_s must be >= 5")
        if s.max_interval_s < s.min_interval_s:
            raise HTTPException(400, "max_interval_s must be >= min_interval_s")
    if s.kind == "s3":
        for k in ("endpoint", "bucket", "access_key", "secret_key"):
            if not s.config.get(k):
                raise HTTPException(400, f"config.{k} required for s3 schedule")
        if s.config.get("random_ops"):
            if not s.config.get("prefix"):
                raise HTTPException(400, "random_ops requires a non-empty 'prefix' (safety)")
        else:
            if not s.config.get("operation"):
                raise HTTPException(400, "config.operation required when random_ops is off")
            if s.config["operation"] not in ("list", "head", "get", "put", "delete"):
                raise HTTPException(400, "operation must be list/head/get/put/delete")
    elif s.kind == "http":
        if not s.config.get("url"):
            raise HTTPException(400, "config.url required for http schedule")
    elif s.kind == "mtr":
        has_target = s.config.get("target") or s.config.get("targets_pool")
        if not has_target:
            raise HTTPException(400, "config.target or config.targets_pool required for mtr schedule")
        proto = s.config.get("proto", "icmp")
        if proto not in ("icmp", "udp", "tcp"):
            raise HTTPException(400, "config.proto must be 'icmp', 'udp' or 'tcp'")


def create_router(db_path: Path, templates) -> APIRouter:
    router = APIRouter()

    @router.get("/schedules", response_class=HTMLResponse)
    def schedules_page(request: Request):
        return templates.TemplateResponse(request, "schedules.html", {})

    @router.get("/api/schedules")
    def api_schedules_list():
        with db.session(db_path) as conn:
            rows = db.list_schedules(conn)
        out = []
        for r in rows:
            d = dict(r)
            try:
                d["config"] = _json.loads(d["config"])
            except Exception:
                pass
            out.append(d)
        return out

    @router.post("/api/schedules")
    def api_schedule_create(s: ScheduleIn):
        _validate_schedule(s)
        with db.session(db_path) as conn:
            sid = db.insert_schedule(
                conn, name=s.name, kind=s.kind,
                config=_json.dumps(s.config),
                schedule_mode=s.schedule_mode,
                interval_s=s.interval_s,
                min_interval_s=s.min_interval_s,
                max_interval_s=s.max_interval_s,
                enabled=s.enabled,
                webhook_url=s.webhook_url or None,
            )
        return {"id": sid}

    @router.put("/api/schedules/{sid}")
    def api_schedule_update(sid: int, s: ScheduleIn):
        _validate_schedule(s)
        with db.session(db_path) as conn:
            if not db.get_schedule(conn, sid):
                raise HTTPException(404)
            db.update_schedule(
                conn, sid,
                name=s.name, kind=s.kind, config=_json.dumps(s.config),
                schedule_mode=s.schedule_mode,
                interval_s=s.interval_s,
                min_interval_s=s.min_interval_s,
                max_interval_s=s.max_interval_s,
                enabled=1 if s.enabled else 0,
                webhook_url=s.webhook_url or None,
                next_run_at=None,
            )
        return {"updated": sid}

    @router.post("/api/schedules/{sid}/toggle")
    def api_schedule_toggle(sid: int):
        with db.session(db_path) as conn:
            row = db.get_schedule(conn, sid)
            if not row:
                raise HTTPException(404)
            new_enabled = 0 if row["enabled"] else 1
            db.update_schedule(conn, sid, enabled=new_enabled,
                               next_run_at=None if new_enabled else row["next_run_at"])
        return {"enabled": bool(new_enabled)}

    @router.post("/api/schedules/{sid}/run-now")
    def api_schedule_run_now(sid: int):
        try:
            scheduler_run_now(db_path, sid)
        except ValueError as e:
            raise HTTPException(404, str(e))
        except Exception as e:
            raise HTTPException(500, str(e))
        with db.session(db_path) as conn:
            row = db.get_schedule(conn, sid)
        return {"last_status": row["last_status"], "last_run_id": row["last_run_id"]}

    @router.delete("/api/schedules/{sid}")
    def api_schedule_delete(sid: int, purge: bool = False):
        """Delete a schedule. If purge=true, also DELETE tracked S3 objects
        (only ones we PUT ourselves) from the remote bucket."""
        purged = 0
        failed = 0
        with db.session(db_path) as conn:
            row = db.get_schedule(conn, sid)
            if not row:
                raise HTTPException(404)
            cfg = {}
            tracked = []
            if purge:
                try:
                    cfg = _json.loads(row["config"])
                except Exception:
                    cfg = {}
                tracked = db.list_tracked_alive(conn, sid)
        if purge and tracked and cfg.get("access_key") and cfg.get("secret_key"):
            for t in tracked:
                r = s3_client.delete_object(
                    t["endpoint"], t["bucket"], t["key"],
                    access_key=cfg["access_key"], secret_key=cfg["secret_key"],
                    region=cfg.get("region", "us-east-1"),
                    session_token=cfg.get("session_token") or None,
                )
                if r.error or (r.http_status and r.http_status >= 400 and r.http_status != 404):
                    failed += 1
                else:
                    purged += 1
                    with db.session(db_path) as conn:
                        db.mark_tracked_deleted(conn, t["id"])
        with db.session(db_path) as conn:
            db.delete_schedule(conn, sid)
        return {"deleted": sid, "purged_objects": purged, "purge_failures": failed}

    @router.get("/api/schedules/{sid}/tracked")
    def api_schedule_tracked(sid: int):
        with db.session(db_path) as conn:
            alive = [dict(r) for r in db.list_tracked_alive(conn, sid)]
            total_rows = [dict(r) for r in db.list_tracked_all(conn, sid)]
        return {
            "alive_count": len(alive),
            "deleted_count": sum(1 for r in total_rows if r["deleted_at"]),
            "alive": alive[:50],
        }

    @router.post("/api/schedules/{sid}/purge")
    def api_schedule_purge(sid: int):
        with db.session(db_path) as conn:
            row = db.get_schedule(conn, sid)
            if not row:
                raise HTTPException(404)
            try:
                cfg = _json.loads(row["config"])
            except Exception:
                cfg = {}
            tracked = db.list_tracked_alive(conn, sid)
        if not cfg.get("access_key") or not cfg.get("secret_key"):
            raise HTTPException(400, "schedule has no credentials")
        purged = 0
        failed = 0
        for t in tracked:
            r = s3_client.delete_object(
                t["endpoint"], t["bucket"], t["key"],
                access_key=cfg["access_key"], secret_key=cfg["secret_key"],
                region=cfg.get("region", "us-east-1"),
                session_token=cfg.get("session_token") or None,
            )
            if r.error or (r.http_status and r.http_status >= 400 and r.http_status != 404):
                failed += 1
            else:
                purged += 1
                with db.session(db_path) as conn:
                    db.mark_tracked_deleted(conn, t["id"])
        return {"purged": purged, "failed": failed}

    return router
