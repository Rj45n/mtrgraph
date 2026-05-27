"""MTR analytics — AS lookups (with on-disk cache) + asymmetric routing detection.

AS lookup uses team-cymru's whois.cymru.com which is rate-limited but free and
well-known. Results are cached forever (an IP→ASN mapping is stable enough for
diagnostic purposes) in a small SQLite table managed here.
"""
from __future__ import annotations

import ipaddress
import re
import socket
import sqlite3
import threading
import time
from pathlib import Path


# ─── AS lookup ────────────────────────────────────────────────────────────

_CYMRU_HOST = "whois.cymru.com"
_CYMRU_PORT = 43
_lock = threading.Lock()
_inflight: set[str] = set()


def _is_routable(ip: str) -> bool:
    """RFC1918 / loopback / link-local don't have a public ASN."""
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_global
    except ValueError:
        return False


def _ensure_asn_table(db_path: Path) -> None:
    conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS asn_cache (
                ip TEXT PRIMARY KEY,
                asn TEXT,
                as_name TEXT,
                country TEXT,
                cached_at TEXT
            )"""
        )
        conn.commit()
    finally:
        conn.close()


def _cymru_lookup(ips: list[str], timeout: float = 5.0) -> dict[str, dict]:
    """Bulk lookup via team-cymru's whois bulk protocol.

    Sends:
        begin
        verbose
        <ip1>
        <ip2>
        ...
        end

    Parses lines like:
        ASN     | IP              | BGP Prefix       | CC | Registry | Allocated  | AS Name
        15169   | 8.8.8.8         | 8.8.8.0/24       | US | arin     | 1992-12-01 | GOOGLE, US
    """
    if not ips:
        return {}
    body = "begin\nverbose\n" + "\n".join(ips) + "\nend\n"
    try:
        with socket.create_connection((_CYMRU_HOST, _CYMRU_PORT), timeout=timeout) as s:
            s.sendall(body.encode("ascii"))
            data = bytearray()
            s.settimeout(timeout)
            while True:
                chunk = s.recv(4096)
                if not chunk:
                    break
                data.extend(chunk)
    except (socket.error, OSError):
        return {}
    out: dict[str, dict] = {}
    for line in data.decode("utf-8", "replace").splitlines():
        if "|" not in line or "Bulk mode" in line or line.startswith("AS "):
            continue
        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 7:
            continue
        asn, ip, prefix, cc, registry, allocated, as_name = parts[:7]
        if not asn or asn.upper() == "NA":
            continue
        out[ip] = {"asn": asn, "as_name": as_name, "country": cc}
    return out


def lookup_asns(db_path: Path, ips: list[str], force: bool = False) -> dict[str, dict]:
    """Return {ip: {asn, as_name, country}} for each ip.

    - Uses SQLite cache (asn_cache table) — never refetches a cached IP.
    - Skips private/loopback IPs (returns None for them).
    - One bulk whois call per missing batch.
    """
    _ensure_asn_table(db_path)
    if not ips:
        return {}
    ips = list({ip for ip in ips if ip and _is_routable(ip)})
    out: dict[str, dict] = {}
    missing: list[str] = []
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        for ip in ips:
            if force:
                missing.append(ip)
                continue
            row = conn.execute(
                "SELECT * FROM asn_cache WHERE ip=?", (ip,),
            ).fetchone()
            if row and row["asn"]:
                out[ip] = {"asn": row["asn"], "as_name": row["as_name"], "country": row["country"]}
            elif row is None:
                missing.append(ip)
    finally:
        conn.close()

    if missing:
        with _lock:
            results = _cymru_lookup(missing)
        conn = sqlite3.connect(db_path, timeout=10.0)
        try:
            for ip in missing:
                r = results.get(ip)
                if r:
                    conn.execute(
                        """INSERT OR REPLACE INTO asn_cache(ip, asn, as_name, country, cached_at)
                           VALUES(?,?,?,?,datetime('now'))""",
                        (ip, r["asn"], r["as_name"], r["country"]),
                    )
                    out[ip] = r
                else:
                    # Negative cache to avoid retrying on every page load
                    conn.execute(
                        """INSERT OR REPLACE INTO asn_cache(ip, asn, as_name, country, cached_at)
                           VALUES(?,NULL,NULL,NULL,datetime('now'))""",
                        (ip,),
                    )
            conn.commit()
        finally:
            conn.close()
    return out


# ─── Asymmetric routing detection ─────────────────────────────────────────

def asymmetry_score(runs_a: list, runs_b: list) -> dict:
    """Compare two MTR run lists (e.g. ICMP vs TCP towards same target) and
    quantify how divergent the paths are.

    Each `runs_*` item is a dict with key `hops` (list of {hop_index, host}).
    Returns {
      hops_a, hops_b: typical hop count of each set,
      common_hops: number of hops with matching host across the two sets,
      divergent_hops: number of (index → different host) mismatches,
      score: 0..1 — 0 = paths identical, 1 = completely divergent,
      details: [{hop_index, a_host, b_host, status}],
    }
    """
    if not runs_a or not runs_b:
        return {"score": None, "reason": "missing data"}

    def typical_hops(runs):
        # Use the most recent run's hop list as representative
        hops = runs[-1].get("hops", [])
        return [
            (h["hop_index"], h.get("host") or "???")
            for h in hops if (h.get("loss_pct") or 0) < 100
        ]

    a = typical_hops(runs_a)
    b = typical_hops(runs_b)
    a_map = dict(a)
    b_map = dict(b)
    all_idx = sorted(set(a_map) | set(b_map))
    common = 0
    divergent = 0
    details = []
    for idx in all_idx:
        ah = a_map.get(idx)
        bh = b_map.get(idx)
        if ah and bh and ah == bh:
            common += 1
            status = "same"
        elif ah and bh:
            divergent += 1
            status = "different"
        else:
            divergent += 1
            status = "only_in_a" if ah and not bh else "only_in_b"
        details.append({
            "hop_index": idx, "a_host": ah, "b_host": bh, "status": status,
        })
    total = common + divergent
    score = divergent / total if total else None
    return {
        "hops_a": len(a), "hops_b": len(b),
        "common_hops": common, "divergent_hops": divergent,
        "score": score,
        "details": details,
    }


# ─── Hop AS-grouping ──────────────────────────────────────────────────────

# Match an IPv4 in a hostname (or just the hostname is an IP)
_IP4_RE = re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})")


def extract_ip(host: str) -> str | None:
    """Best-effort IP extraction from a hostname or raw IP."""
    if not host or host == "???":
        return None
    m = _IP4_RE.search(host)
    if m:
        return m.group(1)
    # Try forward resolution as fallback
    try:
        return socket.gethostbyname(host)
    except (socket.gaierror, OSError):
        return None


def enrich_hops_with_as(db_path: Path, hops: list[dict]) -> list[dict]:
    """Add asn/as_name/country to each hop in-place (returns the same list)."""
    ips = []
    for h in hops:
        ip = extract_ip(h.get("host"))
        h["resolved_ip"] = ip
        if ip and _is_routable(ip):
            ips.append(ip)
    asns = lookup_asns(db_path, ips)
    for h in hops:
        ip = h.get("resolved_ip")
        info = asns.get(ip) if ip else None
        h["asn"] = info["asn"] if info else None
        h["as_name"] = info["as_name"] if info else None
        h["country"] = info["country"] if info else None
    return hops
