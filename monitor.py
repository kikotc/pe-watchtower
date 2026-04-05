"""
Standalone alerting monitor — run this in a separate terminal alongside the Flask app.
It watches /health and /logs and fires Discord alerts independently of the app process.
"""
import logging
import os
import time
from datetime import datetime, timezone
from typing import Optional

import requests
from dotenv import load_dotenv

load_dotenv()

CHECK_INTERVAL = 15
STARTUP_DELAY = 3
ALERT_COOLDOWN = 300
ERROR_RATE_THRESHOLD = 0.5
ERROR_RATE_WINDOW = 120
BASE_URL = "http://localhost:5001"

_last_alert: dict[str, float] = {}
_down_since: Optional[float] = None  # timestamp when service went down
_INCIDENTS_URL = f"http://localhost:5001/incidents/record"


def _record_incident(incident_type: str, started_at: str, resolved_at: Optional[str] = None,
                     duration_seconds: Optional[float] = None, details: str = "") -> None:
    try:
        requests.post(_INCIDENTS_URL, json={
            "type": incident_type,
            "started_at": started_at,
            "resolved_at": resolved_at,
            "duration_seconds": duration_seconds,
            "details": details,
        }, timeout=5)
    except Exception as exc:
        logger.warning("Could not record incident to DB: %s", exc)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("monitor")


def send_discord(embed: dict) -> None:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not set")
        return

    role_id = os.environ.get("DISCORD_ALERT_ROLE_ID")
    content = f"<@&{role_id}>" if role_id else ""
    allowed_mentions = {"roles": [role_id]} if role_id else {"parse": []}

    payload = {
        "content": content,
        "allowed_mentions": allowed_mentions,
        "embeds": [embed],
    }

    try:
        resp = requests.post(webhook_url, json=payload, timeout=5)
        if resp.status_code not in (200, 204):
            logger.warning("Discord webhook returned %s", resp.status_code)
        else:
            logger.info("Discord alert sent")
    except Exception as exc:
        logger.error("Failed to send Discord alert: %s", exc)


def should_alert(key: str) -> bool:
    last = _last_alert.get(key, 0)
    if time.time() - last >= ALERT_COOLDOWN:
        _last_alert[key] = time.time()
        return True
    return False


def check_service_down() -> None:
    global _down_since
    health_url = f"{BASE_URL}/health"
    try:
        resp = requests.get(health_url, timeout=5)
        is_up = resp.status_code == 200
    except Exception:
        is_up = False
        status = "Connection refused"
    else:
        status = resp.status_code

    if is_up:
        if _down_since is not None:
            # Service just recovered — send recovery alert
            duration = int(time.time() - _down_since)
            mins, secs = divmod(duration, 60)
            duration_str = f"{mins}m {secs}s" if mins else f"{secs}s"
            down_ts = datetime.fromtimestamp(_down_since, tz=timezone.utc).isoformat()
            recovered_ts = datetime.now(timezone.utc).isoformat()
            _record_incident("service_down", down_ts, recovered_ts, float(duration))
            send_discord({
                "title": "✅  Service Recovered",
                "description": "The service is back online.",
                "color": 0x2ECC71,  # green
                "fields": [
                    {"name": "Endpoint", "value": f"`{health_url}`", "inline": True},
                    {"name": "Was down for", "value": f"`{duration_str}`", "inline": True},
                ],
                "footer": {"text": "Watchtower Alerting"},
                "timestamp": recovered_ts,
            })
            logger.info("Alert fired: service_recovered — was down %ss", duration)
            _down_since = None
        _last_alert.pop("service_down", None)
        return

    if _down_since is None:
        _down_since = time.time()

    if should_alert("service_down"):
        send_discord({
            "title": "🔴  Service Down",
            "description": "The health check has failed. Immediate attention required.",
            "color": 0xE74C3C,  # red
            "fields": [
                {"name": "Endpoint", "value": f"`{health_url}`", "inline": True},
                {"name": "Reason", "value": "Connection refused", "inline": True},
                {"name": "Full Error", "value": f"||`{status}`||", "inline": False},
                {"name": "Next check in", "value": f"`{CHECK_INTERVAL}s`", "inline": True},
            ],
            "footer": {"text": "Watchtower Alerting"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.error("Alert fired: service_down — %s", status)


def check_error_rate() -> None:
    logs_url = f"{BASE_URL}/logs"
    try:
        resp = requests.get(logs_url, timeout=5)
        if resp.status_code != 200:
            return
        buf = resp.json()
    except Exception:
        return

    now = time.time()
    cutoff = now - ERROR_RATE_WINDOW

    recent = [
        e for e in buf
        if e.get("message") == "request"
        and isinstance(e.get("timestamp"), str)
        and iso_to_epoch(e["timestamp"]) >= cutoff
    ]

    if len(recent) < 5:
        return

    errors = [e for e in recent if e.get("status", 0) >= 500]
    rate = len(errors) / len(recent)

    if rate >= ERROR_RATE_THRESHOLD:
        if should_alert("high_error_rate"):
            send_discord({
                "title": "⚠️  High Error Rate Detected",
                "description": f"More than {ERROR_RATE_THRESHOLD:.0%} of recent requests returned a 5xx error.",
                "color": 0xE67E22,  # orange
                "fields": [
                    {"name": "Error Rate", "value": f"`{rate:.0%}`", "inline": True},
                    {"name": "Failed Requests", "value": f"`{len(errors)} / {len(recent)}`", "inline": True},
                    {"name": "Window", "value": f"`{ERROR_RATE_WINDOW}s`", "inline": True},
                ],
                "footer": {"text": "Watchtower Alerting"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.error("Alert fired: high_error_rate — %.0f%%", rate * 100)
    else:
        _last_alert.pop("high_error_rate", None)


def iso_to_epoch(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0.0


def main() -> None:
    logger.info("Monitor starting — watching %s every %ss", BASE_URL, CHECK_INTERVAL)
    time.sleep(STARTUP_DELAY)
    while True:
        check_service_down()
        check_error_rate()
        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
