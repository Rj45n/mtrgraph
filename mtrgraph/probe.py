import json
import shutil
import subprocess

VALID_PROTOCOLS = ("icmp", "udp", "tcp")

DEFAULT_PORTS = {
    "icmp": None,
    "udp": 33434,
    "tcp": 80,
}


class MtrError(RuntimeError):
    pass


def ensure_mtr() -> str:
    path = shutil.which("mtr")
    if not path:
        raise MtrError("mtr binary not found in PATH (apt install mtr)")
    return path


def resolve_port(protocol: str, port: int | None) -> int | None:
    if protocol == "icmp":
        return None
    return port if port is not None else DEFAULT_PORTS[protocol]


def run_mtr(
    target: str,
    cycles: int = 10,
    interval: float = 1.0,
    timeout: int | None = None,
    protocol: str = "icmp",
    port: int | None = None,
) -> dict:
    """Run `mtr -j` and return parsed JSON.

    protocol:
      - "icmp" (default): ICMP echo. Requires cap_net_raw on the mtr binary.
      - "udp": UDP probes. No privilege required.
      - "tcp": TCP SYN probes. Requires cap_net_raw on the mtr binary.
    port: destination port for udp/tcp (defaults: udp=33434, tcp=80).
    """
    if protocol not in VALID_PROTOCOLS:
        raise MtrError(f"invalid protocol {protocol!r}, expected one of {VALID_PROTOCOLS}")
    mtr = ensure_mtr()
    if timeout is None:
        timeout = int(cycles * interval) + 15
    cmd = [mtr, "-j", "-c", str(cycles), "-i", str(interval)]
    resolved_port = resolve_port(protocol, port)
    if protocol == "udp":
        cmd.append("-u")
        if resolved_port is not None:
            cmd += ["-P", str(resolved_port)]
    elif protocol == "tcp":
        cmd.append("-T")
        if resolved_port is not None:
            cmd += ["-P", str(resolved_port)]
    cmd.append(target)
    try:
        out = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as e:
        raise MtrError(f"mtr timed out after {timeout}s") from e
    if out.returncode != 0:
        raise MtrError(f"mtr exited {out.returncode}: {out.stderr.strip()}")
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError as e:
        raise MtrError(f"failed to parse mtr JSON: {e}") from e


def parse_report(data: dict) -> tuple[str, list[dict]]:
    """Return (src_host, hops_list) from mtr JSON."""
    report = data.get("report", {})
    meta = report.get("mtr", {})
    src = meta.get("src", "")
    hops = []
    for hub in report.get("hubs", []):
        hops.append(
            {
                "hop_index": int(hub.get("count", 0)),
                "host": hub.get("host"),
                "loss_pct": float(hub.get("Loss%", 0.0)),
                "sent": int(hub.get("Snt", 0)),
                "last_ms": float(hub.get("Last", 0.0)),
                "avg_ms": float(hub.get("Avg", 0.0)),
                "best_ms": float(hub.get("Best", 0.0)),
                "worst_ms": float(hub.get("Wrst", 0.0)),
                "stddev_ms": float(hub.get("StDev", 0.0)),
            }
        )
    return src, hops
