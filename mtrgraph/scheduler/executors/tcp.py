"""TCP stats executor (samples /proc/net/snmp deltas)."""
from __future__ import annotations

from pathlib import Path

from ... import db, tcp_stats


def execute(config: dict, db_path: Path) -> tuple[int, str]:
    """Sample TCP stats and store. Returns (sample_id, status)."""
    duration = float(config.get("duration_s", 5.0))
    label = config.get("label", "scheduled-tcp")
    d = tcp_stats.sample(duration)
    with db.session(db_path) as conn:
        sid = db.insert_tcp_sample(
            conn,
            duration_s=d["duration_s"],
            retrans_pct=d["retrans_pct"],
            retrans_per_s=d["retrans_segs_per_s"],
            out_per_s=d["out_segs_per_s"],
            in_per_s=d["in_segs_per_s"],
            in_errs_delta=d["in_errs_delta"],
            estab_resets_delta=d["estab_resets_delta"],
            active_opens_delta=d["active_opens_delta"],
            label=label,
        )
    pct = d["retrans_pct"]
    if pct >= 1.0:
        status = f"critical:retrans {pct:.2f}%"
    elif pct >= 0.1:
        status = f"warning:retrans {pct:.2f}%"
    else:
        status = f"ok:retrans {pct:.3f}%"
    return sid, status
