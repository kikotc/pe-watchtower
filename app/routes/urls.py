import csv
import io
import json
import os
import string
import random
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, redirect
from peewee import IntegrityError

from app.models.url import Url
from app.models.user import User
from app.models.event import Event

urls_bp = Blueprint("urls", __name__)

_SEED_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "seed_data")


def _generate_short_code(length=6):
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=length))


def _url_to_dict(url):
    clicks = Event.select().where(
        (Event.url == url.id) & (Event.event_type == "click")
    ).count()
    return {
        "id": url.id,
        "user_id": url.user_id,
        "short_code": url.short_code,
        "original_url": url.original_url,
        "title": url.title,
        "is_active": url.is_active,
        "clicks": clicks,
        "created_at": url.created_at.isoformat() if url.created_at else None,
        "updated_at": url.updated_at.isoformat() if url.updated_at else None,
    }


@urls_bp.route("/urls", methods=["GET", "POST"])
def list_or_create_urls():
    if request.method == "POST":
        data = request.get_json(silent=True)
        if not data or not isinstance(data, dict):
            return jsonify({"error": "Request body must be a JSON object"}), 400

        original_url = data.get("url") or data.get("original_url")
        user_id = data.get("user_id")
        title = data.get("title", "")

        if not original_url:
            return jsonify({"error": "url is required"}), 400

        if not isinstance(original_url, str):
            return jsonify({"error": "url must be a string"}), 400

        if user_id is not None:
            if not isinstance(user_id, int):
                return jsonify({"error": "user_id must be an integer"}), 400
            user = User.get_or_none(User.id == user_id)
            if not user:
                return jsonify({"error": "User not found"}), 404

        for _ in range(10):
            short_code = _generate_short_code()
            if not Url.get_or_none(Url.short_code == short_code):
                break
        else:
            return jsonify({"error": "Failed to generate unique short code"}), 500

        now = datetime.now(timezone.utc)
        try:
            url = Url.create(
                user=user_id,
                short_code=short_code,
                original_url=original_url,
                title=title,
                is_active=True,
                created_at=now,
                updated_at=now,
            )
        except IntegrityError as e:
            if "short_code" in str(e):
                return jsonify({"error": "Short code collision"}), 409
            return jsonify({"error": "Database error creating URL"}), 500

        Event.create(
            url=url.id,
            user=user_id,
            event_type="created",
            timestamp=now,
            details=json.dumps({"short_code": short_code, "original_url": original_url}),
        )

        return jsonify(_url_to_dict(url)), 201

    # GET
    query = Url.select()

    user_id = request.args.get("user_id", type=int)
    if user_id is not None:
        query = query.where(Url.user == user_id)

    is_active = request.args.get("is_active")
    if is_active is not None:
        query = query.where(Url.is_active == (is_active.lower() in ("true", "1")))

    short_code = request.args.get("short_code")
    if short_code is not None:
        query = query.where(Url.short_code == short_code)

    return jsonify([_url_to_dict(u) for u in query])


@urls_bp.route("/urls/<int:url_id>", methods=["GET"])
def get_url(url_id):
    url = Url.get_or_none(Url.id == url_id)
    if not url:
        return jsonify({"error": "URL not found"}), 404
    return jsonify(_url_to_dict(url))


@urls_bp.route("/shorten", methods=["POST"])
def shorten_url():
    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    original_url = data.get("url") or data.get("original_url")
    user_id = data.get("user_id")
    title = data.get("title", "")

    if not original_url:
        return jsonify({"error": "url is required"}), 400

    if not isinstance(original_url, str):
        return jsonify({"error": "url must be a string"}), 400

    if user_id is not None:
        if not isinstance(user_id, int):
            return jsonify({"error": "user_id must be an integer"}), 400
        user = User.get_or_none(User.id == user_id)
        if not user:
            return jsonify({"error": "User not found"}), 404

    # Generate a unique short code
    for _ in range(10):
        short_code = _generate_short_code()
        if not Url.get_or_none(Url.short_code == short_code):
            break
    else:
        return jsonify({"error": "Failed to generate unique short code"}), 500

    now = datetime.now(timezone.utc)
    try:
        url = Url.create(
            user=user_id,
            short_code=short_code,
            original_url=original_url,
            title=title,
            is_active=True,
            created_at=now,
            updated_at=now,
        )
    except IntegrityError as e:
        if "short_code" in str(e):
            return jsonify({"error": "Short code collision"}), 409
        return jsonify({"error": "Database error creating URL"}), 500

    # Log the creation event
    Event.create(
        url=url.id,
        user=user_id,
        event_type="created",
        timestamp=now,
        details=json.dumps({"short_code": short_code, "original_url": original_url}),
    )

    return jsonify(_url_to_dict(url)), 201


@urls_bp.route("/urls/<int:url_id>", methods=["PUT"])
def update_url(url_id):
    url = Url.get_or_none(Url.id == url_id)
    if not url:
        return jsonify({"error": "URL not found"}), 404

    data = request.get_json(silent=True)
    if not data or not isinstance(data, dict):
        return jsonify({"error": "Request body must be a JSON object"}), 400

    if "original_url" in data:
        if not isinstance(data["original_url"], str):
            return jsonify({"error": "original_url must be a string"}), 400
        url.original_url = data["original_url"]
    if "title" in data:
        url.title = data["title"]
    if "is_active" in data:
        url.is_active = data["is_active"]

    url.updated_at = datetime.now(timezone.utc)
    url.save()

    Event.create(
        url=url.id,
        user=url.user_id,
        event_type="updated",
        timestamp=url.updated_at,
        details=json.dumps({"fields_updated": list(data.keys())}),
    )

    return jsonify(_url_to_dict(url))


@urls_bp.route("/urls/<int:url_id>", methods=["DELETE"])
def delete_url(url_id):
    url = Url.get_or_none(Url.id == url_id)
    if not url:
        return jsonify({"error": "URL not found"}), 404

    now = datetime.now(timezone.utc)
    Event.create(
        url=url.id,
        user=url.user_id,
        event_type="deleted",
        timestamp=now,
        details=json.dumps({"short_code": url.short_code}),
    )

    url.delete_instance()
    return jsonify({"message": "URL deleted"}), 200


@urls_bp.route("/<short_code>", methods=["GET"])
def redirect_short(short_code):
    url = Url.get_or_none(Url.short_code == short_code)
    if not url:
        return jsonify({"error": "Short URL not found"}), 404
    if not url.is_active:
        return jsonify({"error": "This short URL is no longer active"}), 410

    # Record click event
    Event.create(
        url=url.id,
        user=url.user_id,
        event_type="click",
        timestamp=datetime.now(timezone.utc),
        details=json.dumps({
            "referrer": request.headers.get("Referer", ""),
            "user_agent": request.headers.get("User-Agent", ""),
        }),
    )

    return redirect(url.original_url, code=301)


@urls_bp.route("/urls/bulk", methods=["POST"])
def bulk_load_urls():
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
        original_url = row.get("original_url", "").strip()
        short_code = row.get("short_code", "").strip() or _generate_short_code()
        title = row.get("title", "").strip()
        user_id = row.get("user_id") or row.get("user") or None
        is_active = row.get("is_active", "true").strip().lower() in ("true", "1", "t", "yes")

        if not original_url:
            errors.append({"row": i, "error": "missing original_url"})
            continue
        try:
            if user_id:
                user_id = int(user_id)
        except (ValueError, TypeError):
            user_id = None

        try:
            Url.create(
                user=user_id,
                short_code=short_code,
                original_url=original_url,
                title=title,
                is_active=is_active,
                created_at=now,
                updated_at=now,
            )
            created += 1
        except IntegrityError:
            errors.append({"row": i, "error": f"duplicate short_code: {short_code}"})

    return jsonify({
        "file": filename,
        "row_count": created,
        "imported": created,
        "created": created,
        "errors": errors,
        "message": f"Successfully imported {created} URLs",
    }), 201
