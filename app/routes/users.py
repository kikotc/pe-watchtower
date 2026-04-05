import csv
import io
import os

from flask import Blueprint, jsonify, request
from peewee import IntegrityError

from app.models.user import User

users_bp = Blueprint("users", __name__, url_prefix="/users")

_SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "seed_data")


@users_bp.route("", methods=["GET"])
def list_users():
    page = request.args.get("page", None, type=int)
    per_page = request.args.get("per_page", 25, type=int)

    if page is not None:
        query = User.select().order_by(User.id).paginate(page, per_page)
    else:
        query = User.select().order_by(User.id)

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
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    username = data.get("username")
    email = data.get("email")

    if not username or not email:
        return jsonify({"error": "username and email are required"}), 400

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
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

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
