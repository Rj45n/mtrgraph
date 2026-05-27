"""Color thresholds shared by TUI and web."""


def loss_color(loss_pct: float | None) -> str:
    if loss_pct is None:
        return "grey50"
    if loss_pct >= 10:
        return "bold red"
    if loss_pct >= 5:
        return "red"
    if loss_pct >= 1:
        return "yellow"
    return "green"


def latency_color(avg_ms: float | None) -> str:
    if avg_ms is None:
        return "grey50"
    if avg_ms >= 200:
        return "bold red"
    if avg_ms >= 100:
        return "red"
    if avg_ms >= 50:
        return "yellow"
    return "green"


def jitter_color(stddev_ms: float | None) -> str:
    if stddev_ms is None:
        return "grey50"
    if stddev_ms >= 50:
        return "bold red"
    if stddev_ms >= 20:
        return "red"
    if stddev_ms >= 5:
        return "yellow"
    return "green"


def delta_color(delta: float | None, warn: float, crit: float) -> str:
    if delta is None:
        return "grey50"
    if delta >= crit:
        return "bold red"
    if delta >= warn:
        return "yellow"
    if delta <= -warn:
        return "cyan"
    return "white"


def loss_hex(loss_pct: float | None) -> str:
    if loss_pct is None:
        return "#666"
    if loss_pct >= 10:
        return "#b91c1c"
    if loss_pct >= 5:
        return "#dc2626"
    if loss_pct >= 1:
        return "#eab308"
    return "#16a34a"


def latency_hex(avg_ms: float | None) -> str:
    if avg_ms is None:
        return "#666"
    if avg_ms >= 200:
        return "#b91c1c"
    if avg_ms >= 100:
        return "#dc2626"
    if avg_ms >= 50:
        return "#eab308"
    return "#16a34a"


# HTTP-stage thresholds. Different scales because TLS handshake ≈ 2-3 RTT,
# TTFB depends on server processing, etc.
HTTP_THRESHOLDS = {
    "dns":   (20, 100, 300),    # green<20, yellow<100, red<300, bold red>=300
    "tcp":   (20, 100, 300),
    "tls":   (50, 200, 500),
    "ttfb":  (100, 500, 1500),
    "total": (200, 800, 2500),
}


def http_color(stage: str, ms: float | None) -> str:
    if ms is None:
        return "grey50"
    g, y, r = HTTP_THRESHOLDS.get(stage, (50, 200, 500))
    if ms >= r:
        return "bold red"
    if ms >= y:
        return "red"
    if ms >= g:
        return "yellow"
    return "green"


def http_hex(stage: str, ms: float | None) -> str:
    if ms is None:
        return "#666"
    g, y, r = HTTP_THRESHOLDS.get(stage, (50, 200, 500))
    if ms >= r:
        return "#b91c1c"
    if ms >= y:
        return "#dc2626"
    if ms >= g:
        return "#eab308"
    return "#16a34a"


def http_status_color(status: int | None) -> str:
    if status is None:
        return "bold red"
    if 200 <= status < 300:
        return "green"
    if 300 <= status < 400:
        return "cyan"
    if 400 <= status < 500:
        return "yellow"
    return "bold red"
