import json
import string
import random
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, redirect
from peewee import IntegrityError

from app.models.url import Url
from app.models.user import User
from app.models.event import Event

urls_bp = Blueprint("urls", __name__)


def _generate_short_code(length=6):
    chars = string.ascii_letters + string.digits
    return "".join(random.choices(chars, k=length))


def _url_to_dict(url):
    return {
        "id": url.id,
        "user_id": url.user_id,
        "short_code": url.short_code,
        "original_url": url.original_url,
        "title": url.title,
        "is_active": url.is_active,
        "created_at": url.created_at.isoformat() if url.created_at else None,
        "updated_at": url.updated_at.isoformat() if url.updated_at else None,
    }


@urls_bp.route("/urls", methods=["GET"])
def list_urls():
    urls = Url.select()
    return jsonify([_url_to_dict(u) for u in urls])


@urls_bp.route("/urls/<int:url_id>", methods=["GET"])
def get_url(url_id):
    url = Url.get_or_none(Url.id == url_id)
    if not url:
        return jsonify({"error": "URL not found"}), 404
    return jsonify(_url_to_dict(url))


@urls_bp.route("/shorten", methods=["POST"])
def shorten_url():
    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    original_url = data.get("url") or data.get("original_url")
    user_id = data.get("user_id")
    title = data.get("title", "")

    if not original_url:
        return jsonify({"error": "url is required"}), 400

    if user_id:
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
    if not data:
        return jsonify({"error": "Request body must be JSON"}), 400

    if "original_url" in data:
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
    return redirect(url.original_url, code=302)
