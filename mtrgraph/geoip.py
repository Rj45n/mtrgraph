"""GeoIP lookup for MTR hops — uses ipwho.is (free, HTTPS, no API key required).

Cached forever in a local SQLite table. Falls back gracefully when the lookup
service is unavailable.

For higher-volume usage or offline operation, swap the `_lookup_one()` function
to use a MaxMind GeoLite2 mmdb file (~70 MB download + `maxminddb` lib).
"""
from __future__ import annotations

import ipaddress
import json
import socket
import sqlite3
import threading
import urllib.error
import urllib.request
from pathlib import Path

_LOOKUP_URL = "https://ipwho.is/{ip}"
_HTTP_TIMEOUT = 4.0
_lock = threading.Lock()


def _is_routable(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
        return addr.is_global
    except ValueError:
        return False


def _ensure_table(db_path: Path) -> None:
    conn = sqlite3.connect(db_path, timeout=10.0)
    try:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS geoip_cache (
                ip TEXT PRIMARY KEY,
                city TEXT,
                region TEXT,
                country TEXT,
                country_code TEXT,
                lat REAL,
                lng REAL,
                provider TEXT,
                cached_at TEXT
            )"""
        )
        conn.commit()
    finally:
        conn.close()


def _lookup_one(ip: str) -> dict | None:
    """Hit ipwho.is for a single IP. Returns None on any failure."""
    url = _LOOKUP_URL.format(ip=ip)
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "mtrgraph/0.1"})
        with urllib.request.urlopen(req, timeout=_HTTP_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except (urllib.error.URLError, socket.timeout, json.JSONDecodeError, OSError):
        return None
    if not data.get("success", False):
        return None
    return {
        "city": data.get("city"),
        "region": data.get("region"),
        "country": data.get("country"),
        "country_code": data.get("country_code"),
        "lat": data.get("latitude"),
        "lng": data.get("longitude"),
        "provider": (data.get("connection") or {}).get("org"),
    }


def lookup(db_path: Path, ips: list[str], force: bool = False) -> dict[str, dict]:
    """Return {ip: {city, region, country, country_code, lat, lng, provider}}
    for each routable ip. Cached forever on disk."""
    _ensure_table(db_path)
    ips = list({ip for ip in ips if ip and _is_routable(ip)})
    if not ips:
        return {}
    out: dict[str, dict] = {}
    missing: list[str] = []
    conn = sqlite3.connect(db_path, timeout=10.0)
    conn.row_factory = sqlite3.Row
    try:
        for ip in ips:
            if force:
                missing.append(ip)
                continue
            row = conn.execute("SELECT * FROM geoip_cache WHERE ip=?", (ip,)).fetchone()
            if row and row["country"]:
                out[ip] = {
                    "city": row["city"], "region": row["region"],
                    "country": row["country"], "country_code": row["country_code"],
                    "lat": row["lat"], "lng": row["lng"], "provider": row["provider"],
                }
            elif row is None:
                missing.append(ip)
    finally:
        conn.close()

    if missing:
        results: dict[str, dict | None] = {}
        with _lock:
            for ip in missing:
                results[ip] = _lookup_one(ip)
        conn = sqlite3.connect(db_path, timeout=10.0)
        try:
            for ip, r in results.items():
                if r:
                    conn.execute(
                        """INSERT OR REPLACE INTO geoip_cache
                           (ip, city, region, country, country_code, lat, lng, provider, cached_at)
                           VALUES(?,?,?,?,?,?,?,?,datetime('now'))""",
                        (ip, r["city"], r["region"], r["country"], r["country_code"],
                         r["lat"], r["lng"], r["provider"]),
                    )
                    out[ip] = r
                else:
                    # Negative cache to avoid retrying constantly
                    conn.execute(
                        """INSERT OR REPLACE INTO geoip_cache
                           (ip, city, region, country, country_code, lat, lng, provider, cached_at)
                           VALUES(?,NULL,NULL,NULL,NULL,NULL,NULL,NULL,datetime('now'))""",
                        (ip,),
                    )
            conn.commit()
        finally:
            conn.close()
    return out


def enrich_hops_with_geo(db_path: Path, hops: list[dict]) -> list[dict]:
    """Add city/country/lat/lng to each hop dict. Re-uses `resolved_ip` if present
    (from mtr_analysis.enrich_hops_with_as) or extracts it from host."""
    ips = []
    for h in hops:
        ip = h.get("resolved_ip")
        if not ip:
            host = h.get("host") or ""
            try:
                ipaddress.ip_address(host)
                ip = host
            except ValueError:
                ip = None
            h["resolved_ip"] = ip
        if ip and _is_routable(ip):
            ips.append(ip)
    geo = lookup(db_path, ips)
    for h in hops:
        ip = h.get("resolved_ip")
        g = geo.get(ip) if ip else None
        h["geo_city"] = g["city"] if g else None
        h["geo_country"] = g["country"] if g else None
        h["geo_country_code"] = g["country_code"] if g else None
        h["geo_lat"] = g["lat"] if g else None
        h["geo_lng"] = g["lng"] if g else None
        h["geo_provider"] = g["provider"] if g else None
    return hops
