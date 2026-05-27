"""MTR executor + trigger_auto_mtr (background thread for auto-correlation)."""
from __future__ import annotations

import random
import threading
from pathlib import Path

from ... import db
from ...compare import degraded_hops, diff, hops_from_baseline
from ...probe import parse_report, resolve_port, run_mtr


def trigger_auto_mtr(target_ip: str, db_path: Path, log_fn=print,
                       proto: str = "tcp", port: int = 443, cycles: int = 3,
                       label: str = "auto-mtr") -> None:
    """Launch a quick MTR towards target_ip in a background thread (non-blocking).
    Stores result in `runs`/`hops` so the dashboard can correlate RTT with S3.
    Skips silently on any error."""
    if not target_ip:
        return

    def _run():
        try:
            data = run_mtr(target_ip, cycles=cycles, interval=1.0, protocol=proto, port=port)
            src, hops = parse_report(data)
            resolved_port = resolve_port(proto, port)
            with db.session(db_path) as conn:
                run_id = db.insert_run(
                    conn, target_ip, cycles, label, src,
                    protocol=proto, dst_port=resolved_port,
                )
                db.insert_hops(conn, run_id, hops)
                db.finalize_run(conn, run_id)
            log_fn(f"[auto-mtr] {target_ip} → {len(hops)} hops (run #{run_id})")
        except Exception as e:
            log_fn(f"[auto-mtr] {target_ip} skipped: {e}")

    threading.Thread(target=_run, name=f"auto-mtr-{target_ip}", daemon=True).start()


def execute(config: dict, db_path: Path) -> tuple[int, str]:
    """Run a single MTR probe from a schedule config. Returns (run_id, status_str)."""
    pool = config.get("targets_pool") or []
    target = random.choice(pool) if pool else config["target"]
    proto = config.get("proto", "icmp")
    port = config.get("port")
    cycles = int(config.get("cycles", 10))
    interval = float(config.get("interval_s", 1.0))
    label = config.get("label", "scheduled-mtr")
    auto_compare = bool(config.get("auto_compare", False))
    baseline_n = int(config.get("baseline_n", 10))

    data = run_mtr(target, cycles=cycles, interval=interval, protocol=proto, port=port)
    src, hops = parse_report(data)
    resolved_port = resolve_port(proto, port)

    with db.session(db_path) as conn:
        baseline = (
            db.baseline_hops(conn, target, protocol=proto, dst_port=resolved_port, last_n=baseline_n)
            if auto_compare else {}
        )
        run_id = db.insert_run(
            conn, target, cycles, label, src,
            protocol=proto, dst_port=resolved_port,
        )
        db.insert_hops(conn, run_id, hops)
        db.finalize_run(conn, run_id)

    if not auto_compare or not baseline:
        return run_id, f"ok:dst_avg={hops[-1]['avg_ms']:.0f}ms" if hops else "ok:no-hops"

    deltas = diff(hops_from_baseline(baseline), hops)
    bad = degraded_hops(deltas)
    if not bad:
        return run_id, "ok:vs-baseline"
    crit = [d for d in bad if d.severity == "critical"]
    sev = "critical" if crit else "warning"
    hop = (crit or bad)[0]
    parts = [f"hop{hop.hop_index}"]
    if hop.d_avg is not None and abs(hop.d_avg) >= 10:
        parts.append(f"avg {hop.avg_a:.0f}→{hop.avg_b:.0f}ms")
    if hop.d_loss is not None and hop.d_loss >= 3:
        parts.append(f"loss {hop.loss_a:.0f}%→{hop.loss_b:.0f}%")
    return run_id, f"{sev}:{' '.join(parts)}"
