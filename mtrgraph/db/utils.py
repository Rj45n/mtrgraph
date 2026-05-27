"""Pure helpers, no DB."""
from __future__ import annotations


def proto_label(protocol: str, dst_port: int | None) -> str:
    """Human-readable label like 'icmp', 'udp:33434', 'tcp:443'."""
    if protocol == "icmp":
        return "icmp"
    return f"{protocol}:{dst_port}" if dst_port is not None else protocol
