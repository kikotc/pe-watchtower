"""
Standalone monitor — run alongside the Flask app in a separate terminal.
Serves the status page + internal dashboard on port 5002.
Watches /health and error rate, fires Discord alerts independently.
"""
import json
import logging
import os
import pathlib
import subprocess
import sys
import threading
import time
from collections import deque
from datetime import datetime, timezone
from functools import wraps
from typing import Optional

import requests
from dotenv import load_dotenv
from flask import (
    Flask, jsonify, make_response, redirect,
    render_template, request, url_for,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

CHECK_INTERVAL       = int(os.environ.get("MONITOR_INTERVAL", 15))
STARTUP_DELAY        = 3
ALERT_COOLDOWN       = 300
ERROR_RATE_THRESHOLD = 0.25
ERROR_RATE_WINDOW    = 120
BASE_URL             = os.environ.get("APP_URL", "http://localhost:5001")
UI_PORT              = int(os.environ.get("MONITOR_PORT", 5002))

_SECRET   = os.environ.get("DASHBOARD_SECRET", "dashboard-dev-secret-change-me")
_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "admin")
_COOKIE   = "dashboard_token"

_TEMPLATE_DIR = str(pathlib.Path(__file__).parent / "app" / "templates")
_LOG_FILE     = str(pathlib.Path(__file__).parent / "logs" / "app.log")

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("monitor")

# ── Alert state ───────────────────────────────────────────────────────────────

_last_alert: dict[str, float] = {}
_down_since: Optional[float]  = None
_INCIDENTS_URL = f"{BASE_URL}/incidents/record"

# ── Self-healing state ───────────────────────────────────────────────────────

_PROJECT_DIR     = str(pathlib.Path(__file__).parent)
_HEAL_COOLDOWN   = 30          # seconds between restart attempts
_MAX_HEAL_TRIES  = 5           # max consecutive restart attempts
_heal_last       = 0.0
_heal_tries      = 0
_heal_consecutive_down = 0     # checks since last healthy
_flask_proc: Optional[subprocess.Popen] = None
_remediation_log: deque = deque(maxlen=50)  # ring buffer of events
_alert_log: deque = deque(maxlen=50)       # ring buffer of fired alerts


# ── Helpers ───────────────────────────────────────────────────────────────────

def iso_to_epoch(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0.0


def _read_log_file() -> list:
    entries = []
    try:
        with open(_LOG_FILE) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        pass
    return entries


# ── Discord ───────────────────────────────────────────────────────────────────

def send_discord(embed: dict) -> None:
    webhook_url = os.environ.get("DISCORD_WEBHOOK_URL")
    # Always record the alert locally regardless of webhook
    _alert_log.append({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "title": embed.get("title", ""),
        "description": embed.get("description", ""),
        "fields": embed.get("fields", []),
        "color": embed.get("color", 0),
    })
    if not webhook_url:
        return
    role_id = os.environ.get("DISCORD_ALERT_ROLE_ID")
    content = f"<@&{role_id}>" if role_id else ""
    allowed_mentions = {"roles": [role_id]} if role_id else {"parse": []}
    try:
        resp = requests.post(webhook_url,
                             json={"content": content, "allowed_mentions": allowed_mentions, "embeds": [embed]},
                             timeout=5)
        if resp.status_code not in (200, 204):
            logger.warning("Discord webhook returned %s", resp.status_code)
        else:
            logger.info("Discord alert sent")
    except Exception as exc:
        logger.error("Failed to send Discord alert: %s", exc)


# ── Alerting ──────────────────────────────────────────────────────────────────

def _should_alert(key: str) -> bool:
    last = _last_alert.get(key, 0)
    if time.time() - last >= ALERT_COOLDOWN:
        _last_alert[key] = time.time()
        return True
    return False


def _record_incident(incident_type: str, started_at: str,
                     resolved_at: Optional[str] = None,
                     duration_seconds: Optional[float] = None) -> None:
    try:
        requests.post(_INCIDENTS_URL, json={
            "type": incident_type,
            "started_at": started_at,
            "resolved_at": resolved_at,
            "duration_seconds": duration_seconds,
        }, timeout=5)
    except Exception as exc:
        logger.warning("Could not record incident: %s", exc)


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
            duration = int(time.time() - _down_since)
            mins, secs = divmod(duration, 60)
            duration_str = f"{mins}m {secs}s" if mins else f"{secs}s"
            down_ts      = datetime.fromtimestamp(_down_since, tz=timezone.utc).isoformat()
            recovered_ts = datetime.now(timezone.utc).isoformat()
            _record_incident("service_down", down_ts, recovered_ts, float(duration))
            send_discord({
                "title": "✅  Service Recovered",
                "description": "The service is back online.",
                "color": 0x2ECC71,
                "fields": [
                    {"name": "Endpoint",      "value": f"`{health_url}`",  "inline": True},
                    {"name": "Was down for",  "value": f"`{duration_str}`", "inline": True},
                ],
                "footer":    {"text": "Watchtower Alerting"},
                "timestamp": recovered_ts,
            })
            logger.info("Alert fired: service_recovered — was down %ss", duration)
            _down_since = None
        _last_alert.pop("service_down", None)
        return

    if _down_since is None:
        _down_since = time.time()

    if _should_alert("service_down"):
        send_discord({
            "title": "🔴  Service Down",
            "description": "The health check has failed. Immediate attention required.",
            "color": 0xE74C3C,
            "fields": [
                {"name": "Endpoint",      "value": f"`{health_url}`", "inline": True},
                {"name": "Reason",        "value": f"`{status}`",     "inline": True},
                {"name": "Next check in", "value": f"`{CHECK_INTERVAL}s`", "inline": True},
            ],
            "footer":    {"text": "Watchtower Alerting"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.error("Alert fired: service_down — %s", status)


def check_error_rate() -> None:
    try:
        resp = requests.get(f"{BASE_URL}/logs", timeout=5)
        if resp.status_code != 200:
            return
        buf = resp.json()
    except Exception:
        return

    now    = time.time()
    cutoff = now - ERROR_RATE_WINDOW
    skip_paths = ("/health", "/metrics", "/logs", "/slo", "/incidents",
                  "/uptime-history", "/error-classification", "/chaos", "/debug")
    recent = [e for e in buf
              if e.get("message") == "request"
              and isinstance(e.get("timestamp"), str)
              and iso_to_epoch(e["timestamp"]) >= cutoff
              and not any(e.get("path", "").startswith(p) for p in skip_paths)]

    if len(recent) < 5:
        return

    errors = [e for e in recent if e.get("status", 0) >= 500]
    rate   = len(errors) / len(recent)

    if rate >= ERROR_RATE_THRESHOLD:
        if _should_alert("high_error_rate"):
            send_discord({
                "title": "⚠️  High Error Rate Detected",
                "description": f"More than {ERROR_RATE_THRESHOLD:.0%} of recent requests returned a 5xx error.",
                "color": 0xE67E22,
                "fields": [
                    {"name": "Error Rate",       "value": f"`{rate:.0%}`",                  "inline": True},
                    {"name": "Failed Requests",  "value": f"`{len(errors)} / {len(recent)}`", "inline": True},
                    {"name": "Window",           "value": f"`{ERROR_RATE_WINDOW}s`",         "inline": True},
                ],
                "footer":    {"text": "Watchtower Alerting"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            logger.error("Alert fired: high_error_rate — %.0f%%", rate * 100)
    else:
        _last_alert.pop("high_error_rate", None)


# ── Monitor loop ──────────────────────────────────────────────────────────────

def check_burn_rate() -> None:
    try:
        r = requests.get(f"{BASE_URL}/logs", timeout=5)
        if r.status_code != 200:
            return
        buf = r.json()
    except Exception:
        return

    reqs = [e for e in buf if e.get("message") == "request"]
    if not reqs:
        return

    now       = time.time()
    target    = 99.9
    allowed   = 1 - (target / 100)  # 0.001

    window_1h = [e for e in reqs if iso_to_epoch(e.get("timestamp", "")) >= now - 3600]
    if len(window_1h) < 5:
        return

    errors_1h = sum(1 for e in window_1h if e.get("status", 0) >= 500)
    error_rate = errors_1h / len(window_1h)
    burn_rate  = round(error_rate / allowed, 2) if allowed else 0.0

    # Reset if healthy
    if burn_rate <= 14.4:
        _last_alert.pop("burn_rate", None)
        return

    if _should_alert("burn_rate"):
        # Estimate hours until budget exhausted (monthly budget = 43.8 min)
        monthly_budget_hours = (1 - target / 100) * 24 * 30
        hours_left = round(monthly_budget_hours / (burn_rate * allowed * 24), 1) if burn_rate > 0 else "∞"
        send_discord({
            "title": "🔥  SLO Burn Rate Critical",
            "description": f"Error budget is being consumed **{burn_rate}x faster** than expected. At this rate, the monthly SLO budget will be exhausted soon.",
            "color": 0x9B59B6,  # purple
            "fields": [
                {"name": "Burn Rate (1h)",  "value": f"`{burn_rate}x`",          "inline": True},
                {"name": "SLO Target",      "value": f"`{target}%`",              "inline": True},
                {"name": "Errors (1h)",     "value": f"`{errors_1h}/{len(window_1h)}`", "inline": True},
                {"name": "Critical Threshold", "value": "`14.4x` (Google SRE)", "inline": True},
                {"name": "Est. Budget Exhaustion", "value": f"`{hours_left}h`",  "inline": True},
            ],
            "footer":    {"text": "Watchtower Alerting"},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
        logger.error("Alert fired: burn_rate — %.1fx", burn_rate)


# ── Self-healing ─────────────────────────────────────────────────────────────

def _log_remediation(action: str, detail: str, success: bool) -> None:
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "detail": detail,
        "success": success,
    }
    _remediation_log.append(entry)
    logger.info("Remediation: %s — %s (success=%s)", action, detail, success)


def attempt_self_heal() -> None:
    """Try to restart the Flask app if it's been down for 2+ consecutive checks."""
    global _heal_last, _heal_tries, _heal_consecutive_down, _flask_proc

    # Check if service is reachable
    try:
        resp = requests.get(f"{BASE_URL}/health", timeout=5)
        is_up = resp.status_code == 200
    except Exception:
        is_up = False

    if is_up:
        if _heal_consecutive_down > 0:
            _log_remediation("health_restored", "Service came back online", True)
        _heal_consecutive_down = 0
        _heal_tries = 0
        return

    _heal_consecutive_down += 1

    # Wait for 2 consecutive failures before attempting restart
    if _heal_consecutive_down < 2:
        return

    # Respect cooldown and max retries
    now = time.time()
    if now - _heal_last < _HEAL_COOLDOWN:
        return
    if _heal_tries >= _MAX_HEAL_TRIES:
        if _heal_tries == _MAX_HEAL_TRIES:
            _log_remediation("restart_aborted",
                             f"Max attempts ({_MAX_HEAL_TRIES}) reached — manual intervention required",
                             False)
            send_discord({
                "title": "🛑  Self-Healing Exhausted",
                "description": f"Tried to restart the service **{_MAX_HEAL_TRIES} times** without success. Manual intervention required.",
                "color": 0x8B0000,
                "footer": {"text": "Watchtower Self-Healing"},
                "timestamp": datetime.now(timezone.utc).isoformat(),
            })
            _heal_tries += 1  # prevent repeated alerts
        return

    _heal_last = now
    _heal_tries += 1

    _log_remediation("restart_attempt",
                     f"Attempt {_heal_tries}/{_MAX_HEAL_TRIES} — starting Flask process",
                     True)

    send_discord({
        "title": "🔧  Auto-Remediation: Restarting Service",
        "description": f"Service has been down for {_heal_consecutive_down} checks. Attempting automatic restart (attempt {_heal_tries}/{_MAX_HEAL_TRIES}).",
        "color": 0x3498DB,
        "footer": {"text": "Watchtower Self-Healing"},
        "timestamp": datetime.now(timezone.utc).isoformat(),
    })

    try:
        # Kill any existing managed process
        if _flask_proc and _flask_proc.poll() is None:
            _flask_proc.terminate()
            _flask_proc.wait(timeout=5)
    except Exception:
        pass

    try:
        _heal_log_path = os.path.join(_PROJECT_DIR, "logs", "self-heal.log")
        _heal_log_fd = open(_heal_log_path, "a")
        _flask_proc = subprocess.Popen(
            [sys.executable, "run.py"],
            cwd=_PROJECT_DIR,
            stdout=_heal_log_fd,
            stderr=_heal_log_fd,
        )
        _log_remediation("restart_launched",
                         f"PID {_flask_proc.pid} — output → logs/self-heal.log",
                         True)
    except Exception as exc:
        _log_remediation("restart_failed", str(exc), False)


def _monitor_loop() -> None:
    time.sleep(STARTUP_DELAY)
    logger.info("Alerting monitor started — watching %s every %ss", BASE_URL, CHECK_INTERVAL)
    while True:
        try:
            check_service_down()
            check_error_rate()
            check_burn_rate()
            attempt_self_heal()
        except Exception:
            logger.exception("Unexpected error in monitor loop")
        time.sleep(CHECK_INTERVAL)


# ── UI Flask app ──────────────────────────────────────────────────────────────

ui = Flask("monitor_ui", template_folder=_TEMPLATE_DIR)
ui.secret_key = _SECRET

_serializer = URLSafeTimedSerializer(_SECRET)


def _login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get(_COOKIE)
        try:
            _serializer.loads(token, max_age=86400)
        except (BadSignature, SignatureExpired, TypeError, Exception):
            return redirect(url_for("login"))
        return f(*args, **kwargs)
    return decorated


@ui.route("/", methods=["GET"])
def status():
    return render_template("status.html")


@ui.route("/dashboard/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password", "") == _PASSWORD:
            token = _serializer.dumps("authenticated")
            resp  = make_response(redirect(url_for("dashboard")))
            resp.set_cookie(_COOKIE, token, httponly=True, samesite="Lax", max_age=86400)
            return resp
        error = "Incorrect password."
    return render_template("login.html", error=error)


@ui.route("/dashboard/logout")
def logout():
    resp = make_response(redirect(url_for("login")))
    resp.delete_cookie(_COOKIE)
    return resp


@ui.route("/dashboard")
@_login_required
def dashboard():
    return render_template("dashboard.html")


@ui.route("/dashboard/runbook")
@_login_required
def runbook():
    return render_template("runbook.html")


# ── Data routes (proxy to main app or read log directly) ─────────────────────

@ui.route("/health")
def health_proxy():
    try:
        r = requests.get(f"{BASE_URL}/health", timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify({"status": "unreachable"}), 503


@ui.route("/metrics")
def metrics_proxy():
    try:
        r = requests.get(f"{BASE_URL}/metrics", timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify({"error": "unreachable"}), 503


@ui.route("/logs")
def logs_proxy():
    try:
        r = requests.get(f"{BASE_URL}/logs", timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify([]), 200


@ui.route("/slo")
def slo_proxy():
    try:
        r = requests.get(f"{BASE_URL}/slo", timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify({"uptime_percent": 100, "total_requests": 0}), 200


@ui.route("/incidents")
def incidents_proxy():
    try:
        r = requests.get(f"{BASE_URL}/incidents", timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify([]), 200


@ui.route("/uptime-history")
def uptime_history_proxy():
    try:
        r = requests.get(f"{BASE_URL}/uptime-history", timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify([]), 200


@ui.route("/urls")
def urls_proxy():
    try:
        r = requests.get(f"{BASE_URL}/urls", timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify([]), 200


@ui.route("/users")
def users_proxy():
    try:
        r = requests.get(f"{BASE_URL}/users", timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify([]), 200


@ui.route("/events")
def events_proxy():
    try:
        r = requests.get(f"{BASE_URL}/events", timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify([]), 200


@ui.route("/dashboard/history")
@_login_required
def history():
    entries = _read_log_file()
    return jsonify(entries[-10_000:])


@ui.route("/dashboard/slo")
@_login_required
def dashboard_slo():
    entries  = _read_log_file()
    reqs     = [e for e in entries if e.get("method") and e.get("status")]
    total    = len(reqs)
    errors   = sum(1 for r in reqs if r.get("status", 0) >= 500)
    uptime   = round((total - errors) / total * 100, 3) if total else 100.0
    target   = 99.9
    budget_total    = round(100 - target, 3)
    budget_consumed = round((100 - uptime) / budget_total * 100, 1) if budget_total else 0

    # Burn rate — compare last 1h vs last 6h error rate
    # A burn rate of 1.0 = exactly consuming budget at expected pace
    # >14.4 = will exhaust monthly budget in <2 days (Google SRE threshold)
    now    = time.time()
    window_1h  = [r for r in reqs if iso_to_epoch(r.get("timestamp", "")) >= now - 3600]
    window_6h  = [r for r in reqs if iso_to_epoch(r.get("timestamp", "")) >= now - 21600]

    def _burn(window):
        if not window:
            return 0.0
        err = sum(1 for r in window if r.get("status", 0) >= 500)
        error_rate = err / len(window)
        allowed_error_rate = 1 - (target / 100)  # 0.001 for 99.9%
        return round(error_rate / allowed_error_rate, 2) if allowed_error_rate else 0.0

    burn_1h = _burn(window_1h)
    burn_6h = _burn(window_6h)

    return jsonify({
        "uptime_pct":      uptime,
        "slo_target":      target,
        "slo_met":         uptime >= target,
        "total_requests":  total,
        "error_requests":  errors,
        "budget_consumed": min(budget_consumed, 999),
        "burn_rate_1h":    burn_1h,
        "burn_rate_6h":    burn_6h,
        "burn_alert":      burn_1h > 14.4,  # Google SRE critical threshold
    })


@ui.route("/error-classification")
def error_classification_proxy():
    try:
        r = requests.get(f"{BASE_URL}/error-classification", timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify({"total_errors": 0, "breakdown": [], "top_error_endpoints": []}), 200


@ui.route("/dashboard/incidents")
@_login_required
def dashboard_incidents():
    return jsonify(list(_alert_log))


# ── Chaos proxy routes ──────────────────────────────────────────────────────

@ui.route("/chaos/status")
@_login_required
def chaos_status_proxy():
    try:
        r = requests.get(f"{BASE_URL}/chaos/status", timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception:
        return jsonify({"experiments": {}, "any_active": False, "unreachable": True}), 200


@ui.route("/chaos/<action>", methods=["POST"])
@_login_required
def chaos_action_proxy(action):
    allowed = {"latency", "error-rate", "db-kill", "cpu-stress", "crash", "clear", "traffic"}
    if action not in allowed:
        return jsonify({"error": "unknown action"}), 400
    try:
        data = request.get_json(silent=True) or {}
        r = requests.post(f"{BASE_URL}/chaos/{action}", json=data, timeout=5)
        return jsonify(r.json()), r.status_code
    except Exception as exc:
        return jsonify({"error": str(exc)}), 503


# ── Remediation log endpoint ────────────────────────────────────────────────

@ui.route("/dashboard/remediation-log")
@_login_required
def remediation_log():
    return jsonify(list(_remediation_log))


def _start_ui_server() -> None:
    logger.info("UI server running at http://localhost:%s", UI_PORT)
    ui.run(host="0.0.0.0", port=UI_PORT, debug=False, use_reloader=False)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    threading.Thread(target=_start_ui_server, daemon=True, name="ui-server").start()
    threading.Thread(target=_monitor_loop,    daemon=False, name="monitor-loop").start()


if __name__ == "__main__":
    main()
