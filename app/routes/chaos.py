"""
Chaos Engineering endpoints — inject faults into the running Flask app.
Each experiment is toggled on/off and applied via before_request middleware.
"""
import os
import random
import signal
import threading
import time
import uuid

from flask import Blueprint, g, jsonify, request

chaos_bp = Blueprint("chaos", __name__)

# ── Chaos state (in-memory) ─────────────────────────────────────────────────

_state = {
    "latency":    {"active": False, "delay_ms": 0},
    "error_rate": {"active": False, "rate": 0.0},
    "db_kill":    {"active": False},
    "cpu_stress": {"active": False},
}
_cpu_thread = None
_cpu_stop   = threading.Event()
_traffic_thread = None
_traffic_stop   = threading.Event()


def get_chaos_state():
    return {k: dict(v) for k, v in _state.items()}


def is_any_active():
    return any(v.get("active") for v in _state.values())


# ── Middleware (registered by init_chaos) ────────────────────────────────────

def init_chaos(app):
    """Call from the app factory to wire up chaos middleware."""

    @app.before_request
    def _apply_chaos():
        # Ensure request tracking is set (we run before _start_timer)
        if not hasattr(g, "start_time"):
            g.start_time = time.monotonic()
        if not hasattr(g, "request_id"):
            g.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]

        # Skip internal/observability endpoints
        skip = ("/chaos", "/health", "/metrics", "/logs", "/slo",
                "/incidents", "/uptime-history", "/error-classification",
                "/debug")
        if any(request.path.startswith(p) for p in skip):
            return

        # Latency injection
        if _state["latency"]["active"]:
            delay_s = _state["latency"]["delay_ms"] / 1000
            time.sleep(delay_s)

        # Random 500 errors
        if _state["error_rate"]["active"]:
            if random.random() < _state["error_rate"]["rate"]:
                return jsonify({
                    "error": "Chaos: injected failure",
                    "chaos": True,
                }), 500

        # DB kill — prevent DB connection for this request
        if _state["db_kill"]["active"]:
            try:
                from app.database import db
                if not db.is_closed():
                    db.close()
                # Monkey-patch connect to no-op so _db_connect can't reopen
                if not hasattr(db.obj, "_chaos_orig_connect"):
                    db.obj._chaos_orig_connect = db.obj.connect
                    db.obj.connect = lambda *a, **k: None
            except Exception:
                pass


# ── API endpoints ────────────────────────────────────────────────────────────

@chaos_bp.route("/chaos/status", methods=["GET"])
def chaos_status():
    return jsonify({
        "experiments": get_chaos_state(),
        "any_active": is_any_active(),
    })


@chaos_bp.route("/chaos/latency", methods=["POST"])
def chaos_latency():
    data = request.get_json(silent=True) or {}
    delay = int(data.get("delay_ms", 2000))
    _state["latency"]["active"]   = True
    _state["latency"]["delay_ms"] = delay
    return jsonify({"status": "activated", "delay_ms": delay})


@chaos_bp.route("/chaos/error-rate", methods=["POST"])
def chaos_error_rate():
    data = request.get_json(silent=True) or {}
    rate = min(float(data.get("rate", 0.5)), 1.0)
    _state["error_rate"]["active"] = True
    _state["error_rate"]["rate"]   = rate
    return jsonify({"status": "activated", "rate": rate})


@chaos_bp.route("/chaos/db-kill", methods=["POST"])
def chaos_db_kill():
    _state["db_kill"]["active"] = True
    try:
        from app.database import db
        # Monkey-patch connect so it can't reopen
        if not hasattr(db.obj, "_chaos_orig_connect"):
            db.obj._chaos_orig_connect = db.obj.connect
            db.obj.connect = lambda *a, **k: None
        if not db.is_closed():
            db.close()
    except Exception:
        pass
    return jsonify({"status": "activated", "experiment": "db_kill"})


@chaos_bp.route("/chaos/cpu-stress", methods=["POST"])
def chaos_cpu_stress():
    global _cpu_thread
    data = request.get_json(silent=True) or {}
    duration = int(data.get("duration_s", 30))
    _state["cpu_stress"]["active"] = True
    _cpu_stop.clear()

    def _burn():
        end = time.time() + duration
        while time.time() < end and not _cpu_stop.is_set():
            _ = sum(i * i for i in range(10000))
        _state["cpu_stress"]["active"] = False

    _cpu_thread = threading.Thread(target=_burn, daemon=True)
    _cpu_thread.start()
    return jsonify({"status": "activated", "duration_s": duration})


@chaos_bp.route("/chaos/crash", methods=["POST"])
def chaos_crash():
    """Kill the Flask process — the monitor's self-healing will restart it."""
    def _kill():
        time.sleep(0.5)
        os.kill(os.getpid(), signal.SIGTERM)

    threading.Thread(target=_kill, daemon=True).start()
    return jsonify({"status": "crashing", "pid": os.getpid()})


@chaos_bp.route("/chaos/traffic", methods=["POST"])
def chaos_traffic():
    """Generate synthetic traffic to user-facing endpoints so chaos effects show up."""
    global _traffic_thread
    data = request.get_json(silent=True) or {}
    duration = int(data.get("duration_s", 60))
    rps = int(data.get("rps", 5))
    _traffic_stop.clear()

    import requests as http_requests

    def _generate():
        import random as rnd
        endpoints = ["/urls", "/users", "/events"]
        end = time.time() + duration
        while time.time() < end and not _traffic_stop.is_set():
            try:
                url = f"http://localhost:5001{rnd.choice(endpoints)}"
                http_requests.get(url, timeout=10)
            except Exception:
                pass
            time.sleep(1.0 / rps)

    _traffic_thread = threading.Thread(target=_generate, daemon=True)
    _traffic_thread.start()
    return jsonify({"status": "generating", "duration_s": duration, "rps": rps})


@chaos_bp.route("/chaos/clear", methods=["POST"])
def chaos_clear():
    data = request.get_json(silent=True) or {}
    target = data.get("experiment")

    # Clear a single experiment
    if target:
        if target == "latency":
            _state["latency"]["active"] = False
            _state["latency"]["delay_ms"] = 0
        elif target == "error_rate":
            _state["error_rate"]["active"] = False
            _state["error_rate"]["rate"] = 0.0
        elif target == "cpu_stress":
            _state["cpu_stress"]["active"] = False
            _cpu_stop.set()
        elif target == "db_kill":
            _state["db_kill"]["active"] = False
            try:
                from app.database import db
                if hasattr(db.obj, "_chaos_orig_connect"):
                    db.obj.connect = db.obj._chaos_orig_connect
                    del db.obj._chaos_orig_connect
                db.connect(reuse_if_open=True)
            except Exception:
                pass
        return jsonify({"status": "cleared", "experiment": target})

    # Clear all
    _state["latency"]["active"]    = False
    _state["latency"]["delay_ms"]  = 0
    _state["error_rate"]["active"] = False
    _state["error_rate"]["rate"]   = 0.0
    _state["cpu_stress"]["active"] = False
    _cpu_stop.set()
    _traffic_stop.set()

    if _state["db_kill"]["active"]:
        _state["db_kill"]["active"] = False
        try:
            from app.database import db
            if hasattr(db.obj, "_chaos_orig_connect"):
                db.obj.connect = db.obj._chaos_orig_connect
                del db.obj._chaos_orig_connect
            db.connect(reuse_if_open=True)
        except Exception:
            pass
    else:
        _state["db_kill"]["active"] = False

    return jsonify({"status": "all_cleared"})
