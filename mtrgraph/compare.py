from dataclasses import dataclass


@dataclass
class HopDelta:
    hop_index: int
    host: str | None
    avg_a: float | None
    avg_b: float | None
    loss_a: float | None
    loss_b: float | None

    @property
    def d_avg(self) -> float | None:
        if self.avg_a is None or self.avg_b is None:
            return None
        return self.avg_b - self.avg_a

    @property
    def d_loss(self) -> float | None:
        if self.loss_a is None or self.loss_b is None:
            return None
        return self.loss_b - self.loss_a

    @property
    def severity(self) -> str:
        if self.d_loss is not None and self.d_loss >= 10:
            return "critical"
        if self.d_loss is not None and self.d_loss >= 3:
            return "warning"
        if self.d_avg is not None and self.avg_a and self.d_avg / max(self.avg_a, 1) >= 0.5 and self.d_avg >= 20:
            return "warning"
        return "ok"


def diff(hops_a: list[dict], hops_b: list[dict]) -> list[HopDelta]:
    by_a = {h["hop_index"]: h for h in hops_a}
    by_b = {h["hop_index"]: h for h in hops_b}
    out = []
    for idx in sorted(set(by_a) | set(by_b)):
        a = by_a.get(idx)
        b = by_b.get(idx)
        out.append(
            HopDelta(
                hop_index=idx,
                host=(b or a).get("host"),
                avg_a=a["avg_ms"] if a else None,
                avg_b=b["avg_ms"] if b else None,
                loss_a=a["loss_pct"] if a else None,
                loss_b=b["loss_pct"] if b else None,
            )
        )
    return out


def hops_from_baseline(baseline: dict[int, dict]) -> list[dict]:
    """Convert baseline_hops() output into the same shape as parse_report() hops."""
    return [
        {
            "hop_index": idx,
            "host": b["host"],
            "loss_pct": b["loss_pct"],
            "sent": b["samples"],
            "last_ms": None,
            "avg_ms": b["avg_ms"],
            "best_ms": None,
            "worst_ms": None,
            "stddev_ms": None,
        }
        for idx, b in sorted(baseline.items())
    ]


def degraded_hops(deltas: list[HopDelta]) -> list[HopDelta]:
    return [d for d in deltas if d.severity in ("warning", "critical")]
