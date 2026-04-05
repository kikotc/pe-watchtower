import os
import time

import psutil
from flask import Blueprint, jsonify, request

from app.logging_config import get_log_buffer

observability_bp = Blueprint("observability", __name__)

_start_time = time.time()


@observability_bp.route("/metrics")
def metrics():
    process = psutil.Process(os.getpid())
    mem = psutil.virtual_memory()

    uptime_seconds = int(time.time() - _start_time)

    return jsonify({
        "uptime_seconds": uptime_seconds,
        "cpu_percent": psutil.cpu_percent(interval=0.1),
        "memory": {
            "used_mb": round(mem.used / 1024 / 1024, 1),
            "total_mb": round(mem.total / 1024 / 1024, 1),
            "percent": mem.percent,
        },
        "process": {
            "pid": process.pid,
            "memory_mb": round(process.memory_info().rss / 1024 / 1024, 1),
            "cpu_percent": process.cpu_percent(interval=0.1),
            "threads": process.num_threads(),
        },
    })


@observability_bp.route("/logs")
def logs():
    return jsonify(get_log_buffer())


@observability_bp.route("/slo")
def slo():
    from app.logging_config import get_log_buffer
    buf = get_log_buffer()
    now = time.time()
    window = 86400  # 24 hours
    cutoff = now - window

    requests_in_window = [
        e for e in buf
        if e.get("message") == "request"
        and isinstance(e.get("timestamp"), str)
        and _iso_to_epoch(e["timestamp"]) >= cutoff
    ]

    total = len(requests_in_window)
    errors = [e for e in requests_in_window if e.get("status", 0) >= 500]
    successful = total - len(errors)

    uptime_pct = round((successful / total * 100), 3) if total > 0 else 100.0

    durations = [e["duration_ms"] for e in requests_in_window if "duration_ms" in e]
    avg_latency = round(sum(durations) / len(durations), 2) if durations else 0
    p99_latency = round(sorted(durations)[int(len(durations) * 0.99)], 2) if len(durations) >= 10 else None

    return jsonify({
        "window": "24h",
        "uptime_percent": uptime_pct,
        "total_requests": total,
        "successful_requests": successful,
        "error_requests": len(errors),
        "avg_latency_ms": avg_latency,
        "p99_latency_ms": p99_latency,
    })


def _iso_to_epoch(iso: str) -> float:
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).timestamp()
    except Exception:
        return 0.0


@observability_bp.route("/incidents/record", methods=["POST"])
def record_incident():
    from app.models.incident import Incident
    from datetime import datetime, timezone
    data = request.get_json(silent=True) or {}
    incident_type = data.get("type")
    if not incident_type:
        return jsonify({"error": "type required"}), 400

    started_at = datetime.fromisoformat(data["started_at"]) if "started_at" in data else datetime.now(timezone.utc)
    resolved_at = datetime.fromisoformat(data["resolved_at"]) if "resolved_at" in data else None

    inc = Incident.create(
        incident_type=incident_type,
        started_at=started_at,
        resolved_at=resolved_at,
        duration_seconds=data.get("duration_seconds"),
        details=data.get("details", ""),
    )
    return jsonify({"id": inc.id}), 201


@observability_bp.route("/incidents")
def incidents():
    from app.models.incident import Incident
    rows = Incident.select().order_by(Incident.started_at.desc()).limit(50)
    return jsonify([
        {
            "id": i.id,
            "type": i.incident_type,
            "started_at": i.started_at.isoformat(),
            "resolved_at": i.resolved_at.isoformat() if i.resolved_at else None,
            "duration_seconds": i.duration_seconds,
            "details": i.details,
        }
        for i in rows
    ])


@observability_bp.route("/debug/crash")
def debug_crash():
    """Test endpoint: returns 500 to trigger high-error-rate alerting."""
    return jsonify({"error": "simulated server error"}), 500
