from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from .colors import (
    http_color,
    http_status_color,
    jitter_color,
    latency_color,
    loss_color,
)


def _fmt(v, suffix="", digits=1):
    if v is None:
        return "-"
    return f"{v:.{digits}f}{suffix}"


def hops_table(target: str, src: str, hops: list[dict], title: str | None = None) -> Table:
    table = Table(
        title=title or f"MTR  {src} → {target}",
        title_style="bold cyan",
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("#", justify="right", width=3)
    table.add_column("Host", overflow="fold")
    table.add_column("Loss%", justify="right")
    table.add_column("Snt", justify="right")
    table.add_column("Last", justify="right")
    table.add_column("Avg", justify="right")
    table.add_column("Best", justify="right")
    table.add_column("Wrst", justify="right")
    table.add_column("StDev", justify="right")
    table.add_column("Bar", overflow="ignore")

    max_avg = max((h["avg_ms"] or 0 for h in hops), default=1) or 1

    for h in hops:
        bar_width = int(((h["avg_ms"] or 0) / max_avg) * 20)
        bar = Text("█" * bar_width, style=latency_color(h["avg_ms"]))
        table.add_row(
            str(h["hop_index"]),
            h["host"] or "???",
            Text(_fmt(h["loss_pct"], "%"), style=loss_color(h["loss_pct"])),
            str(h["sent"] or 0),
            _fmt(h["last_ms"]),
            Text(_fmt(h["avg_ms"]), style=latency_color(h["avg_ms"])),
            _fmt(h["best_ms"]),
            _fmt(h["worst_ms"]),
            Text(_fmt(h["stddev_ms"]), style=jitter_color(h["stddev_ms"])),
            bar,
        )
    return table


def diff_table(
    target: str,
    hops_a: list[dict],
    hops_b: list[dict],
    label_a: str,
    label_b: str,
) -> Table:
    table = Table(
        title=f"DIFF  {target}   {label_a} → {label_b}",
        title_style="bold cyan",
        header_style="bold magenta",
        expand=True,
    )
    table.add_column("#", justify="right", width=3)
    table.add_column("Host")
    table.add_column(f"Avg {label_a}", justify="right")
    table.add_column(f"Avg {label_b}", justify="right")
    table.add_column("ΔAvg", justify="right")
    table.add_column(f"Loss {label_a}", justify="right")
    table.add_column(f"Loss {label_b}", justify="right")
    table.add_column("ΔLoss", justify="right")
    table.add_column("Verdict")

    by_idx_a = {h["hop_index"]: h for h in hops_a}
    by_idx_b = {h["hop_index"]: h for h in hops_b}
    indices = sorted(set(by_idx_a) | set(by_idx_b))

    for idx in indices:
        a = by_idx_a.get(idx)
        b = by_idx_b.get(idx)
        avg_a = a["avg_ms"] if a else None
        avg_b = b["avg_ms"] if b else None
        loss_a = a["loss_pct"] if a else None
        loss_b = b["loss_pct"] if b else None
        host = (b or a)["host"] or "???"
        d_avg = (avg_b - avg_a) if (avg_a is not None and avg_b is not None) else None
        d_loss = (loss_b - loss_a) if (loss_a is not None and loss_b is not None) else None

        verdict_style = "green"
        verdict = "OK"
        if d_loss is not None and d_loss >= 5:
            verdict, verdict_style = "PERTE++", "bold red"
        elif d_avg is not None and avg_a and d_avg / max(avg_a, 1) >= 0.5 and d_avg >= 20:
            verdict, verdict_style = "LATENCE++", "red"
        elif d_avg is not None and avg_a and d_avg / max(avg_a, 1) >= 0.2 and d_avg >= 10:
            verdict, verdict_style = "lent", "yellow"
        elif a is None:
            verdict, verdict_style = "nouveau hop", "cyan"
        elif b is None:
            verdict, verdict_style = "disparu", "magenta"

        def _delta_text(d, unit=""):
            if d is None:
                return Text("-", style="grey50")
            sign = "+" if d > 0 else ""
            style = "red" if d > 0 else ("cyan" if d < 0 else "white")
            return Text(f"{sign}{d:.1f}{unit}", style=style)

        table.add_row(
            str(idx),
            host,
            _fmt(avg_a),
            _fmt(avg_b),
            _delta_text(d_avg, " ms"),
            _fmt(loss_a, "%"),
            _fmt(loss_b, "%"),
            _delta_text(d_loss, " pt"),
            Text(verdict, style=verdict_style),
        )
    return table


def legend() -> Panel:
    body = Text()
    body.append("Loss%  ", style="bold")
    body.append("<1%", style="green")
    body.append("  1-5%", style="yellow")
    body.append("  5-10%", style="red")
    body.append("  >10%\n", style="bold red")
    body.append("Latence ", style="bold")
    body.append("<50ms", style="green")
    body.append("  50-100ms", style="yellow")
    body.append("  100-200ms", style="red")
    body.append("  >200ms\n", style="bold red")
    body.append("Jitter ", style="bold")
    body.append("<5ms", style="green")
    body.append("  5-20ms", style="yellow")
    body.append("  20-50ms", style="red")
    body.append("  >50ms", style="bold red")
    return Panel(body, title="Légende", border_style="cyan", expand=False)


def print_run(console: Console, target: str, src: str, hops: list[dict], title: str | None = None):
    console.print(hops_table(target, src, hops, title=title))
    console.print(legend())


def http_samples_table(url: str, samples: list[dict], title: str | None = None) -> Table:
    table = Table(
        title=title or f"HTTP samples — {url}",
        title_style="bold cyan", header_style="bold magenta", expand=True,
    )
    table.add_column("#", justify="right", width=4)
    table.add_column("DNS", justify="right")
    table.add_column("TCP", justify="right")
    table.add_column("TLS", justify="right")
    table.add_column("TTFB", justify="right")
    table.add_column("Total", justify="right")
    table.add_column("Status", justify="right")
    table.add_column("Erreur", overflow="fold")

    def cell(stage: str, v: float | None) -> Text:
        if v is None:
            return Text("-", style="grey50")
        return Text(f"{v:.1f}", style=http_color(stage, v))

    for s in samples:
        status = s.get("status")
        status_txt = Text(str(status) if status is not None else "-",
                          style=http_status_color(status))
        table.add_row(
            str(s["sample_idx"]),
            cell("dns", s.get("dns_ms")),
            cell("tcp", s.get("tcp_ms")),
            cell("tls", s.get("tls_ms")),
            cell("ttfb", s.get("ttfb_ms")),
            cell("total", s.get("total_ms")),
            status_txt,
            Text(s.get("error") or "", style="red"),
        )
    return table


def http_summary_table(agg: dict, title: str | None = None) -> Table:
    table = Table(
        title=title or "HTTP — résumé", title_style="bold cyan",
        header_style="bold magenta", expand=True,
    )
    table.add_column("Étape", style="bold")
    table.add_column("Avg ms", justify="right")
    table.add_column("Best", justify="right")
    table.add_column("Worst", justify="right")
    table.add_column("StDev", justify="right")
    table.add_column("n", justify="right")

    for stage in ("dns", "tcp", "tls", "ttfb", "total"):
        s = agg.get(stage, {})
        def fmt(v):
            return Text("-", style="grey50") if v is None else Text(f"{v:.1f}", style=http_color(stage, v))
        table.add_row(
            stage.upper(),
            fmt(s.get("avg")),
            fmt(s.get("best")),
            fmt(s.get("worst")),
            Text("-" if s.get("stddev") is None else f"{s['stddev']:.1f}", style="white"),
            str(s.get("n", 0)),
        )
    return table


def http_legend() -> Panel:
    body = Text()
    body.append("DNS  ", style="bold")
    body.append("<20", style="green"); body.append("  20-100", style="yellow"); body.append("  100-300", style="red"); body.append("  >300\n", style="bold red")
    body.append("TCP  ", style="bold")
    body.append("<20", style="green"); body.append("  20-100", style="yellow"); body.append("  100-300", style="red"); body.append("  >300\n", style="bold red")
    body.append("TLS  ", style="bold")
    body.append("<50", style="green"); body.append("  50-200", style="yellow"); body.append("  200-500", style="red"); body.append("  >500\n", style="bold red")
    body.append("TTFB ", style="bold")
    body.append("<100", style="green"); body.append("  100-500", style="yellow"); body.append("  500-1500", style="red"); body.append("  >1500\n", style="bold red")
    body.append("Total", style="bold")
    body.append("<200", style="green"); body.append("  200-800", style="yellow"); body.append("  800-2500", style="red"); body.append("  >2500", style="bold red")
    return Panel(body, title="Légende HTTP (ms)", border_style="cyan", expand=False)
