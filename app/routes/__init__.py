def register_routes(app):
    from app.routes.users import users_bp
    from app.routes.urls import urls_bp
    from app.routes.events import events_bp
    from app.routes.observability import observability_bp
    from app.routes.dashboard import dashboard_bp

    app.register_blueprint(users_bp)
    app.register_blueprint(urls_bp)
    app.register_blueprint(events_bp)
    app.register_blueprint(observability_bp)
    app.register_blueprint(dashboard_bp)
