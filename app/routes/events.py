import csv
import io
import json
import os
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request
from peewee import fn

from app.models.event import Event

events_bp = Blueprint("events", __name__, url_prefix="/events")

_SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "seed_data")


def _event_to_dict(e):
    details = e.details
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except (json.JSONDecodeError, TypeError):
            pass
    return {
        "id": e.id,
        "url_id": e.url_id,
        "user_id": e.user_id,
        "event_type": e.event_type,
        "timestamp": e.timestamp.isoformat() if e.timestamp else None,
        "details": details,
    }


@events_bp.route("", methods=["GET", "POST"])
def list_or_create_events():
    if request.method == "POST":
        data = request.get_json(silent=True)
        if not data:
            return jsonify({"error": "Request body must be JSON"}), 400

        url_id = data.get("url_id")
        user_id = data.get("user_id")
        event_type = data.get("event_type")

        if not event_type:
            return jsonify({"error": "event_type is required"}), 400

        details = data.get("details", {})
        if isinstance(details, dict):
            details = json.dumps(details)

        event = Event.create(
            url=url_id,
            user=user_id,
            event_type=event_type,
            timestamp=datetime.now(timezone.utc),
            details=details,
        )
        return jsonify(_event_to_dict(event)), 201

    # GET with optional filters
    query = Event.select().order_by(Event.timestamp.desc())

    url_id = request.args.get("url_id", type=int)
    if url_id is not None:
        query = query.where(Event.url == url_id)

    user_id = request.args.get("user_id", type=int)
    if user_id is not None:
        query = query.where(Event.user == user_id)

    event_type = request.args.get("event_type")
    if event_type is not None:
        query = query.where(Event.event_type == event_type)

    return jsonify([_event_to_dict(e) for e in query.limit(100)])


@events_bp.route("/<int:event_id>", methods=["GET"])
def get_event(event_id):
    event = Event.get_or_none(Event.id == event_id)
    if not event:
        return jsonify({"error": "Event not found"}), 404
    return jsonify(_event_to_dict(event))


@events_bp.route("/<int:event_id>", methods=["PUT"])
def update_event(event_id):
    event = Event.get_or_none(Event.id == event_id)
    if not event:
        return jsonify({"error": "Event not found"}), 404

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    if "event_type" in data:
        event.event_type = data["event_type"]
    if "details" in data:
        details = data["details"]
        if isinstance(details, dict):
            details = json.dumps(details)
        event.details = details
    if "url_id" in data:
        event.url = data["url_id"]
    if "user_id" in data:
        event.user = data["user_id"]

    event.save()
    return jsonify(_event_to_dict(event))


@events_bp.route("/<int:event_id>", methods=["DELETE"])
def delete_event(event_id):
    event = Event.get_or_none(Event.id == event_id)
    if not event:
        return jsonify({"error": "Event not found"}), 404
    event.delete_instance()
    return jsonify({"message": "Event deleted"}), 200


@events_bp.route("/bulk", methods=["POST"])
def bulk_load_events():
    data = request.get_json(silent=True)
    if data and "file" in data:
        filename = data["file"]
        filepath = os.path.join(_SEED_DIR, os.path.basename(filename))
        if not os.path.exists(filepath):
            return jsonify({"error": f"File not found: {filename}"}), 404
        with open(filepath, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
    elif "file" in request.files:
        f = request.files["file"]
        stream = io.StringIO(f.stream.read().decode("utf-8"))
        reader = csv.DictReader(stream)
        rows = list(reader)
        filename = f.filename
    else:
        return jsonify({"error": "No file provided"}), 400

    created = 0
    errors = []

    for i, row in enumerate(rows, start=1):
        url_id = row.get("url_id") or row.get("url") or None
        user_id = row.get("user_id") or row.get("user") or None
        event_type = row.get("event_type", "").strip()
        timestamp = row.get("timestamp", "").strip()
        details = row.get("details", "{}")

        if not event_type:
            errors.append({"row": i, "error": "missing event_type"})
            continue

        try:
            url_id = int(url_id) if url_id else None
        except (ValueError, TypeError):
            url_id = None
        try:
            user_id = int(user_id) if user_id else None
        except (ValueError, TypeError):
            user_id = None

        ts = datetime.now(timezone.utc)
        if timestamp:
            try:
                ts = datetime.fromisoformat(timestamp)
            except ValueError:
                pass

        try:
            Event.create(
                url=url_id,
                user=user_id,
                event_type=event_type,
                timestamp=ts,
                details=details,
            )
            created += 1
        except Exception:
            errors.append({"row": i, "error": "failed to create event"})

    return jsonify({
        "file": filename,
        "row_count": created,
        "imported": created,
        "created": created,
        "errors": errors,
        "message": f"Successfully imported {created} events",
    }), 201


@events_bp.route("/stats", methods=["GET"])
def event_stats():
    """Return aggregated event statistics."""
    query = Event.select(
        Event.event_type,
        fn.COUNT(Event.id).alias("count")
    ).group_by(Event.event_type)

    url_id = request.args.get("url_id", type=int)
    if url_id is not None:
        query = query.where(Event.url == url_id)

    user_id = request.args.get("user_id", type=int)
    if user_id is not None:
        query = query.where(Event.user == user_id)

    breakdown = [{"event_type": row.event_type, "count": row.count} for row in query]
    total = sum(r["count"] for r in breakdown)

    return jsonify({
        "total": total,
        "breakdown": breakdown,
    })
