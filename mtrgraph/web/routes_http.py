"""HTTP probes UI + API: /http, /http/{id}, /api/http/*."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import db
from ..colors import http_hex
from ..http_probe import HttpSample
from ..http_probe import aggregate as http_aggregate


def create_router(db_path: Path, templates) -> APIRouter:
    router = APIRouter()

    @router.get("/http", response_class=HTMLResponse)
    def http_index(request: Request, url: str | None = None):
        with db.session(db_path) as conn:
            runs = db.list_http_runs(conn, url=url, limit=100)
            urls = [
                r["url"] for r in conn.execute(
                    "SELECT DISTINCT url FROM http_runs ORDER BY url"
                ).fetchall()
            ]
        return templates.TemplateResponse(
            request, "http_index.html",
            {"runs": [dict(r) for r in runs], "urls": urls, "current_url": url},
        )

    @router.get("/http/{run_id}", response_class=HTMLResponse)
    def http_view(request: Request, run_id: int):
        with db.session(db_path) as conn:
            run = db.get_http_run(conn, run_id)
            if not run:
                raise HTTPException(404)
            samples = [dict(s) for s in db.get_http_samples(conn, run_id)]
        sample_objs = [
            HttpSample(s["sample_idx"], s["dns_ms"], s["tcp_ms"], s["tls_ms"],
                       s["ttfb_ms"], s["total_ms"], s["status"], None, s["error"])
            for s in samples
        ]
        agg = http_aggregate(sample_objs)
        return templates.TemplateResponse(
            request, "http_run.html",
            {"run": dict(run), "samples": samples, "agg": agg, "http_hex": http_hex},
        )

    @router.get("/api/http/{run_id}")
    def api_http_run(run_id: int):
        with db.session(db_path) as conn:
            run = db.get_http_run(conn, run_id)
            if not run:
                raise HTTPException(404)
            samples = [dict(s) for s in db.get_http_samples(conn, run_id)]
        return {"run": dict(run), "samples": samples}

    @router.get("/api/http/url/history")
    def api_http_history(url: str, limit: int = 50):
        with db.session(db_path) as conn:
            runs = db.list_http_runs(conn, url=url, limit=limit)
            out = []
            for r in reversed(runs):
                samples = db.get_http_samples(conn, r["id"])
                obj_samples = [
                    HttpSample(s["sample_idx"], s["dns_ms"], s["tcp_ms"], s["tls_ms"],
                               s["ttfb_ms"], s["total_ms"], s["status"], None, s["error"])
                    for s in samples
                ]
                agg = http_aggregate(obj_samples)
                out.append({
                    "run_id": r["id"],
                    "started_at": r["started_at"],
                    "status_summary": r["status_summary"],
                    "errors": r["errors"],
                    "dns_avg": agg["dns"]["avg"],
                    "tcp_avg": agg["tcp"]["avg"],
                    "tls_avg": agg["tls"]["avg"],
                    "ttfb_avg": agg["ttfb"]["avg"],
                    "total_avg": agg["total"]["avg"],
                })
        return JSONResponse(out)

    return router
