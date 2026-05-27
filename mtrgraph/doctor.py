"""Health checks. Highlights problems with traffic-light colors."""
from __future__ import annotations

import os
import shutil
import socket
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path

from rich.console import Console
from rich.table import Table


@dataclass
class Check:
    name: str
    status: str  # "ok" | "warn" | "fail"
    detail: str
    fix: str | None = None


def _color(status: str) -> str:
    return {"ok": "green", "warn": "yellow", "fail": "bold red"}.get(status, "white")


def _icon(status: str) -> str:
    return {"ok": "✓", "warn": "!", "fail": "✗"}.get(status, "?")


def check_mtr_binary() -> Check:
    path = shutil.which("mtr")
    if not path:
        return Check(
            "mtr binary",
            "fail",
            "introuvable dans PATH",
            "apt install mtr  (ou dnf install mtr)",
        )
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=3)
        version = (out.stdout or out.stderr).strip().splitlines()[0]
    except Exception as e:
        return Check("mtr binary", "warn", f"trouvé mais version illisible: {e}")
    return Check("mtr binary", "ok", f"{path} — {version}")


def check_mtr_json() -> Check:
    if not shutil.which("mtr"):
        return Check("mtr -j", "fail", "mtr absent", "voir check ci-dessus")
    try:
        out = subprocess.run(
            ["mtr", "-j", "-c", "1", "1.1.1.1"],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return Check("mtr -j", "warn", "timeout (>15s) — réseau ?")
    if out.returncode != 0:
        return Check(
            "mtr -j", "fail",
            f"exit {out.returncode}: {out.stderr.strip()[:120]}",
            "Vérifier permissions ou ajouter UDP: mtr -u -j ...",
        )
    if not out.stdout.lstrip().startswith("{"):
        return Check(
            "mtr -j", "fail",
            f"sortie non-JSON: {out.stdout[:80]!r}",
            "mtr trop vieux — passer en >=0.86",
        )
    return Check("mtr -j", "ok", "JSON OK sans privilège")


def check_mtr_tcp() -> Check:
    """TCP SYN probe needs cap_net_raw. Test with a 1-cycle TCP probe to localhost router."""
    if not shutil.which("mtr"):
        return Check("mtr -T (TCP)", "fail", "mtr absent")
    try:
        out = subprocess.run(
            ["mtr", "-T", "-P", "80", "-j", "-c", "1", "1.1.1.1"],
            capture_output=True, text=True, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return Check("mtr -T (TCP)", "warn", "timeout (>15s)")
    if out.returncode != 0:
        msg = out.stderr.strip()[:120] or "exit non-zéro"
        return Check(
            "mtr -T (TCP)", "warn",
            f"TCP indisponible: {msg}",
            "sudo setcap cap_net_raw+ep /usr/bin/mtr",
        )
    return Check("mtr -T (TCP)", "ok", "TCP SYN probes OK")


def check_mtr_capabilities() -> Check:
    path = shutil.which("mtr") or "/usr/bin/mtr"
    if not Path(path).exists():
        return Check("mtr capabilities", "warn", "binaire absent, check sauté")
    if os.access(path, os.R_OK) and os.stat(path).st_mode & 0o4000:
        return Check("mtr capabilities", "ok", "setuid root (ICMP autorisé)")
    try:
        out = subprocess.run(["getcap", path], capture_output=True, text=True, timeout=3)
        caps = out.stdout.strip()
    except FileNotFoundError:
        return Check("mtr capabilities", "warn", "getcap absent — install libcap2-bin")
    if "cap_net_raw" in caps:
        return Check("mtr capabilities", "ok", caps)
    return Check(
        "mtr capabilities", "warn",
        "aucune capability ICMP — UDP utilisé par défaut",
        "Pour ICMP: sudo setcap cap_net_raw+ep " + path,
    )


def check_python_deps() -> Check:
    missing = []
    for mod in ("rich", "fastapi", "uvicorn", "jinja2"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return Check(
            "deps python", "fail",
            "manquant: " + ", ".join(missing),
            "pip install -r requirements.txt",
        )
    from importlib.metadata import PackageNotFoundError, version

    def _v(pkg: str) -> str:
        try:
            return version(pkg)
        except PackageNotFoundError:
            return "?"

    return Check(
        "deps python", "ok",
        f"rich={_v('rich')} fastapi={_v('fastapi')} jinja2={_v('jinja2')} uvicorn={_v('uvicorn')}",
    )


def check_db_size(db_path: Path, warn_mb: int = 500, crit_mb: int = 2000) -> Check:
    if not db_path.exists():
        return Check("db size", "ok", "DB pas encore créée")
    sz = db_path.stat().st_size
    mb = sz / (1024 * 1024)
    detail = f"{mb:.1f} MB"
    if mb >= crit_mb:
        return Check("db size", "fail", detail,
                     f"appliquer retention: mtrgraph retention --max-age-days 15 ou moins")
    if mb >= warn_mb:
        return Check("db size", "warn", detail,
                     "envisager mtrgraph retention pour limiter la croissance")
    return Check("db size", "ok", detail)


def check_db(db_path: Path) -> Check:
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        return Check("db parent dir", "fail", str(e), f"mkdir -p {db_path.parent}")
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('runs','hops')"
        )
        tables = {r[0] for r in cur.fetchall()}
        runs_count = 0
        if "runs" in tables:
            runs_count = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
        conn.close()
    except sqlite3.OperationalError as e:
        return Check("db", "fail", f"{db_path}: {e}")
    if not tables:
        return Check(
            "db", "warn", f"{db_path} existe mais vide",
            "Sera initialisée au prochain 'run'",
        )
    if {"runs", "hops"} - tables:
        return Check("db", "warn", f"tables manquantes: {{'runs','hops'}} - {tables}")
    return Check("db", "ok", f"{db_path} · runs={runs_count}")


def check_port(host: str = "127.0.0.1", port: int = 8765) -> Check:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        try:
            s.bind((host, port))
        except OSError as e:
            return Check(
                f"port {host}:{port}", "warn",
                f"occupé ({e}) — utiliser --port autre chose",
            )
    return Check(f"port {host}:{port}", "ok", "libre")


def check_disk_space(db_path: Path, min_mb: int = 100) -> Check:
    try:
        st = shutil.disk_usage(db_path.parent if db_path.parent.exists() else Path("/"))
    except OSError as e:
        return Check("disk", "warn", str(e))
    free_mb = st.free // (1024 * 1024)
    if free_mb < min_mb:
        return Check("disk", "fail", f"libre {free_mb} MB (< {min_mb})", "Faire de la place")
    return Check("disk", "ok", f"libre {free_mb} MB sur {db_path.parent or '/'}")


def check_dns() -> Check:
    try:
        socket.gethostbyname("one.one.one.one")
    except socket.gaierror as e:
        return Check("DNS", "fail", f"résolution KO: {e}")
    return Check("DNS", "ok", "résolution OK (one.one.one.one)")


def check_tcp_retrans() -> Check:
    """Snapshot TCP RetransSegs ratio. Doesn't sample (would block doctor)."""
    try:
        from .tcp_stats import read_snmp
        snap = read_snmp()
    except Exception as e:
        return Check("TCP retrans", "warn", f"indisponible: {e}")
    if snap.out_segs == 0:
        return Check("TCP retrans", "warn", "aucun OutSegs (snapshot vide)")
    ratio = 100.0 * snap.retrans_segs / snap.out_segs
    detail = (
        f"{snap.retrans_segs:,} / {snap.out_segs:,} OutSegs "
        f"({ratio:.3f}% lifetime, voir `mtrgraph tcp-stats --duration 5` pour le live)"
    )
    if ratio >= 1.0:
        return Check("TCP retrans", "warn", detail,
                     "investigate avec `mtrgraph tcp-stats --duration 10 --ss`")
    return Check("TCP retrans", "ok", detail)


def check_https() -> Check:
    """Validate that HTTPS works end-to-end (DNS + TCP + TLS handshake)."""
    from .http_probe import probe_once

    s = probe_once("https://www.cloudflare.com/", method="HEAD", timeout=8.0)
    if s.error:
        return Check("HTTPS probe", "fail", s.error, "vérifier proxy/firewall sortant")
    if s.status and 200 <= s.status < 400:
        return Check(
            "HTTPS probe", "ok",
            f"cloudflare.com {s.status} en {s.total_ms:.0f} ms "
            f"(dns={s.dns_ms:.0f} tcp={s.tcp_ms:.0f} tls={s.tls_ms:.0f} ttfb={s.ttfb_ms:.0f})",
        )
    return Check("HTTPS probe", "warn", f"status inattendu: {s.status}")


def run_all(db_path: Path) -> list[Check]:
    return [
        check_python_deps(),
        check_mtr_binary(),
        check_mtr_json(),
        check_mtr_capabilities(),
        check_mtr_tcp(),
        check_dns(),
        check_https(),
        check_tcp_retrans(),
        check_disk_space(db_path),
        check_db(db_path),
        check_db_size(db_path),
        check_port(),
    ]


def render(console: Console, checks: list[Check]) -> int:
    table = Table(title="mtrgraph doctor", title_style="bold cyan", expand=True)
    table.add_column(" ", width=2)
    table.add_column("Check", style="bold")
    table.add_column("Status", width=6)
    table.add_column("Détail", overflow="fold")
    fails = 0
    warns = 0
    fixes: list[tuple[str, str]] = []
    for c in checks:
        color = _color(c.status)
        if c.status == "fail":
            fails += 1
        elif c.status == "warn":
            warns += 1
        table.add_row(
            f"[{color}]{_icon(c.status)}[/]",
            c.name,
            f"[{color}]{c.status.upper()}[/]",
            c.detail,
        )
        if c.fix:
            fixes.append((c.name, c.fix))
    console.print(table)
    if fixes:
        console.print("\n[bold yellow]Suggestions :[/]")
        for name, fix in fixes:
            console.print(f"  [yellow]→ {name}[/] : {fix}")
    summary_color = "red" if fails else ("yellow" if warns else "green")
    console.print(
        f"\n[bold {summary_color}]"
        f"{len([c for c in checks if c.status=='ok'])} OK · "
        f"{warns} WARN · "
        f"{fails} FAIL[/]"
    )
    return 1 if fails else 0
