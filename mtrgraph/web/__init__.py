"""FastAPI web entrypoint — composes the per-domain routers.

Public surface:
- `create_app(db_path, start_scheduler=True)` returns a configured FastAPI app
- `serve(db_path, host, port)` runs uvicorn

Each route group lives in its own module under `web/routes_*.py`. Background
tasks (scheduler, retention) are wired here in `create_app`.
"""
from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from .. import retention as ret_mod
from ..scheduler import Scheduler
from . import (
    routes_admin,
    routes_dashboard,
    routes_http,
    routes_mtr,
    routes_s3,
    routes_schedules,
)

TEMPLATES_DIR = Path(__file__).parent.parent / "templates"


def create_app(db_path: Path, start_scheduler: bool = True) -> FastAPI:
    app = FastAPI(title="mtrgraph")
    templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
    # Custom Jinja filter to decode JSON columns inline in templates
    import json as _json
    def _fromjson(v):
        if not v:
            return None
        try:
            return _json.loads(v)
        except (ValueError, TypeError):
            return None
    templates.env.filters["fromjson"] = _fromjson

    scheduler = Scheduler(db_path)
    retention_days = int(os.environ.get("MTRGRAPH_RETENTION_DAYS", "30"))
    retention_period_h = float(os.environ.get("MTRGRAPH_RETENTION_PERIOD_HOURS", "24"))
    retention_task = ret_mod.RetentionTask(
        db_path, max_age_days=retention_days, period_h=retention_period_h,
    )

    if start_scheduler:
        @app.on_event("startup")
        def _start_bg():
            scheduler.start()
            retention_task.start()

        @app.on_event("shutdown")
        def _stop_bg():
            scheduler.stop()
            retention_task.stop()

    app.include_router(routes_mtr.create_router(db_path, templates))
    app.include_router(routes_http.create_router(db_path, templates))
    app.include_router(routes_s3.create_router(db_path, templates))
    app.include_router(routes_schedules.create_router(db_path, templates))
    app.include_router(routes_dashboard.create_router(db_path, templates))
    app.include_router(routes_admin.create_router(db_path, templates))
    return app


def serve(db_path: Path, host: str = "127.0.0.1", port: int = 8765) -> None:
    import uvicorn
    uvicorn.run(create_app(db_path), host=host, port=port)
