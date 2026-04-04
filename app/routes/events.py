from flask import Blueprint, jsonify

from app.models.event import Event

events_bp = Blueprint("events", __name__, url_prefix="/events")


@events_bp.route("", methods=["GET"])
def list_events():
    events = Event.select().order_by(Event.timestamp.desc()).limit(100)
    return jsonify([
        {
            "id": e.id,
            "url_id": e.url_id,
            "user_id": e.user_id,
            "event_type": e.event_type,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "details": e.details,
        }
        for e in events
    ])


@events_bp.route("/<int:event_id>", methods=["GET"])
def get_event(event_id):
    event = Event.get_or_none(Event.id == event_id)
    if not event:
        return jsonify({"error": "Event not found"}), 404
    return jsonify({
        "id": event.id,
        "url_id": event.url_id,
        "user_id": event.user_id,
        "event_type": event.event_type,
        "timestamp": event.timestamp.isoformat() if event.timestamp else None,
        "details": event.details,
    })
