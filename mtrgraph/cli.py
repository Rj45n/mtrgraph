import argparse
import os
import sys
from pathlib import Path

from rich.console import Console

from . import db
from .compare import hops_from_baseline
from .daemon import run_daemon, run_http_daemon
from .doctor import render as doctor_render
from .doctor import run_all as doctor_run_all
from . import retention, s3_bench, s3_client, tcp_stats
from .http_probe import aggregate as http_aggregate
from .http_probe import probe_many as http_probe_many
from .http_probe import status_summary as http_status_summary
from .probe import VALID_PROTOCOLS, MtrError, parse_report, resolve_port, run_mtr
from .tui import (
    diff_table,
    http_legend,
    http_samples_table,
    http_summary_table,
    print_run,
)
from .web import serve


def _add_db_arg(p: argparse.ArgumentParser) -> None:
    p.add_argument("--db", type=Path, default=db.DEFAULT_DB, help="SQLite path")


def cmd_run(args: argparse.Namespace, console: Console) -> int:
    port = resolve_port(args.proto, args.port)
    proto_lbl = db.proto_label(args.proto, port)
    console.print(f"[cyan]▶ mtr[/] {args.target} [magenta]{proto_lbl}[/] (cycles={args.cycles})")
    try:
        data = run_mtr(
            args.target,
            cycles=args.cycles,
            interval=args.interval,
            protocol=args.proto,
            port=args.port,
        )
    except MtrError as e:
        console.print(f"[bold red]erreur:[/] {e}")
        return 2
    src, hops = parse_report(data)
    with db.session(args.db) as conn:
        run_id = db.insert_run(
            conn, args.target, args.cycles, args.label, src,
            protocol=args.proto, dst_port=port,
        )
        db.insert_hops(conn, run_id, hops)
        db.finalize_run(conn, run_id)
    console.print(f"[green]✓ run #{run_id} sauvegardé[/]")
    print_run(
        console, args.target, src, hops,
        title=f"Run #{run_id} — {args.target}  {proto_lbl}",
    )
    return 0


def cmd_list(args: argparse.Namespace, console: Console) -> int:
    with db.session(args.db) as conn:
        runs = db.list_runs(conn, target=args.target, limit=args.limit)
    if not runs:
        console.print("[yellow]aucun run[/]")
        return 0
    for r in runs:
        proto_lbl = db.proto_label(r["protocol"] or "icmp", r["dst_port"])
        console.print(
            f"[bold]#{r['id']:>4}[/]  {r['started_at']}  [cyan]{r['target']:<25}[/]  "
            f"[magenta]{proto_lbl:<10}[/]  cycles={r['cycles']:<3}  label={r['label'] or '-'}"
        )
    return 0


def cmd_show(args: argparse.Namespace, console: Console) -> int:
    with db.session(args.db) as conn:
        run = db.get_run(conn, args.run_id)
        if not run:
            console.print(f"[bold red]run #{args.run_id} introuvable[/]")
            return 1
        hops = [dict(h) for h in db.get_hops(conn, args.run_id)]
    proto_lbl = db.proto_label(run["protocol"] or "icmp", run["dst_port"])
    print_run(
        console,
        run["target"],
        run["src"] or "?",
        hops,
        title=f"Run #{run['id']} — {run['target']}  {proto_lbl} — {run['started_at']}",
    )
    return 0


def cmd_compare(args: argparse.Namespace, console: Console) -> int:
    with db.session(args.db) as conn:
        if args.baseline:
            run_b = db.get_run(conn, args.b)
            if not run_b:
                console.print(f"[bold red]run #{args.b} introuvable[/]")
                return 1
            proto = run_b["protocol"] or "icmp"
            dst_port = run_b["dst_port"]
            baseline = db.baseline_hops(
                conn, run_b["target"], protocol=proto, dst_port=dst_port,
                last_n=args.baseline_n,
            )
            if not baseline:
                console.print(
                    f"[yellow]pas de baseline pour {run_b['target']} "
                    f"{db.proto_label(proto, dst_port)}[/]"
                )
                return 1
            hops_a = hops_from_baseline(baseline)
            hops_b = [dict(h) for h in db.get_hops(conn, args.b)]
            label_a, label_b = f"baseline(<={args.baseline_n} runs)", f"#{args.b}"
            target = f"{run_b['target']}  {db.proto_label(proto, dst_port)}"
        else:
            run_a = db.get_run(conn, args.a)
            run_b = db.get_run(conn, args.b)
            if not run_a or not run_b:
                console.print("[bold red]un des runs est introuvable[/]")
                return 1
            if (
                run_a["target"] != run_b["target"]
                or (run_a["protocol"] or "icmp") != (run_b["protocol"] or "icmp")
                or run_a["dst_port"] != run_b["dst_port"]
            ):
                console.print(
                    "[yellow]attention :[/] runs sur (target, proto, port) différents — "
                    "diff peu pertinent"
                )
            hops_a = [dict(h) for h in db.get_hops(conn, args.a)]
            hops_b = [dict(h) for h in db.get_hops(conn, args.b)]
            label_a, label_b = f"#{args.a}", f"#{args.b}"
            proto_b = db.proto_label(run_b["protocol"] or "icmp", run_b["dst_port"])
            target = f"{run_b['target']}  {proto_b}"
    console.print(diff_table(target, hops_a, hops_b, label_a, label_b))
    return 0


def cmd_daemon(args: argparse.Namespace, console: Console) -> int:
    try:
        run_daemon(
            target=args.target,
            interval_seconds=args.every,
            cycles=args.cycles,
            db_path=args.db,
            label=args.label,
            baseline_n=args.baseline_n,
            console=console,
            protocol=args.proto,
            port=args.port,
        )
    except KeyboardInterrupt:
        console.print("[yellow]daemon arrêté[/]")
    return 0


def cmd_web(args: argparse.Namespace, console: Console) -> int:
    console.print(f"[cyan]▶ web[/] http://{args.host}:{args.port}  db={args.db}")
    serve(args.db, host=args.host, port=args.port)
    return 0


def cmd_http(args: argparse.Namespace, console: Console) -> int:
    ip_info = f" forced→{args.ip}" if args.ip else ""
    console.print(
        f"[cyan]▶ http[/] {args.method} {args.url}{ip_info} "
        f"(samples={args.count}, timeout={args.timeout}s)"
    )
    samples = http_probe_many(
        args.url, count=args.count, method=args.method,
        timeout=args.timeout, interval=args.interval, force_ip=args.ip,
    )
    agg = http_aggregate(samples)
    summary = http_status_summary(agg["status_counts"])
    resolved_ip = next((s.resolved_ip for s in samples if s.resolved_ip), None)
    tls_meta = db.tls_meta_from_samples(samples)
    with db.session(args.db) as conn:
        run_id = db.insert_http_run(
            conn, args.url, args.method, len(samples), args.label,
            resolved_ip, summary, agg["errors"],
            tls_meta=tls_meta,
        )
        db.insert_http_samples(conn, run_id, samples)
        db.finalize_http_run(conn, run_id)
    # Auto-MTR towards resolved IP (alimente la courbe RTT du dashboard)
    if not getattr(args, "no_mtr", False) and resolved_ip:
        from .scheduler import trigger_auto_mtr
        trigger_auto_mtr(resolved_ip, args.db)
    console.print(
        f"[green]✓ http_run #{run_id} sauvegardé[/]  "
        f"ip={resolved_ip or '?'}  status={summary}  errors={agg['errors']}"
    )
    console.print(http_summary_table(agg, title=f"HTTP #{run_id} — {args.url}"))
    if args.verbose:
        console.print(http_samples_table(
            args.url, [s.__dict__ for s in samples],
            title=f"Samples #{run_id}",
        ))
    console.print(http_legend())
    return 0


def cmd_http_daemon(args: argparse.Namespace, console: Console) -> int:
    try:
        run_http_daemon(
            url=args.url,
            interval_seconds=args.every,
            count=args.count,
            db_path=args.db,
            method=args.method,
            timeout=args.timeout,
            label=args.label,
            baseline_n=args.baseline_n,
            error_threshold_pct=args.error_threshold,
            force_ip=args.ip,
            console=console,
        )
    except KeyboardInterrupt:
        console.print("[yellow]daemon HTTP arrêté[/]")
    return 0


def cmd_http_list(args: argparse.Namespace, console: Console) -> int:
    with db.session(args.db) as conn:
        runs = db.list_http_runs(conn, url=args.url, limit=args.limit)
    if not runs:
        console.print("[yellow]aucun http_run[/]")
        return 0
    for r in runs:
        console.print(
            f"[bold]#{r['id']:>4}[/]  {r['started_at']}  "
            f"[cyan]{r['url']:<50}[/]  status={r['status_summary'] or '-'}  "
            f"errors={r['errors']}  label={r['label'] or '-'}"
        )
    return 0


def cmd_http_show(args: argparse.Namespace, console: Console) -> int:
    with db.session(args.db) as conn:
        run = db.get_http_run(conn, args.run_id)
        if not run:
            console.print(f"[bold red]http_run #{args.run_id} introuvable[/]")
            return 1
        samples = [dict(s) for s in db.get_http_samples(conn, args.run_id)]
    # Re-aggregate from stored samples
    from .http_probe import HttpSample
    sample_objs = [
        HttpSample(
            s["sample_idx"], s["dns_ms"], s["tcp_ms"], s["tls_ms"],
            s["ttfb_ms"], s["total_ms"], s["status"], None, s["error"],
        )
        for s in samples
    ]
    agg = http_aggregate(sample_objs)
    console.print(http_summary_table(
        agg, title=f"HTTP #{run['id']} — {run['url']} — {run['started_at']}",
    ))
    console.print(http_samples_table(run["url"], samples, title="Samples"))
    console.print(http_legend())
    return 0


def _resolve_creds(args) -> tuple[str, str, str | None]:
    """Pull AWS-style credentials from args/env."""
    ak = args.access_key or os.environ.get("AWS_ACCESS_KEY_ID")
    sk = args.secret_key or os.environ.get("AWS_SECRET_ACCESS_KEY")
    tk = getattr(args, "session_token", None) or os.environ.get("AWS_SESSION_TOKEN")
    if not ak or not sk:
        raise SystemExit(
            "credentials manquants — fournir --access-key/--secret-key "
            "ou AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY"
        )
    return ak, sk, tk


def _print_s3_result(console: Console, result, run_id: int | None = None):
    from rich.table import Table
    from .colors import http_color, http_status_color
    from .db import proto_label  # noqa: just to avoid lint issues

    title = f"S3 {result.operation.upper()}"
    if run_id is not None:
        title = f"S3 #{run_id} — {result.operation.upper()}"
    if result.bucket:
        title += f"  {result.bucket}"
        if result.key:
            title += f"/{result.key}"

    table = Table(title=title, title_style="bold cyan", header_style="bold magenta", expand=True)
    table.add_column("Champ", style="bold")
    table.add_column("Valeur")

    def cell(v, color="white"):
        from rich.text import Text
        return Text(str(v) if v is not None else "-", style=color)

    status_col = http_status_color(result.http_status)
    table.add_row("Endpoint", cell(result.endpoint))
    table.add_row("IP", cell(result.resolved_ip))
    table.add_row("Status", cell(result.http_status, status_col))
    table.add_row("DNS ms", cell(f"{result.dns_ms:.1f}" if result.dns_ms is not None else "-",
                                  http_color("dns", result.dns_ms)))
    table.add_row("TCP ms", cell(f"{result.tcp_ms:.1f}" if result.tcp_ms is not None else "-",
                                  http_color("tcp", result.tcp_ms)))
    table.add_row("TLS ms", cell(f"{result.tls_ms:.1f}" if result.tls_ms is not None else "-",
                                  http_color("tls", result.tls_ms)))
    table.add_row("TTFB ms", cell(f"{result.ttfb_ms:.1f}" if result.ttfb_ms is not None else "-",
                                   http_color("ttfb", result.ttfb_ms)))
    table.add_row("Total ms", cell(f"{result.duration_ms:.1f}" if result.duration_ms is not None else "-",
                                    http_color("total", result.duration_ms)))
    table.add_row("Bytes", cell(result.bytes_transferred))
    if result.response_summary:
        table.add_row("Résumé", cell(result.response_summary, "cyan"))
    if result.error:
        table.add_row("Erreur", cell(result.error, "bold red"))
    console.print(table)


def _store_s3(args, result, console):
    with db.session(args.db) as conn:
        run_id = db.insert_s3_run(conn, result)
    _print_s3_result(console, result, run_id=run_id)
    return run_id


def cmd_s3_list(args, console):
    ak, sk, tk = _resolve_creds(args)
    result = s3_client.list_bucket(
        args.endpoint, args.bucket, ak, sk, args.region,
        prefix=args.prefix or "", max_keys=args.max_keys,
        session_token=tk, timeout=args.timeout, label=args.label,
    )
    _store_s3(args, result, console)
    return 0 if result.error is None and (result.http_status or 0) < 400 else 1


def cmd_s3_head(args, console):
    ak, sk, tk = _resolve_creds(args)
    result = s3_client.head_object(
        args.endpoint, args.bucket, args.key, ak, sk, args.region,
        session_token=tk, timeout=args.timeout, label=args.label,
    )
    _store_s3(args, result, console)
    return 0 if result.error is None and (result.http_status or 0) < 400 else 1


def cmd_s3_get(args, console):
    ak, sk, tk = _resolve_creds(args)
    result = s3_client.get_object(
        args.endpoint, args.bucket, args.key, ak, sk, args.region,
        session_token=tk, timeout=args.timeout, label=args.label,
    )
    _store_s3(args, result, console)
    return 0 if result.error is None and (result.http_status or 0) < 400 else 1


def cmd_s3_put(args, console):
    ak, sk, tk = _resolve_creds(args)
    if args.file:
        body = Path(args.file).read_bytes()
    elif args.size_kb:
        import os as _os
        body = _os.urandom(args.size_kb * 1024)
    else:
        body = b"mtrgraph test payload"
    result = s3_client.put_object(
        args.endpoint, args.bucket, args.key, body, ak, sk, args.region,
        session_token=tk, timeout=args.timeout, label=args.label,
        content_type=args.content_type,
    )
    _store_s3(args, result, console)
    return 0 if result.error is None and (result.http_status or 0) < 400 else 1


def cmd_s3_delete(args, console):
    ak, sk, tk = _resolve_creds(args)
    result = s3_client.delete_object(
        args.endpoint, args.bucket, args.key, ak, sk, args.region,
        session_token=tk, timeout=args.timeout, label=args.label,
    )
    _store_s3(args, result, console)
    return 0 if result.error is None and (result.http_status or 0) < 400 else 1


def cmd_s3_bench(args, console):
    ak, sk, tk = _resolve_creds(args)
    console.print(
        f"[cyan]▶ s3-bench[/] {args.operation.upper()} {args.endpoint}/{args.bucket} "
        f"concurrency={args.concurrency} count={args.count} size={args.size_kb}KiB"
    )
    def progress(done, total):
        if done % max(1, total // 10) == 0 or done == total:
            console.print(f"  {done}/{total}", end="\r")
    summary = s3_bench.run_bench(
        operation=args.operation,
        endpoint=args.endpoint, bucket=args.bucket,
        access_key=ak, secret_key=sk, region=args.region,
        key_or_prefix=args.key_or_prefix,
        concurrency=args.concurrency, count=args.count,
        object_size_kb=args.size_kb,
        session_token=tk, timeout=args.timeout, label=args.label or "bench",
        db_path=args.db, progress_fn=progress, track_puts=not args.no_track,
    )
    from rich.table import Table
    t = Table(title=f"s3-bench {summary.operation.upper()}", title_style="bold cyan", header_style="bold magenta", expand=True)
    t.add_column("Champ", style="bold"); t.add_column("Valeur")
    t.add_row("Endpoint", summary.endpoint)
    t.add_row("Bucket", summary.bucket)
    t.add_row("Concurrency", str(summary.concurrency))
    t.add_row("Ops total", str(summary.total_ops))
    t.add_row("Ops réussies", str(summary.successful_ops))
    t.add_row("Erreurs", str(summary.errors))
    t.add_row("Bytes transférés", f"{summary.total_bytes:,}")
    t.add_row("Wall time", f"{summary.total_wall_s:.2f} s")
    t.add_row("Throughput", f"[bold green]{summary.throughput_mbps:.2f} MB/s[/]")
    t.add_row("Ops/sec", f"{summary.ops_per_sec:.1f}")
    if summary.p50_ms is not None:
        t.add_row("Latence p50", f"{summary.p50_ms:.1f} ms")
        t.add_row("Latence p95", f"{summary.p95_ms:.1f} ms")
        t.add_row("Latence p99", f"{summary.p99_ms:.1f} ms")
        t.add_row("Latence min/avg/max", f"{summary.min_ms:.1f} / {summary.avg_ms:.1f} / {summary.max_ms:.1f} ms")
    console.print(t)
    return 0 if summary.errors == 0 else 1


def cmd_s3_list_runs(args, console):
    with db.session(args.db) as conn:
        runs = db.list_s3_runs(conn, endpoint=args.endpoint, operation=args.operation, limit=args.limit)
    if not runs:
        console.print("[yellow]aucun s3_run[/]")
        return 0
    for r in runs:
        from .colors import http_status_color
        status_col = http_status_color(r["http_status"])
        target = f"{r['bucket'] or ''}/{r['key'] or ''}".strip("/")
        console.print(
            f"[bold]#{r['id']:>4}[/]  {r['started_at']}  "
            f"[cyan]{r['operation'].upper():<6}[/] "
            f"[{status_col}]{r['http_status'] or 'ERR':<4}[/]  "
            f"{r['duration_ms']:>7.1f} ms  "
            f"{target:<40}  "
            f"label={r['label'] or '-'}  "
            f"{r['response_summary'] or r['error'] or ''}"
        )
    return 0


def cmd_tcp_stats(args, console):
    try:
        if args.duration <= 0:
            snap = tcp_stats.read_snmp()
            console.print(
                f"[bold cyan]Snapshot TCP[/]  "
                f"OutSegs={snap.out_segs:,}  RetransSegs={snap.retrans_segs:,}  "
                f"InSegs={snap.in_segs:,}  InErrs={snap.in_errs:,}  "
                f"ActiveOpens={snap.active_opens:,}  EstabResets={snap.estab_resets:,}"
            )
        else:
            console.print(f"[cyan]▶ tcp-stats[/] sampling pendant {args.duration}s …")
            d = tcp_stats.sample(args.duration)
            color = "red" if d["retrans_pct"] >= 1.0 else "yellow" if d["retrans_pct"] >= 0.1 else "green"
            console.print(
                f"[bold]Retrans: [{color}]{d['retrans_pct']:.3f}%[/]  "
                f"({d['retrans_segs_per_s']:.1f} retrans/s sur {d['out_segs_per_s']:.0f} segs/s)"
            )
            console.print(
                f"In: {d['in_segs_per_s']:.0f} segs/s · "
                f"InErrs delta: {d['in_errs_delta']} · "
                f"EstabResets delta: {d['estab_resets_delta']} · "
                f"ActiveOpens delta: {d['active_opens_delta']}"
            )
        if args.ss:
            ss = tcp_stats.ss_summary()
            console.print("[bold]ss -s :[/]")
            console.print(ss.get("raw", ss.get("error", "?")))
    except Exception as e:
        console.print(f"[bold red]erreur:[/] {e}")
        return 1
    return 0


def cmd_retention(args, console):
    if args.dry_run:
        s = retention.db_stats(args.db)
        console.print(f"[bold cyan]DB stats[/] {args.db}")
        console.print(f"  size: {s['size_bytes']/1024/1024:.2f} MB")
        for k, v in s.items():
            if k.startswith("rows_") and v is not None:
                console.print(f"  {k}: {v:,}")
            elif k.startswith("oldest_"):
                console.print(f"  {k}: {v}")
        return 0
    console.print(f"[cyan]▶ retention[/] max_age={args.max_age_days}d  vacuum={not args.no_vacuum}")
    stats = retention.apply_retention(
        args.db, max_age_days=args.max_age_days, vacuum=not args.no_vacuum,
    )
    console.print(
        f"[green]✓[/] runs={stats.runs_deleted} http={stats.http_runs_deleted} "
        f"s3={stats.s3_runs_deleted} tcp={stats.tcp_samples_deleted} "
        f"tracked={stats.tracked_purged}  "
        f"freed=[bold]{stats.bytes_freed/1024/1024:.1f} MB[/]  "
        f"size après=[bold]{stats.bytes_after/1024/1024:.1f} MB[/]  "
        f"({stats.duration_s:.2f}s)"
    )
    return 0


def cmd_doctor(args: argparse.Namespace, console: Console) -> int:
    return doctor_render(console, doctor_run_all(args.db))


def cmd_delete(args: argparse.Namespace, console: Console) -> int:
    with db.session(args.db) as conn:
        db.delete_run(conn, args.run_id)
    console.print(f"[green]✓ run #{args.run_id} supprimé[/]")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mtrgraph", description="MTR graphique + DB + web")
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="Lance un MTR et le stocke")
    r.add_argument("target")
    r.add_argument("-c", "--cycles", type=int, default=10)
    r.add_argument("-i", "--interval", type=float, default=1.0)
    r.add_argument("--label", default=None)
    r.add_argument(
        "--proto", choices=VALID_PROTOCOLS, default="icmp",
        help="protocole de probe (défaut: icmp)",
    )
    r.add_argument(
        "--port", type=int, default=None,
        help="port destination pour udp/tcp (défaut udp=33434, tcp=80)",
    )
    _add_db_arg(r)
    r.set_defaults(func=cmd_run)

    l = sub.add_parser("list", help="Liste les runs")
    l.add_argument("--target", default=None)
    l.add_argument("--limit", type=int, default=50)
    _add_db_arg(l)
    l.set_defaults(func=cmd_list)

    s = sub.add_parser("show", help="Affiche un run")
    s.add_argument("run_id", type=int)
    _add_db_arg(s)
    s.set_defaults(func=cmd_show)

    c = sub.add_parser("compare", help="Compare deux runs (ou un run vs baseline)")
    c.add_argument("a", type=int, nargs="?", help="run A (ignoré si --baseline)")
    c.add_argument("b", type=int, help="run B")
    c.add_argument("--baseline", action="store_true", help="compare B vs médiane des derniers runs")
    c.add_argument("--baseline-n", type=int, default=10)
    _add_db_arg(c)
    c.set_defaults(func=cmd_compare)

    d = sub.add_parser("daemon", help="Lance MTR en boucle avec alertes")
    d.add_argument("target")
    d.add_argument("--every", type=int, default=300, help="intervalle en secondes")
    d.add_argument("-c", "--cycles", type=int, default=10)
    d.add_argument("--label", default="daemon")
    d.add_argument("--baseline-n", type=int, default=10)
    d.add_argument(
        "--proto", choices=VALID_PROTOCOLS, default="icmp",
        help="protocole de probe (défaut: icmp)",
    )
    d.add_argument(
        "--port", type=int, default=None,
        help="port destination pour udp/tcp",
    )
    _add_db_arg(d)
    d.set_defaults(func=cmd_daemon)

    w = sub.add_parser("web", help="Lance l'interface web")
    w.add_argument("--host", default="127.0.0.1")
    w.add_argument("--port", type=int, default=8765)
    _add_db_arg(w)
    w.set_defaults(func=cmd_web)

    h = sub.add_parser("http", help="Probe HTTP : DNS/TCP/TLS/TTFB/total")
    h.add_argument("url", help="URL complète ex. https://s3.eu-west-3.amazonaws.com/")
    h.add_argument("-c", "--count", type=int, default=10, help="nb de samples")
    h.add_argument("-m", "--method", default="HEAD", choices=["HEAD", "GET"])
    h.add_argument("-i", "--interval", type=float, default=0.5, help="pause entre samples (s)")
    h.add_argument("-T", "--timeout", type=float, default=10.0)
    h.add_argument("--label", default=None)
    h.add_argument("--ip", default=None, help="force la résolution sur cette IP (garde SNI/Host du hostname)")
    h.add_argument("--no-mtr", action="store_true",
                   help="ne pas déclencher d'auto-MTR vers l'IP résolue après le probe")
    h.add_argument("-v", "--verbose", action="store_true", help="affiche aussi le détail par sample")
    _add_db_arg(h)
    h.set_defaults(func=cmd_http)

    hd = sub.add_parser("http-daemon", help="Probe HTTP en boucle avec alerte vs baseline")
    hd.add_argument("url")
    hd.add_argument("--every", type=int, default=60, help="intervalle en secondes (défaut 60)")
    hd.add_argument("-c", "--count", type=int, default=5, help="samples par itération")
    hd.add_argument("-m", "--method", default="HEAD", choices=["HEAD", "GET"])
    hd.add_argument("-T", "--timeout", type=float, default=10.0)
    hd.add_argument("--label", default="http-daemon")
    hd.add_argument("--baseline-n", type=int, default=10)
    hd.add_argument("--error-threshold", type=float, default=10.0,
                    help="alerter si %% d'erreurs dépasse ce seuil (défaut 10)")
    hd.add_argument("--ip", default=None, help="forcer une IP (garde SNI/Host du hostname)")
    _add_db_arg(hd)
    hd.set_defaults(func=cmd_http_daemon)

    hl = sub.add_parser("http-list", help="Liste les http_runs")
    hl.add_argument("--url", default=None)
    hl.add_argument("--limit", type=int, default=50)
    _add_db_arg(hl)
    hl.set_defaults(func=cmd_http_list)

    hs = sub.add_parser("http-show", help="Affiche un http_run")
    hs.add_argument("run_id", type=int)
    _add_db_arg(hs)
    hs.set_defaults(func=cmd_http_show)

    # ─── S3 (SigV4) ───────────────────────────────────────────────────────
    def _add_s3_common(p):
        p.add_argument("--endpoint", required=True,
                       help="https://s3.region.exemple.com (sans le bucket)")
        p.add_argument("--region", default="us-east-1",
                       help="région AWS — souvent ignorée par les MinIO mais doit être set")
        p.add_argument("--access-key", default=None,
                       help="ou AWS_ACCESS_KEY_ID")
        p.add_argument("--secret-key", default=None,
                       help="ou AWS_SECRET_ACCESS_KEY")
        p.add_argument("--session-token", default=None,
                       help="ou AWS_SESSION_TOKEN (STS)")
        p.add_argument("-T", "--timeout", type=float, default=30.0)
        p.add_argument("--label", default=None)
        _add_db_arg(p)

    s3l = sub.add_parser("s3-list", help="S3 LIST bucket (souvent la cause #1 de lenteur)")
    s3l.add_argument("--bucket", required=True)
    s3l.add_argument("--prefix", default="")
    s3l.add_argument("--max-keys", type=int, default=1000)
    _add_s3_common(s3l)
    s3l.set_defaults(func=cmd_s3_list)

    s3h = sub.add_parser("s3-head", help="S3 HEAD object (check d'existence)")
    s3h.add_argument("--bucket", required=True)
    s3h.add_argument("--key", required=True)
    _add_s3_common(s3h)
    s3h.set_defaults(func=cmd_s3_head)

    s3g = sub.add_parser("s3-get", help="S3 GET object (mesure TTFB + débit)")
    s3g.add_argument("--bucket", required=True)
    s3g.add_argument("--key", required=True)
    _add_s3_common(s3g)
    s3g.set_defaults(func=cmd_s3_get)

    s3p = sub.add_parser("s3-put", help="S3 PUT object")
    s3p.add_argument("--bucket", required=True)
    s3p.add_argument("--key", required=True)
    s3p.add_argument("--file", default=None, help="chemin local d'un fichier à uploader")
    s3p.add_argument("--size-kb", type=int, default=None,
                     help="générer N kio aléatoires si --file absent")
    s3p.add_argument("--content-type", default="application/octet-stream")
    _add_s3_common(s3p)
    s3p.set_defaults(func=cmd_s3_put)

    s3d = sub.add_parser("s3-delete", help="S3 DELETE object")
    s3d.add_argument("--bucket", required=True)
    s3d.add_argument("--key", required=True)
    _add_s3_common(s3d)
    s3d.set_defaults(func=cmd_s3_delete)

    s3b = sub.add_parser("s3-bench", help="Benchmark concurrent S3 GET/PUT")
    s3b.add_argument("operation", choices=["get", "put"])
    s3b.add_argument("--bucket", required=True)
    s3b.add_argument("--key-or-prefix", default="mtrgraph-bench/",
                     help="GET: clé exacte à fetcher | PUT: prefix sous lequel créer")
    s3b.add_argument("--concurrency", type=int, default=10)
    s3b.add_argument("--count", type=int, default=100)
    s3b.add_argument("--size-kb", type=int, default=64, help="taille des objets PUT")
    s3b.add_argument("--no-track", action="store_true",
                     help="ne pas enregistrer les objets PUT dans s3_tracked_objects (à tes risques)")
    _add_s3_common(s3b)
    s3b.set_defaults(func=cmd_s3_bench)

    s3ls = sub.add_parser("s3-runs", help="Liste les s3_runs enregistrés")
    s3ls.add_argument("--endpoint", default=None)
    s3ls.add_argument("--operation", default=None,
                      choices=["list", "head", "get", "put", "delete"])
    s3ls.add_argument("--limit", type=int, default=50)
    _add_db_arg(s3ls)
    s3ls.set_defaults(func=cmd_s3_list_runs)

    ret = sub.add_parser("retention", help="Purge les vieilles données + VACUUM")
    ret.add_argument("--max-age-days", type=int, default=30)
    ret.add_argument("--no-vacuum", action="store_true")
    ret.add_argument("--dry-run", action="store_true",
                     help="affiche les stats DB sans rien supprimer")
    _add_db_arg(ret)
    ret.set_defaults(func=cmd_retention)

    tcp = sub.add_parser("tcp-stats", help="Compteurs TCP (RetransSegs etc.) depuis /proc/net/snmp")
    tcp.add_argument("--duration", type=float, default=5.0,
                     help="durée du sampling en secondes (0 = snapshot instantané)")
    tcp.add_argument("--ss", action="store_true", help="ajouter ss -s")
    tcp.set_defaults(func=cmd_tcp_stats)

    doc = sub.add_parser("doctor", help="Vérifie l'environnement (mtr, deps, DB, port)")
    _add_db_arg(doc)
    doc.set_defaults(func=cmd_doctor)

    rm = sub.add_parser("delete", help="Supprime un run")
    rm.add_argument("run_id", type=int)
    _add_db_arg(rm)
    rm.set_defaults(func=cmd_delete)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    console = Console()
    return args.func(args, console)


if __name__ == "__main__":
    sys.exit(main())
