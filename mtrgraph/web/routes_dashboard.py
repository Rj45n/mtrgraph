"""Dashboard UI + correlated API: /dashboard, /api/dashboard/*."""
from __future__ import annotations

from datetime import datetime as _dt
from datetime import timedelta as _td
from datetime import timezone as _tz
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse

from .. import db


def create_router(db_path: Path, templates) -> APIRouter:
    router = APIRouter()

    @router.get("/dashboard", response_class=HTMLResponse)
    def dashboard_page(request: Request):
        return templates.TemplateResponse(request, "dashboard.html", {})

    @router.get("/api/dashboard/series")
    def api_dashboard_series(
        endpoint: str,
        operation: str | None = None,
        bucket: str | None = None,
        limit: int = 200,
        start_time: str | None = None,
        end_time: str | None = None,
    ):
        """Returns S3 runs + correlated MTR RTT + derived throughput/server_proc."""
        sql = "SELECT * FROM s3_runs WHERE endpoint=?"
        params: list = [endpoint]
        if operation:
            sql += " AND operation=?"; params.append(operation)
        if bucket:
            sql += " AND bucket=?"; params.append(bucket)
        if start_time:
            sql += " AND started_at >= ?"; params.append(start_time)
        if end_time:
            sql += " AND started_at <= ?"; params.append(end_time)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        out = []
        with db.session(db_path) as conn:
            rows = list(reversed(conn.execute(sql, params).fetchall()))
            for r in rows:
                d = dict(r)
                rtt = db.latest_mtr_rtt_for_ip(conn, d.get("resolved_ip") or "")
                ttfb = d.get("ttfb_ms")
                server_proc = None
                if rtt is not None and ttfb is not None:
                    sp = ttfb - rtt
                    server_proc = sp if sp >= 0 else 0.0
                d["network_rtt_ms"] = rtt
                d["server_processing_ms"] = server_proc
                bytes_ = d.get("bytes_transferred") or 0
                dur = d.get("duration_ms") or 0
                if bytes_ > 0 and dur > 0:
                    d["throughput_mbps"] = (bytes_ / (dur / 1000.0)) / (1024 * 1024)
                else:
                    d["throughput_mbps"] = None
                out.append(d)
        return out

    @router.get("/api/dashboard/kpis")
    def api_dashboard_kpis(
        endpoint: str,
        operation: str | None = None,
        bucket: str | None = None,
        last_n: int = 100,
        start_time: str | None = None,
        end_time: str | None = None,
    ):
        sql = "SELECT * FROM s3_runs WHERE endpoint=?"
        params: list = [endpoint]
        if operation:
            sql += " AND operation=?"; params.append(operation)
        if bucket:
            sql += " AND bucket=?"; params.append(bucket)
        if start_time:
            sql += " AND started_at >= ?"; params.append(start_time)
        if end_time:
            sql += " AND started_at <= ?"; params.append(end_time)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(last_n)
        with db.session(db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        if not rows:
            return {"count": 0}

        def percentile(values, p):
            if not values:
                return None
            v = sorted(values)
            k = int(round((p / 100.0) * (len(v) - 1)))
            return v[k]

        total_ms = [r["duration_ms"] for r in rows if r["duration_ms"] is not None]
        ttfb_ms = [r["ttfb_ms"] for r in rows if r["ttfb_ms"] is not None]
        errs = sum(1 for r in rows if r["error"] or (r["http_status"] and r["http_status"] >= 400))
        ips = {r["resolved_ip"] for r in rows if r["resolved_ip"]}
        ops_seen: dict = {}
        for r in rows:
            ops_seen[r["operation"]] = ops_seen.get(r["operation"], 0) + 1
        return {
            "count": len(rows),
            "err_count": errs,
            "err_pct": round(100 * errs / len(rows), 2) if rows else 0,
            "avg_total_ms": sum(total_ms) / len(total_ms) if total_ms else None,
            "p50_total_ms": percentile(total_ms, 50),
            "p95_total_ms": percentile(total_ms, 95),
            "p99_total_ms": percentile(total_ms, 99),
            "avg_ttfb_ms": sum(ttfb_ms) / len(ttfb_ms) if ttfb_ms else None,
            "p95_ttfb_ms": percentile(ttfb_ms, 95),
            "ips": sorted(ips),
            "ops_distribution": ops_seen,
        }

    @router.get("/api/dashboard/tcp")
    def api_dashboard_tcp(
        start_time: str | None = None,
        end_time: str | None = None,
        limit: int = 500,
    ):
        with db.session(db_path) as conn:
            rows = list(reversed(db.list_tcp_samples(
                conn, limit=limit, start_time=start_time, end_time=end_time,
            )))
        return [dict(r) for r in rows]

    @router.get("/api/dashboard/errors")
    def api_dashboard_errors(
        endpoint: str,
        operation: str | None = None,
        bucket: str | None = None,
        limit: int = 100,
        start_time: str | None = None,
        end_time: str | None = None,
    ):
        sql = """SELECT * FROM s3_runs
                 WHERE endpoint=?
                   AND (error IS NOT NULL OR (http_status IS NOT NULL AND http_status >= 400))"""
        params: list = [endpoint]
        if operation:
            sql += " AND operation=?"; params.append(operation)
        if bucket:
            sql += " AND bucket=?"; params.append(bucket)
        if start_time:
            sql += " AND started_at >= ?"; params.append(start_time)
        if end_time:
            sql += " AND started_at <= ?"; params.append(end_time)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with db.session(db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @router.get("/api/dashboard/closest-run")
    def api_dashboard_closest_run(
        endpoint: str,
        timestamp: str,
        operation: str | None = None,
        bucket: str | None = None,
        window_minutes: int = 10,
    ):
        try:
            ts = _dt.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(400, "invalid timestamp (expected ISO 8601)")
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=_tz.utc)
        lo = (ts - _td(minutes=window_minutes)).isoformat(timespec="seconds")
        hi = (ts + _td(minutes=window_minutes)).isoformat(timespec="seconds")
        sql = """SELECT * FROM s3_runs
                 WHERE endpoint=? AND started_at >= ? AND started_at <= ?"""
        params: list = [endpoint, lo, hi]
        if operation:
            sql += " AND operation=?"; params.append(operation)
        if bucket:
            sql += " AND bucket=?"; params.append(bucket)
        with db.session(db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
            if not rows:
                return {"found": False, "window_minutes": window_minutes}

            def dist(r):
                rts = _dt.fromisoformat(r["started_at"].replace("Z", "+00:00"))
                if rts.tzinfo is None:
                    rts = rts.replace(tzinfo=_tz.utc)
                return abs((rts - ts).total_seconds())

            best = min(rows, key=dist)
            d = dict(best)
            rtt = db.latest_mtr_rtt_for_ip(conn, d.get("resolved_ip") or "")
            if rtt is not None and d.get("ttfb_ms") is not None:
                sp = d["ttfb_ms"] - rtt
                d["server_processing_ms"] = sp if sp >= 0 else 0.0
                d["network_rtt_ms"] = rtt
            else:
                d["server_processing_ms"] = None
                d["network_rtt_ms"] = rtt
            bytes_ = d.get("bytes_transferred") or 0
            dur = d.get("duration_ms") or 0
            d["throughput_mbps"] = (bytes_ / (dur / 1000.0)) / (1024 * 1024) if bytes_ and dur else None
            return {"found": True, "run": d, "delta_seconds": dist(best)}

    return router
