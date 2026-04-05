from flask import Blueprint

# All dashboard UI and data routes have moved to monitor.py (port 5002).
# This blueprint is kept so routes/__init__.py doesn't need changes.
dashboard_bp = Blueprint("dashboard", __name__)
