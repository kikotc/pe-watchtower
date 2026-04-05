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
            "started_at": i.started_at.isoformat() + "Z",
            "resolved_at": (i.resolved_at.isoformat() + "Z") if i.resolved_at else None,
            "duration_seconds": i.duration_seconds,
            "details": i.details,
        }
        for i in rows
    ])


@observability_bp.route("/uptime-history")
def uptime_history():
    """Return per-day uptime stats for the last 30 days, computed from the log file."""
    import json
    from datetime import datetime, timezone, timedelta

    log_path = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "logs", "app.log")
    )

    # Build a dict of date → {total, errors}
    days: dict[str, dict] = {}
    try:
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("message") != "request":
                    continue
                ts = entry.get("timestamp", "")
                try:
                    day = datetime.fromisoformat(ts).strftime("%Y-%m-%d")
                except Exception:
                    continue
                if day not in days:
                    days[day] = {"total": 0, "errors": 0}
                days[day]["total"] += 1
                if entry.get("status", 0) >= 500:
                    days[day]["errors"] += 1
    except FileNotFoundError:
        pass

    # Build last 30 days in order, filling gaps with None
    result = []
    today = datetime.now(timezone.utc).date()
    for i in range(29, -1, -1):
        day = (today - timedelta(days=i)).isoformat()
        stats = days.get(day)
        if stats and stats["total"] > 0:
            uptime = round((stats["total"] - stats["errors"]) / stats["total"] * 100, 1)
        else:
            uptime = None
        result.append({"date": day, "uptime": uptime,
                        "total": stats["total"] if stats else 0,
                        "errors": stats["errors"] if stats else 0})

    return jsonify(result)


@observability_bp.route("/error-classification")
def error_classification():
    """Classify 500 errors from the log buffer by root cause."""
    buf = get_log_buffer()

    # Classify rules: (label, keywords to match in exception field)
    RULES = [
        ("db_unavailable",   ["OperationalError", "InterfaceError", "connection", "SSL connection"]),
        ("data_integrity",   ["IntegrityError", "unique", "NOT NULL", "duplicate"]),
        ("timeout",          ["timeout", "Timeout", "timed out"]),
        ("connection_error", ["ConnectionError", "ConnectionRefused", "NewConnectionError"]),
        ("not_found",        ["404", "not found", "DoesNotExist"]),
    ]

    counts: dict[str, int] = {}
    endpoints: dict[str, int] = {}
    unclassified = 0

    for entry in buf:
        if entry.get("status", 0) < 500:
            continue
        exc = entry.get("exception", "") or ""
        path = entry.get("path", "unknown")

        matched = False
        for label, keywords in RULES:
            if any(kw.lower() in exc.lower() for kw in keywords):
                counts[label] = counts.get(label, 0) + 1
                endpoints[path] = endpoints.get(path, 0) + 1
                matched = True
                break
        if not matched and exc:
            counts["unclassified"] = counts.get("unclassified", 0) + 1
            endpoints[path] = endpoints.get(path, 0) + 1
            unclassified += 1

    total_errors = sum(counts.values())
    top_endpoints = sorted(endpoints.items(), key=lambda x: x[1], reverse=True)[:5]

    return jsonify({
        "total_errors": total_errors,
        "breakdown": [
            {"type": k, "count": v, "percent": round(v / total_errors * 100, 1)}
            for k, v in sorted(counts.items(), key=lambda x: x[1], reverse=True)
        ] if total_errors else [],
        "top_error_endpoints": [
            {"path": p, "count": c} for p, c in top_endpoints
        ],
    })


@observability_bp.route("/debug/crash")
def debug_crash():
    """Test endpoint: returns 500 to trigger high-error-rate alerting."""
    return jsonify({"error": "simulated server error"}), 500
