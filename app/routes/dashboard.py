import json
import os
from functools import wraps

from flask import (
    Blueprint,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

dashboard_bp = Blueprint("dashboard", __name__)

_SECRET = os.environ.get("DASHBOARD_SECRET", "dashboard-dev-secret-change-me")
_PASSWORD = os.environ.get("DASHBOARD_PASSWORD", "admin")
_COOKIE = "dashboard_token"
_serializer = URLSafeTimedSerializer(_SECRET)


def _login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = request.cookies.get(_COOKIE)
        try:
            _serializer.loads(token, max_age=86400)  # 24-hour session
        except (BadSignature, SignatureExpired, TypeError, Exception):
            return redirect(url_for("dashboard.login"))
        return f(*args, **kwargs)
    return decorated


@dashboard_bp.route("/dashboard/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        if request.form.get("password", "") == _PASSWORD:
            token = _serializer.dumps("authenticated")
            resp = make_response(redirect(url_for("dashboard.home")))
            resp.set_cookie(_COOKIE, token, httponly=True, samesite="Lax", max_age=86400)
            return resp
        error = "Incorrect password."
    return render_template("login.html", error=error)


@dashboard_bp.route("/dashboard/logout")
def logout():
    resp = make_response(redirect(url_for("dashboard.login")))
    resp.delete_cookie(_COOKIE)
    return resp


@dashboard_bp.route("/dashboard")
@_login_required
def home():
    return render_template("dashboard.html")


@dashboard_bp.route("/dashboard/history")
@_login_required
def history():
    """Read the full log file and return up to 10 000 entries for chart seeding."""
    log_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "logs", "app.log")
    )
    entries = []
    try:
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        entries.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except FileNotFoundError:
        pass
    from flask import jsonify
    return jsonify(entries[-10_000:])


def _read_log_file():
    """Read and parse every line of the log file. Returns list of dicts."""
    log_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "logs", "app.log")
    )
    entries = []
    try:
        with open(log_path) as f:
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


@dashboard_bp.route("/dashboard/slo")
@_login_required
def slo():
    """Compute SLO uptime % from the full log file."""
    from flask import jsonify
    entries = _read_log_file()
    reqs    = [e for e in entries if e.get("method") and e.get("status")]
    total   = len(reqs)
    errors  = sum(1 for r in reqs if r.get("status", 0) >= 500)
    uptime  = round((total - errors) / total * 100, 3) if total else 100.0
    target  = 99.9
    budget_total    = round(100 - target, 3)
    budget_consumed = round((100 - uptime) / budget_total * 100, 1) if budget_total else 0
    return jsonify({
        "uptime_pct":       uptime,
        "slo_target":       target,
        "slo_met":          uptime >= target,
        "total_requests":   total,
        "error_requests":   errors,
        "budget_consumed":  min(budget_consumed, 999),
    })


@dashboard_bp.route("/dashboard/incidents")
@_login_required
def incidents():
    """Return alert-fired entries from the log file as incident records."""
    from flask import jsonify
    entries = _read_log_file()
    fired   = [e for e in entries if "Alert fired" in e.get("message", "")]
    return jsonify(fired[-20:])


@dashboard_bp.route("/dashboard/runbook")
@_login_required
def runbook():
    return render_template("runbook.html")
