"""Admin API: /api/admin/db-stats, /api/admin/retention."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter

from .. import retention as ret_mod


def create_router(db_path: Path, templates) -> APIRouter:
    router = APIRouter()

    @router.get("/api/admin/db-stats")
    def api_admin_db_stats():
        return ret_mod.db_stats(db_path)

    @router.post("/api/admin/retention")
    def api_admin_retention(max_age_days: int = 30, vacuum: bool = True):
        s = ret_mod.apply_retention(db_path, max_age_days=max_age_days, vacuum=vacuum)
        return {
            "runs_deleted": s.runs_deleted,
            "http_runs_deleted": s.http_runs_deleted,
            "s3_runs_deleted": s.s3_runs_deleted,
            "tcp_samples_deleted": s.tcp_samples_deleted,
            "tracked_purged": s.tracked_purged,
            "bytes_freed": s.bytes_freed,
            "bytes_after": s.bytes_after,
            "duration_s": s.duration_s,
            "vacuumed": s.vacuumed,
        }

    return router
