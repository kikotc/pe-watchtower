import json
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request

from app.models.event import Event

events_bp = Blueprint("events", __name__, url_prefix="/events")


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
