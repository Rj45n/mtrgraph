"""S3 testing UI + API: /s3, /api/s3/*."""
from __future__ import annotations

import os as _os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from .. import db, s3_bench, s3_client
from ..scheduler import trigger_auto_mtr


class S3TestRequest(BaseModel):
    endpoint: str
    region: str = "us-east-1"
    access_key: str
    secret_key: str
    session_token: str | None = None
    bucket: str
    operation: str  # list | head | get | put | delete
    key: str | None = None
    prefix: str = ""
    max_keys: int = 1000
    content_type: str = "application/octet-stream"
    body_size_kb: int | None = None
    body_text: str | None = None
    label: str | None = None
    timeout: float = 30.0
    auto_mtr: bool = True


class S3BenchRequest(BaseModel):
    endpoint: str
    region: str = "us-east-1"
    access_key: str
    secret_key: str
    session_token: str | None = None
    bucket: str
    operation: str               # 'get' | 'put'
    key_or_prefix: str = "mtrgraph-bench/"
    concurrency: int = 10
    count: int = 100
    size_kb: int = 64
    timeout: float = 30.0
    label: str = "bench"
    track_puts: bool = True


def create_router(db_path: Path, templates) -> APIRouter:
    router = APIRouter()

    @router.get("/s3", response_class=HTMLResponse)
    def s3_page(request: Request):
        with db.session(db_path) as conn:
            recent = [dict(r) for r in db.list_s3_runs(conn, limit=50)]
            endpoints = sorted({r["endpoint"] for r in recent})
            buckets = sorted({r["bucket"] for r in recent if r["bucket"]})
        return templates.TemplateResponse(
            request, "s3.html",
            {"recent": recent, "endpoints": endpoints, "buckets": buckets},
        )

    @router.post("/api/s3/test")
    def api_s3_test(req: S3TestRequest):
        op = req.operation.lower()
        kw = dict(
            access_key=req.access_key, secret_key=req.secret_key,
            region=req.region, session_token=req.session_token,
            timeout=req.timeout, label=req.label,
        )
        try:
            if op == "list":
                result = s3_client.list_bucket(
                    req.endpoint, req.bucket, prefix=req.prefix, max_keys=req.max_keys, **kw,
                )
            elif op == "head":
                if not req.key:
                    raise HTTPException(400, "key required for head")
                result = s3_client.head_object(req.endpoint, req.bucket, req.key, **kw)
            elif op == "get":
                if not req.key:
                    raise HTTPException(400, "key required for get")
                result = s3_client.get_object(req.endpoint, req.bucket, req.key, **kw)
            elif op == "put":
                if not req.key:
                    raise HTTPException(400, "key required for put")
                if req.body_text is not None:
                    body = req.body_text.encode("utf-8")
                elif req.body_size_kb:
                    body = _os.urandom(req.body_size_kb * 1024)
                else:
                    body = b"mtrgraph test payload"
                result = s3_client.put_object(
                    req.endpoint, req.bucket, req.key, body,
                    content_type=req.content_type, **kw,
                )
            elif op == "delete":
                if not req.key:
                    raise HTTPException(400, "key required for delete")
                result = s3_client.delete_object(req.endpoint, req.bucket, req.key, **kw)
            else:
                raise HTTPException(400, f"unknown operation {op!r}")
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, f"client error: {e}")

        with db.session(db_path) as conn:
            run_id = db.insert_s3_run(conn, result)
        if req.auto_mtr and result.resolved_ip:
            trigger_auto_mtr(result.resolved_ip, db_path)
        return {
            "run_id": run_id,
            "operation": result.operation,
            "endpoint": result.endpoint,
            "bucket": result.bucket,
            "key": result.key,
            "http_status": result.http_status,
            "duration_ms": result.duration_ms,
            "dns_ms": result.dns_ms,
            "tcp_ms": result.tcp_ms,
            "tls_ms": result.tls_ms,
            "ttfb_ms": result.ttfb_ms,
            "bytes_transferred": result.bytes_transferred,
            "resolved_ip": result.resolved_ip,
            "response_summary": result.response_summary,
            "error": result.error,
        }

    @router.post("/api/s3/bench")
    def api_s3_bench(req: S3BenchRequest):
        try:
            summary = s3_bench.run_bench(
                operation=req.operation,
                endpoint=req.endpoint, bucket=req.bucket,
                access_key=req.access_key, secret_key=req.secret_key,
                region=req.region, key_or_prefix=req.key_or_prefix,
                concurrency=req.concurrency, count=req.count,
                object_size_kb=req.size_kb,
                session_token=req.session_token, timeout=req.timeout,
                label=req.label, db_path=db_path, track_puts=req.track_puts,
            )
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {
            "operation": summary.operation,
            "endpoint": summary.endpoint,
            "bucket": summary.bucket,
            "concurrency": summary.concurrency,
            "total_ops": summary.total_ops,
            "successful_ops": summary.successful_ops,
            "errors": summary.errors,
            "total_bytes": summary.total_bytes,
            "total_wall_s": summary.total_wall_s,
            "throughput_mbps": summary.throughput_mbps,
            "ops_per_sec": summary.ops_per_sec,
            "p50_ms": summary.p50_ms, "p95_ms": summary.p95_ms, "p99_ms": summary.p99_ms,
            "avg_ms": summary.avg_ms, "min_ms": summary.min_ms, "max_ms": summary.max_ms,
            "label": summary.label,
        }

    @router.get("/api/s3/history")
    def api_s3_history(endpoint: str | None = None, operation: str | None = None,
                       bucket: str | None = None, limit: int = 200):
        sql = "SELECT * FROM s3_runs WHERE 1=1"
        params: list = []
        if endpoint:
            sql += " AND endpoint=?"; params.append(endpoint)
        if operation:
            sql += " AND operation=?"; params.append(operation)
        if bucket:
            sql += " AND bucket=?"; params.append(bucket)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with db.session(db_path) as conn:
            rows = list(reversed(conn.execute(sql, params).fetchall()))
        return [dict(r) for r in rows]

    @router.get("/api/s3/by-ip")
    def api_s3_by_ip(endpoint: str | None = None, operation: str | None = None,
                     bucket: str | None = None, limit: int = 500):
        sql = "SELECT * FROM s3_runs WHERE 1=1"
        params: list = []
        if endpoint:
            sql += " AND endpoint=?"; params.append(endpoint)
        if operation:
            sql += " AND operation=?"; params.append(operation)
        if bucket:
            sql += " AND bucket=?"; params.append(bucket)
        sql += " ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        with db.session(db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        buckets: dict[str | None, list] = {}
        for r in rows:
            buckets.setdefault(r["resolved_ip"], []).append(r)

        def stats(rows_, col):
            vs = [r[col] for r in rows_ if r[col] is not None]
            return sum(vs) / len(vs) if vs else None

        out = []
        for ip, ip_rows in buckets.items():
            err = sum(1 for r in ip_rows if r["error"] or (r["http_status"] and r["http_status"] >= 400))
            out.append({
                "ip": ip or "?",
                "count": len(ip_rows),
                "err_count": err,
                "err_pct": round(100 * err / len(ip_rows), 1) if ip_rows else 0,
                "avg_dns_ms":   stats(ip_rows, "dns_ms"),
                "avg_tcp_ms":   stats(ip_rows, "tcp_ms"),
                "avg_tls_ms":   stats(ip_rows, "tls_ms"),
                "avg_ttfb_ms":  stats(ip_rows, "ttfb_ms"),
                "avg_total_ms": stats(ip_rows, "duration_ms"),
            })
        out.sort(key=lambda x: -(x["avg_total_ms"] or 0))
        return out

    @router.get("/api/s3/filters")
    def api_s3_filters():
        with db.session(db_path) as conn:
            endpoints = [r[0] for r in conn.execute("SELECT DISTINCT endpoint FROM s3_runs ORDER BY endpoint").fetchall()]
            buckets = [r[0] for r in conn.execute("SELECT DISTINCT bucket FROM s3_runs WHERE bucket IS NOT NULL ORDER BY bucket").fetchall()]
            ops = [r[0] for r in conn.execute("SELECT DISTINCT operation FROM s3_runs ORDER BY operation").fetchall()]
        return {"endpoints": endpoints, "buckets": buckets, "operations": ops}

    @router.get("/api/s3/runs")
    def api_s3_runs(limit: int = 50, endpoint: str | None = None, operation: str | None = None):
        with db.session(db_path) as conn:
            runs = db.list_s3_runs(conn, endpoint=endpoint, operation=operation, limit=limit)
        return [dict(r) for r in runs]

    @router.delete("/api/s3/runs/{run_id}")
    def api_s3_run_delete(run_id: int):
        with db.session(db_path) as conn:
            db.delete_s3_run(conn, run_id)
        return {"deleted": run_id}

    return router
