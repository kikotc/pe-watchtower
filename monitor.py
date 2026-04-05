"""
Standalone alerting monitor — run this in a separate terminal alongside the Flask app.
It watches /health and /logs and fires Discord alerts independently of the app process.
"""
import json
import logging
import os
import time
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

load_dotenv()

CHECK_INTERVAL = 30
STARTUP_DELAY = 3
ALERT_COOLDOWN = 300
ERROR_RATE_THRESHOLD = 0.5
ERROR_RATE_WINDOW = 120
BASE_URL = "http://localhost:5001"

_last_alert: dict[str, float] = {}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("monitor")


def send_discord(message: str) -> None:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not set")
        return

    role_id = os.environ.get("DISCORD_ALERT_ROLE_ID")
    if role_id:
        content = f"<@&{role_id}> {message}"
        allowed_mentions = {"roles": [role_id]}
    else:
        content = message
        allowed_mentions = {"parse": []}

    try:
        resp = requests.post(
            webhook_url,
            json={"content": content, "allowed_mentions": allowed_mentions},
            timeout=5,
        )
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
    health_url = f"{BASE_URL}/health"
    try:
        resp = requests.get(health_url, timeout=5)
        if resp.status_code == 200:
            _last_alert.pop("service_down", None)
            return
        status = resp.status_code
    except Exception as exc:
        status = str(exc)

    if should_alert("service_down"):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        send_discord(
            f":red_circle: **[{ts}] SERVICE DOWN**\n"
            f"Health check failed — `{health_url}`\nReason: `{status}`"
        )
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
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            send_discord(
                f":warning: **[{ts}] HIGH ERROR RATE**\n"
                f"{len(errors)}/{len(recent)} requests in the last {ERROR_RATE_WINDOW}s "
                f"returned 5xx ({rate:.0%})"
            )
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
