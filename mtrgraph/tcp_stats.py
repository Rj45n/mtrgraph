"""TCP-level stats from /proc/net/snmp and `ss -ti`.

Useful complement to MTR packet loss: confirms that loss seen by mtr translates
to actual TCP retransmissions experienced by real flows.

In containers, /proc/net/snmp shows the network namespace's counters. For
host-level stats use hostNetwork in K8s or `--net=host` for Docker.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class TcpSnapshot:
    timestamp: float
    out_segs: int          # total segments sent
    retrans_segs: int      # retransmitted segments
    in_segs: int           # received
    in_errs: int           # bad checksum / etc.
    active_opens: int      # connect() calls
    estab_resets: int      # connections reset while ESTABLISHED


def read_snmp() -> TcpSnapshot:
    """Parse /proc/net/snmp for TCP MIB counters."""
    p = Path("/proc/net/snmp")
    if not p.exists():
        raise RuntimeError("/proc/net/snmp absent (not Linux ?)")
    keys = None
    values = None
    with p.open() as f:
        for line in f:
            if line.startswith("Tcp:"):
                row = line.rstrip().split(" ")[1:]
                if keys is None:
                    keys = row
                else:
                    values = [int(x) for x in row]
                    break
    if keys is None or values is None:
        raise RuntimeError("TCP section missing in /proc/net/snmp")
    m = dict(zip(keys, values))
    return TcpSnapshot(
        timestamp=time.time(),
        out_segs=m.get("OutSegs", 0),
        retrans_segs=m.get("RetransSegs", 0),
        in_segs=m.get("InSegs", 0),
        in_errs=m.get("InErrs", 0),
        active_opens=m.get("ActiveOpens", 0),
        estab_resets=m.get("EstabResets", 0),
    )


def snapshot_delta(a: TcpSnapshot, b: TcpSnapshot) -> dict:
    """Return per-second rates and ratios between two snapshots."""
    dt = max(b.timestamp - a.timestamp, 0.001)
    d_out = b.out_segs - a.out_segs
    d_retrans = b.retrans_segs - a.retrans_segs
    d_in = b.in_segs - a.in_segs
    return {
        "duration_s": dt,
        "out_segs_per_s": d_out / dt,
        "retrans_segs_per_s": d_retrans / dt,
        "in_segs_per_s": d_in / dt,
        "retrans_pct": (100.0 * d_retrans / d_out) if d_out > 0 else 0.0,
        "in_errs_delta": b.in_errs - a.in_errs,
        "estab_resets_delta": b.estab_resets - a.estab_resets,
        "active_opens_delta": b.active_opens - a.active_opens,
    }


def sample(duration_s: float = 5.0) -> dict:
    """Take 2 snapshots `duration_s` apart and return the delta."""
    a = read_snmp()
    time.sleep(duration_s)
    b = read_snmp()
    return snapshot_delta(a, b)


def ss_summary() -> dict:
    """Run `ss -s` and parse the summary lines.

    Output looks like:
      Total: 234
      TCP:   12 (estab 8, closed 2, orphaned 0, timewait 1)
      Transport Total     IP        IPv6
      RAW    0    0    0
      UDP    8    5    3
      TCP    12   10   2
      ...
    """
    ss = shutil.which("ss")
    if not ss:
        return {"error": "ss command not available"}
    try:
        out = subprocess.run(
            [ss, "-s"], capture_output=True, text=True, timeout=3, check=False,
        )
    except Exception as e:
        return {"error": str(e)}
    if out.returncode != 0:
        return {"error": out.stderr.strip()[:200]}

    info: dict = {"raw": out.stdout.strip()}
    for line in out.stdout.splitlines():
        ls = line.strip()
        if ls.startswith("TCP:"):
            # "TCP:   12 (estab 8, closed 2, orphaned 0, timewait 1)"
            import re
            m = re.findall(r"(\w+)\s+(\d+)", ls)
            for k, v in m:
                if k.lower() == "tcp":
                    info["tcp_total"] = int(v)
                else:
                    info[f"tcp_{k.lower()}"] = int(v)
    return info
