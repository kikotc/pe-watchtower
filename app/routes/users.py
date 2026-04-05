import csv
import io
import os

from flask import Blueprint, jsonify, request
from peewee import IntegrityError

from app.models.user import User
from app.models.url import Url
from app.models.event import Event

users_bp = Blueprint("users", __name__, url_prefix="/users")

_SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "seed_data")


@users_bp.route("", methods=["GET"])
def list_users():
    page = request.args.get("page", None, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    query = User.select().order_by(User.id)

    username = request.args.get("username")
    if username:
        query = query.where(User.username == username)

    email = request.args.get("email")
    if email:
        query = query.where(User.email == email)

    if page is not None:
        query = query.paginate(page, per_page)

    return jsonify([
        {
            "id": u.id,
            "username": u.username,
            "email": u.email,
            "created_at": u.created_at.isoformat() if u.created_at else None,
        }
        for u in query
    ])


@users_bp.route("/bulk", methods=["POST"])
def bulk_load_users():
    from datetime import datetime, timezone

    # Support JSON body with {"file": "users.csv"} — load from seed_data/
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
    now = datetime.now(timezone.utc)

    for i, row in enumerate(rows, start=1):
        username = row.get("username", "").strip()
        email = row.get("email", "").strip()
        if not username or not email:
            errors.append({"row": i, "error": "missing username or email"})
            continue
        try:
            User.create(username=username, email=email, created_at=now)
            created += 1
        except IntegrityError:
            errors.append({"row": i, "error": f"duplicate email: {email}"})

    return jsonify({
        "file": filename,
        "row_count": created,
        "imported": created,
        "created": created,
        "errors": errors,
        "message": f"Successfully imported {created} users",
    }), 201


@users_bp.route("/<int:user_id>", methods=["GET"])
def get_user(user_id):
    user = User.get_or_none(User.id == user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    return jsonify({
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    })


@users_bp.route("", methods=["POST"])
def create_user():
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    username = data.get("username")
    email = data.get("email")

    if not username or not email:
        return jsonify({"error": "username and email are required"}), 400

    if not isinstance(username, str) or not isinstance(email, str):
        return jsonify({"error": "username and email must be strings"}), 400

    try:
        from datetime import datetime, timezone

        user = User.create(
            username=username,
            email=email,
            created_at=datetime.now(timezone.utc),
        )
    except IntegrityError:
        return jsonify({"error": "Email already exists"}), 409

    return jsonify({
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }), 201


@users_bp.route("/<int:user_id>", methods=["PUT"])
def update_user(user_id):
    user = User.get_or_none(User.id == user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    if "username" in data and not isinstance(data["username"], str):
        return jsonify({"error": "username must be a string"}), 400
    if "email" in data and not isinstance(data["email"], str):
        return jsonify({"error": "email must be a string"}), 400

    try:
        if "username" in data:
            user.username = data["username"]
        if "email" in data:
            user.email = data["email"]
        user.save()
    except IntegrityError:
        return jsonify({"error": "Email already exists"}), 409

    return jsonify({
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "created_at": user.created_at.isoformat() if user.created_at else None,
    })


@users_bp.route("/<int:user_id>", methods=["DELETE"])
def delete_user(user_id):
    user = User.get_or_none(User.id == user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404
    user.delete_instance(recursive=True)
    return jsonify({"message": "User deleted"}), 200


@users_bp.route("/<int:user_id>/events", methods=["GET"])
def get_user_events(user_id):
    user = User.get_or_none(User.id == user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    import json as _json
    events = Event.select().where(Event.user == user_id).order_by(Event.timestamp.desc())
    results = []
    for e in events:
        details = e.details
        if isinstance(details, str):
            try:
                details = _json.loads(details)
            except (_json.JSONDecodeError, TypeError):
                pass
        results.append({
            "id": e.id,
            "url_id": e.url_id,
            "user_id": e.user_id,
            "event_type": e.event_type,
            "timestamp": e.timestamp.isoformat() if e.timestamp else None,
            "details": details,
        })
    return jsonify(results)


@users_bp.route("/<int:user_id>/urls", methods=["GET"])
def get_user_urls(user_id):
    user = User.get_or_none(User.id == user_id)
    if not user:
        return jsonify({"error": "User not found"}), 404

    urls = Url.select().where(Url.user == user_id).order_by(Url.id)
    results = []
    for u in urls:
        clicks = Event.select().where(
            (Event.url == u.id) & (Event.event_type == "click")
        ).count()
        results.append({
            "id": u.id,
            "user_id": u.user_id,
            "short_code": u.short_code,
            "original_url": u.original_url,
            "title": u.title,
            "is_active": u.is_active,
            "clicks": clicks,
            "created_at": u.created_at.isoformat() if u.created_at else None,
            "updated_at": u.updated_at.isoformat() if u.updated_at else None,
        })
    return jsonify(results)
