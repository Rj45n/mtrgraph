"""MTR runs UI + API: /, /run/{id}, /compare, /api/run/{id}, /api/target/{}/history."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .. import db, geoip, mtr_analysis
from ..colors import latency_hex, loss_hex
from ..compare import diff
from ..db import proto_label


def create_router(db_path: Path, templates) -> APIRouter:
    router = APIRouter()

    @router.get("/", response_class=HTMLResponse)
    def index(request: Request, target: str | None = None,
              proto: str | None = None, port: int | None = None):
        with db.session(db_path) as conn:
            runs_rows = db.list_runs(conn, target=target, limit=100)
            scopes = conn.execute(
                """SELECT target, protocol, dst_port, COUNT(*) AS n
                   FROM runs GROUP BY target, protocol, dst_port
                   ORDER BY target, protocol, dst_port"""
            ).fetchall()
        runs = [
            {**dict(r), "proto_label": proto_label(r["protocol"] or "icmp", r["dst_port"])}
            for r in runs_rows
        ]
        scope_list = [
            {**dict(s), "proto_label": proto_label(s["protocol"] or "icmp", s["dst_port"])}
            for s in scopes
        ]
        return templates.TemplateResponse(
            request, "index.html",
            {"runs": runs, "scopes": scope_list, "current_target": target,
             "current_proto": proto, "current_port": port},
        )

    @router.get("/run/{run_id}", response_class=HTMLResponse)
    def run_view(request: Request, run_id: int):
        with db.session(db_path) as conn:
            run = db.get_run(conn, run_id)
            if not run:
                raise HTTPException(404)
            hops = db.get_hops(conn, run_id)
        run_dict = dict(run)
        run_dict["proto_label"] = proto_label(run["protocol"] or "icmp", run["dst_port"])
        return templates.TemplateResponse(
            request, "run.html",
            {"run": run_dict, "hops": [dict(h) for h in hops],
             "loss_hex": loss_hex, "latency_hex": latency_hex},
        )

    @router.get("/compare", response_class=HTMLResponse)
    def compare_view(request: Request, a: int, b: int):
        with db.session(db_path) as conn:
            run_a = db.get_run(conn, a)
            run_b = db.get_run(conn, b)
            if not run_a or not run_b:
                raise HTTPException(404)
            hops_a = [dict(h) for h in db.get_hops(conn, a)]
            hops_b = [dict(h) for h in db.get_hops(conn, b)]
        deltas = diff(hops_a, hops_b)
        deltas_dicts = [
            {"hop_index": d.hop_index, "host": d.host,
             "avg_a": d.avg_a, "avg_b": d.avg_b,
             "loss_a": d.loss_a, "loss_b": d.loss_b,
             "d_avg": d.d_avg, "d_loss": d.d_loss,
             "severity": d.severity}
            for d in deltas
        ]
        run_a_dict = dict(run_a)
        run_a_dict["proto_label"] = proto_label(run_a["protocol"] or "icmp", run_a["dst_port"])
        run_b_dict = dict(run_b)
        run_b_dict["proto_label"] = proto_label(run_b["protocol"] or "icmp", run_b["dst_port"])
        scope_mismatch = (
            run_a["target"] != run_b["target"]
            or (run_a["protocol"] or "icmp") != (run_b["protocol"] or "icmp")
            or run_a["dst_port"] != run_b["dst_port"]
        )
        return templates.TemplateResponse(
            request, "compare.html",
            {"run_a": run_a_dict, "run_b": run_b_dict,
             "deltas": deltas_dicts, "latency_hex": latency_hex,
             "loss_hex": loss_hex, "scope_mismatch": scope_mismatch},
        )

    @router.get("/api/target/{target}/history")
    def api_history(target: str, limit: int = 50,
                    proto: str | None = None, port: int | None = None):
        with db.session(db_path) as conn:
            if proto:
                runs = conn.execute(
                    """SELECT * FROM runs
                       WHERE target=? AND protocol=? AND (dst_port IS ? OR dst_port=?)
                       ORDER BY started_at DESC LIMIT ?""",
                    (target, proto, port, port, limit),
                ).fetchall()
            else:
                runs = db.list_runs(conn, target=target, limit=limit)
            out = []
            for r in reversed(runs):
                hops = db.get_hops(conn, r["id"])
                last = hops[-1] if hops else None
                out.append({
                    "run_id": r["id"],
                    "started_at": r["started_at"],
                    "protocol": r["protocol"],
                    "dst_port": r["dst_port"],
                    "proto_label": proto_label(r["protocol"] or "icmp", r["dst_port"]),
                    "hops_count": len(hops),
                    "dst_avg_ms": last["avg_ms"] if last else None,
                    "dst_loss_pct": last["loss_pct"] if last else None,
                })
        return JSONResponse(out)

    @router.get("/api/run/{run_id}")
    def api_run(run_id: int):
        with db.session(db_path) as conn:
            run = db.get_run(conn, run_id)
            if not run:
                raise HTTPException(404)
            hops = [dict(h) for h in db.get_hops(conn, run_id)]
        return {"run": dict(run), "hops": hops}

    @router.get("/mtr", response_class=HTMLResponse)
    def mtr_targets_list(request: Request):
        with db.session(db_path) as conn:
            targets_rows = db.list_targets(conn)
        return templates.TemplateResponse(
            request, "mtr_targets.html",
            {"targets": [dict(r) for r in targets_rows]},
        )

    @router.get("/mtr/target/{target}", response_class=HTMLResponse)
    def mtr_target_page(request: Request, target: str):
        with db.session(db_path) as conn:
            targets_rows = db.list_targets(conn)
        targets = [dict(r) for r in targets_rows]
        return templates.TemplateResponse(
            request, "mtr_target.html",
            {"target": target, "targets": targets},
        )

    @router.get("/api/mtr/targets")
    def api_mtr_targets():
        with db.session(db_path) as conn:
            rows = db.list_targets(conn)
        return [dict(r) for r in rows]

    @router.get("/api/mtr/target/{target}/series")
    def api_mtr_target_series(target: str, last_n: int = 100):
        """Per-hop latency timeseries (matrix hop × time)."""
        with db.session(db_path) as conn:
            return db.hop_matrix(conn, target, metric="avg_ms", last_n=last_n)

    @router.get("/api/mtr/target/{target}/loss")
    def api_mtr_target_loss(target: str, last_n: int = 100):
        """Per-hop loss heatmap (matrix hop × time)."""
        with db.session(db_path) as conn:
            return db.hop_matrix(conn, target, metric="loss_pct", last_n=last_n)

    @router.get("/api/mtr/target/{target}/latest-enriched")
    def api_mtr_target_latest_enriched(target: str):
        """Latest run with each hop enriched with ASN / AS name / country
        from the on-disk team-cymru cache."""
        with db.session(db_path) as conn:
            runs = conn.execute(
                "SELECT * FROM runs WHERE target=? ORDER BY started_at DESC LIMIT 1",
                (target,),
            ).fetchall()
            if not runs:
                raise HTTPException(404)
            r = runs[0]
            hops = [dict(h) for h in db.get_hops(conn, r["id"])]
        try:
            mtr_analysis.enrich_hops_with_as(db_path, hops)
        except Exception:
            pass
        try:
            geoip.enrich_hops_with_geo(db_path, hops)
        except Exception:
            pass
        return {"run": dict(r), "hops": hops}

    @router.get("/api/mtr/target/{target}/asymmetry")
    def api_mtr_target_asymmetry(target: str, proto_a: str = "tcp", proto_b: str = "icmp",
                                   last_n: int = 5):
        """Compare the most recent N runs of proto_a vs proto_b on `target`."""
        with db.session(db_path) as conn:
            def _runs(proto):
                rows = conn.execute(
                    "SELECT * FROM runs WHERE target=? AND protocol=? ORDER BY started_at DESC LIMIT ?",
                    (target, proto, last_n),
                ).fetchall()
                out = []
                for r in rows:
                    hops = conn.execute(
                        "SELECT * FROM hops WHERE run_id=? ORDER BY hop_index", (r["id"],),
                    ).fetchall()
                    d = dict(r)
                    d["hops"] = [dict(h) for h in hops]
                    out.append(d)
                return out
            runs_a = _runs(proto_a)
            runs_b = _runs(proto_b)
        return {
            "proto_a": proto_a, "proto_b": proto_b,
            "runs_a_count": len(runs_a), "runs_b_count": len(runs_b),
            **mtr_analysis.asymmetry_score(runs_a, runs_b),
        }

    @router.get("/api/mtr/target/{target}/latest")
    def api_mtr_target_latest(target: str):
        """The very last run for this target: used for topology + hop contribution."""
        with db.session(db_path) as conn:
            runs = conn.execute(
                "SELECT * FROM runs WHERE target=? ORDER BY started_at DESC LIMIT 1",
                (target,),
            ).fetchall()
            if not runs:
                raise HTTPException(404)
            r = runs[0]
            hops = [dict(h) for h in db.get_hops(conn, r["id"])]
        return {"run": dict(r), "hops": hops}

    return router
