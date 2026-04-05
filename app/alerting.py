import logging
import os
import threading
import time
from datetime import datetime, timezone

import requests

from app.logging_config import get_log_buffer

logger = logging.getLogger("app")

# How often the monitor loop runs (seconds)
_CHECK_INTERVAL = 30

# Delay before first check — lets Flask finish starting up
_STARTUP_DELAY = 5

# Minimum seconds between repeated alerts for the same condition
_ALERT_COOLDOWN = 300  # 5 minutes

# Error rate threshold: if >= this fraction of requests in the window are 5xx, alert
_ERROR_RATE_THRESHOLD = 0.5  # 50%

# Rolling window for error rate calculation (seconds)
_ERROR_RATE_WINDOW = 120  # 2 minutes

# Track when each alert type was last fired to enforce cooldown
_last_alert: dict[str, float] = {}
_lock = threading.Lock()


def _send_discord(message: str) -> None:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    if not webhook_url:
        logger.warning("DISCORD_WEBHOOK_URL not set — skipping alert")
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
    except Exception as exc:
        logger.error("Failed to send Discord alert", exc_info=exc)


def _should_alert(key: str) -> bool:
    """Return True if enough time has passed since the last alert for this key."""
    with _lock:
        last = _last_alert.get(key, 0)
        if time.time() - last >= _ALERT_COOLDOWN:
            _last_alert[key] = time.time()
            return True
        return False


def _check_service_down(base_url: str) -> None:
    health_url = f"{base_url}/health"
    try:
        resp = requests.get(health_url, timeout=5)
        if resp.status_code == 200:
            # Service is up — reset so next outage alerts immediately
            with _lock:
                _last_alert.pop("service_down", None)
            return
        status = resp.status_code
    except Exception as exc:
        status = str(exc)

    if _should_alert("service_down"):
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        _send_discord(
            f":red_circle: **[{ts}] SERVICE DOWN**\n"
            f"Health check failed — `{health_url}` returned `{status}`"
        )
        logger.error("Alert fired: service_down", extra={"health_url": health_url, "reason": str(status)})


def _check_error_rate() -> None:
    buf = get_log_buffer()
    now = time.time()
    cutoff = now - _ERROR_RATE_WINDOW

    recent = [
        e for e in buf
        if e.get("message") == "request"
        and isinstance(e.get("timestamp"), str)
        and _iso_to_epoch(e["timestamp"]) >= cutoff
    ]

    if len(recent) < 5:
        return  # not enough traffic to be meaningful

    errors = [e for e in recent if e.get("status", 0) >= 500]
    rate = len(errors) / len(recent)

    if rate >= _ERROR_RATE_THRESHOLD:
        if _should_alert("high_error_rate"):
            ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            _send_discord(
                f":warning: **[{ts}] HIGH ERROR RATE**\n"
                f"{len(errors)}/{len(recent)} requests in the last "
                f"{_ERROR_RATE_WINDOW}s returned 5xx "
                f"({rate:.0%})"
            )
            logger.error(
                "Alert fired: high_error_rate",
                extra={"error_count": len(errors), "total": len(recent), "rate": round(rate, 2)},
            )
    else:
        # Rate is healthy — reset so next spike alerts immediately
        with _lock:
            _last_alert.pop("high_error_rate", None)


def _iso_to_epoch(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0.0


def _monitor_loop(base_url: str) -> None:
    time.sleep(_STARTUP_DELAY)  # wait for Flask to finish starting
    logger.info("Alerting monitor started", extra={"base_url": base_url, "interval": _CHECK_INTERVAL})
    while True:
        try:
            _check_service_down(base_url)
            _check_error_rate()
        except Exception:
            logger.exception("Unexpected error in monitor loop")
        time.sleep(_CHECK_INTERVAL)


def start_monitor(base_url: str = "http://localhost:5001") -> None:
    """Start the background monitoring thread. Call once at app startup."""
    t = threading.Thread(target=_monitor_loop, args=(base_url,), daemon=True, name="alerting-monitor")
    t.start()
