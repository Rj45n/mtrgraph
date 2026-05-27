"""Webhook notifications for degraded statuses (Slack-compatible)."""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from ._common import iso, now_utc


def is_degraded(status: str | None) -> bool:
    if not status:
        return False
    return (
        status.startswith("warning:") or status.startswith("critical:")
        or status.startswith("err:") or status.startswith("http:4")
        or status.startswith("http:5") or status.startswith("unknown")
    )


def post_webhook(url: str, payload: dict, log_fn) -> None:
    """Best-effort POST. Slack-compatible if {text:...}. Errors are logged but
    never propagated — webhook failure must not break the scheduler."""
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json", "User-Agent": "mtrgraph-scheduler/0.1"},
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            if resp.status >= 400:
                log_fn(f"[webhook] {url} → {resp.status}")
    except urllib.error.URLError as e:
        log_fn(f"[webhook] {url} → error: {e}")
    except Exception as e:
        log_fn(f"[webhook] {url} → unexpected: {e}")


def maybe_notify(row, status: str, run_id: int | None, log_fn) -> None:
    """Post the schedule's webhook_url if status is degraded."""
    webhook = row["webhook_url"] if "webhook_url" in row.keys() else None
    if not webhook or not is_degraded(status):
        return
    sev = status.split(":", 1)[0]
    icon = {"critical": "🔴", "warning": "🟡", "err": "❌"}.get(sev, "⚠️")
    text = (
        f"{icon} *mtrgraph* schedule [`{row['name']}`] (#{row['id']}, kind={row['kind']}) "
        f"→ `{status}` (run_id={run_id})"
    )
    payload = {
        "text": text,
        "schedule_id": row["id"],
        "schedule_name": row["name"],
        "kind": row["kind"],
        "status": status,
        "severity": sev,
        "run_id": run_id,
        "timestamp": iso(now_utc()),
    }
    post_webhook(webhook, payload, log_fn)
