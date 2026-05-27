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

    @router.get("/http/by-url", response_class=HTMLResponse)
    def http_by_url_page_early(request: Request, url: str):
        """Static route declared before /http/{run_id:int} to win route ordering."""
        with db.session(db_path) as conn:
            urls = [r["url"] for r in conn.execute(
                "SELECT DISTINCT url FROM http_runs ORDER BY url"
            ).fetchall()]
        return templates.TemplateResponse(
            request, "http_by_url.html",
            {"url": url, "urls": urls},
        )

    @router.get("/http/compare", response_class=HTMLResponse)
    def http_compare_page_early(request: Request, a: int, b: int):
        """Static route declared before /http/{run_id:int} to win route ordering."""
        with db.session(db_path) as conn:
            run_a = db.get_http_run(conn, a)
            run_b = db.get_http_run(conn, b)
            if not run_a or not run_b:
                raise HTTPException(404)
            samples_a = [dict(s) for s in db.get_http_samples(conn, a)]
            samples_b = [dict(s) for s in db.get_http_samples(conn, b)]
        from ..http_probe import HttpSample
        from ..http_probe import aggregate as http_aggregate

        def _agg(samples):
            objs = [HttpSample(
                s["sample_idx"], s["dns_ms"], s["tcp_ms"], s["tls_ms"],
                s["ttfb_ms"], s["total_ms"], s["status"], None, s["error"],
            ) for s in samples]
            return http_aggregate(objs)

        agg_a = _agg(samples_a)
        agg_b = _agg(samples_b)
        stages = ("dns", "tcp", "tls", "ttfb", "total")
        deltas = {}
        for st in stages:
            a_avg = agg_a[st]["avg"]
            b_avg = agg_b[st]["avg"]
            if a_avg is None or b_avg is None:
                deltas[st] = {"a": a_avg, "b": b_avg, "d_abs": None, "d_pct": None, "verdict": "no_data"}
                continue
            d_abs = b_avg - a_avg
            d_pct = (d_abs / a_avg * 100) if a_avg else None
            verdict = "ok"
            if d_pct is not None and a_avg > 0:
                if b_avg >= a_avg * 3 and d_abs >= 50:
                    verdict = "critical"
                elif b_avg >= a_avg * 1.5 and d_abs >= 20:
                    verdict = "warning"
            deltas[st] = {"a": a_avg, "b": b_avg, "d_abs": d_abs, "d_pct": d_pct, "verdict": verdict}
        return templates.TemplateResponse(
            request, "http_compare.html",
            {
                "run_a": dict(run_a), "run_b": dict(run_b),
                "agg_a": agg_a, "agg_b": agg_b,
                "deltas": deltas, "stages": stages,
            },
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

    @router.get("/api/http/url/series")
    def api_http_url_series(url: str, limit: int = 100):
        """Per-stage averages over time for a given URL (for the timeline chart)."""
        from ..http_probe import HttpSample
        from ..http_probe import aggregate as http_aggregate

        with db.session(db_path) as conn:
            runs = db.list_http_runs(conn, url=url, limit=limit)
            out = []
            for r in reversed(runs):
                samples = db.get_http_samples(conn, r["id"])
                objs = [HttpSample(
                    s["sample_idx"], s["dns_ms"], s["tcp_ms"], s["tls_ms"],
                    s["ttfb_ms"], s["total_ms"], s["status"], None, s["error"],
                ) for s in samples]
                agg = http_aggregate(objs)
                d = dict(r)
                d["agg_dns_avg"]   = agg["dns"]["avg"]
                d["agg_tcp_avg"]   = agg["tcp"]["avg"]
                d["agg_tls_avg"]   = agg["tls"]["avg"]
                d["agg_ttfb_avg"]  = agg["ttfb"]["avg"]
                d["agg_total_avg"] = agg["total"]["avg"]
                d["err_pct"]       = round(100 * (agg["errors"] / max(len(samples), 1)), 2)
                out.append(d)
        return out

    @router.get("/api/http/url/by-ip")
    def api_http_url_by_ip(url: str, limit: int = 200):
        """Aggregate stats per resolved_ip for a given URL."""
        with db.session(db_path) as conn:
            runs = db.list_http_runs(conn, url=url, limit=limit)
            ip_data: dict = {}
            for r in runs:
                ip = r["resolved_ip"] or "?"
                samples = db.get_http_samples(conn, r["id"])
                ip_data.setdefault(ip, []).extend(dict(s) for s in samples)

        def stats(rows, col):
            vs = [r[col] for r in rows if r[col] is not None]
            return sum(vs) / len(vs) if vs else None

        out = []
        for ip, samples in ip_data.items():
            errs = sum(1 for s in samples if s["error"] or (s["status"] and s["status"] >= 400))
            out.append({
                "ip": ip,
                "samples": len(samples),
                "err_count": errs,
                "err_pct": round(100 * errs / len(samples), 1) if samples else 0,
                "avg_dns_ms":   stats(samples, "dns_ms"),
                "avg_tcp_ms":   stats(samples, "tcp_ms"),
                "avg_tls_ms":   stats(samples, "tls_ms"),
                "avg_ttfb_ms":  stats(samples, "ttfb_ms"),
                "avg_total_ms": stats(samples, "total_ms"),
            })
        out.sort(key=lambda x: -(x["avg_total_ms"] or 0))
        return out

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
