import time
from pathlib import Path

from rich.console import Console

from . import db
from .compare import degraded_hops, diff, hops_from_baseline
from .http_probe import aggregate as http_aggregate
from .http_probe import probe_many as http_probe_many
from .http_probe import status_summary as http_status_summary
from .probe import parse_report, resolve_port, run_mtr

# HTTP degradation thresholds: (warn_ratio, crit_ratio, min_delta_ms_to_flag)
HTTP_DEGRADATION = {
    "dns":   (1.5, 3.0, 20),
    "tcp":   (1.5, 3.0, 20),
    "tls":   (1.5, 3.0, 30),
    "ttfb":  (1.5, 3.0, 50),
    "total": (1.5, 3.0, 100),
}


def run_daemon(
    target: str,
    interval_seconds: int,
    cycles: int,
    db_path: Path,
    label: str | None = None,
    baseline_n: int = 10,
    console: Console | None = None,
    protocol: str = "icmp",
    port: int | None = None,
) -> None:
    console = console or Console()
    resolved_port = resolve_port(protocol, port)
    proto_lbl = db.proto_label(protocol, resolved_port)
    console.print(
        f"[bold cyan]Daemon[/] target=[bold]{target}[/] [magenta]{proto_lbl}[/] "
        f"every {interval_seconds}s, cycles={cycles}, baseline={baseline_n} runs"
    )
    while True:
        try:
            data = run_mtr(target, cycles=cycles, protocol=protocol, port=port)
            src, hops = parse_report(data)
            with db.session(db_path) as conn:
                baseline = db.baseline_hops(
                    conn, target, protocol=protocol, dst_port=resolved_port,
                    last_n=baseline_n,
                )
                run_id = db.insert_run(
                    conn, target, cycles, label, src,
                    protocol=protocol, dst_port=resolved_port,
                )
                db.insert_hops(conn, run_id, hops)
                db.finalize_run(conn, run_id)

            ts = time.strftime("%H:%M:%S")
            if baseline:
                deltas = diff(hops_from_baseline(baseline), hops)
                bad = degraded_hops(deltas)
                if bad:
                    console.print(
                        f"[bold red]\\[{ts}] DEGRADATION[/] run #{run_id} "
                        f"on {target} [magenta]{proto_lbl}[/]"
                    )
                    for d in bad:
                        console.print(
                            f"  hop {d.hop_index} {d.host}  "
                            f"avg {d.avg_a} → {d.avg_b}  loss {d.loss_a} → {d.loss_b}  "
                            f"[bold]{d.severity}[/]"
                        )
                else:
                    console.print(
                        f"[green]\\[{ts}] OK[/] run #{run_id} "
                        f"(vs baseline de {baseline_n} runs max)"
                    )
            else:
                console.print(f"[cyan]\\[{ts}] baseline run[/] #{run_id} stored")
        except Exception as exc:
            console.print(f"[bold red]error:[/] {exc}")

        time.sleep(interval_seconds)


def _http_degradation(current_avg: float | None, baseline_avg: float | None, stage: str) -> str | None:
    """Return 'critical', 'warning' or None."""
    if current_avg is None or baseline_avg is None:
        return None
    warn_ratio, crit_ratio, min_delta = HTTP_DEGRADATION[stage]
    delta = current_avg - baseline_avg
    if delta < min_delta:
        return None
    if baseline_avg > 0 and current_avg >= baseline_avg * crit_ratio:
        return "critical"
    if baseline_avg > 0 and current_avg >= baseline_avg * warn_ratio:
        return "warning"
    return None


def run_http_daemon(
    url: str,
    interval_seconds: int,
    count: int,
    db_path: Path,
    method: str = "HEAD",
    timeout: float = 10.0,
    label: str | None = None,
    baseline_n: int = 10,
    error_threshold_pct: float = 10.0,
    force_ip: str | None = None,
    console: Console | None = None,
) -> None:
    console = console or Console()
    ip_info = f" forced→{force_ip}" if force_ip else ""
    console.print(
        f"[bold cyan]HTTP daemon[/] [bold]{url}[/]{ip_info} "
        f"every {interval_seconds}s, {count} samples, {method}, "
        f"baseline last {baseline_n} runs"
    )
    while True:
        try:
            samples = http_probe_many(
                url, count=count, method=method, timeout=timeout,
                interval=min(0.5, interval_seconds / max(count, 1)),
                force_ip=force_ip,
            )
            agg = http_aggregate(samples)
            summary = http_status_summary(agg["status_counts"])
            resolved_ip = next((s.resolved_ip for s in samples if s.resolved_ip), None)
            errors = agg["errors"]
            err_pct = (errors / max(len(samples), 1)) * 100

            with db.session(db_path) as conn:
                baseline = db.http_baseline(conn, url, last_n=baseline_n)
                run_id = db.insert_http_run(
                    conn, url, method, len(samples), label, resolved_ip, summary, errors,
                )
                db.insert_http_samples(conn, run_id, samples)
                db.finalize_http_run(conn, run_id)

            ts = time.strftime("%H:%M:%S")
            problems: list[str] = []
            if err_pct >= error_threshold_pct:
                problems.append(f"[bold red]ERRORS {err_pct:.0f}%[/] ({errors}/{len(samples)})")
            if baseline:
                for stage in ("dns", "tcp", "tls", "ttfb", "total"):
                    cur = agg[stage]["avg"]
                    base = baseline.get(stage, {}).get("avg_ms")
                    sev = _http_degradation(cur, base, stage)
                    if sev:
                        color = "bold red" if sev == "critical" else "yellow"
                        problems.append(
                            f"[{color}]{stage.upper()} {sev}[/] "
                            f"{base:.0f}→{cur:.0f} ms"
                        )

            if problems:
                console.print(
                    f"[bold red]\\[{ts}] DEGRADATION[/] http_run #{run_id} {url} → "
                    + " · ".join(problems)
                    + f"  [grey50]status={summary}[/]"
                )
            elif baseline:
                console.print(
                    f"[green]\\[{ts}] OK[/] http_run #{run_id} "
                    f"total={agg['total']['avg']:.0f} ms  status={summary}"
                )
            else:
                console.print(
                    f"[cyan]\\[{ts}] baseline run[/] #{run_id} stored  "
                    f"total={agg['total']['avg']:.0f} ms  status={summary}"
                )
        except Exception as exc:
            console.print(f"[bold red]http daemon error:[/] {exc}")

        time.sleep(interval_seconds)
