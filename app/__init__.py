"""AI Laboratory Assessment System - Flask application factory."""
from __future__ import annotations

import json
import logging

from flask import Flask, g, render_template, request

from . import db
from .config import load_config
from .security import RateLimiter, csrf_token, install_log_redaction, verify_csrf

ERROR_TITLES = {
    400: "Bad request", 403: "Access denied", 404: "Not found",
    405: "Method not allowed", 413: "Submission too large", 429: "Too many requests",
    500: "Server error",
}


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.update(load_config())
    if test_config:
        app.config.update(test_config)

    install_log_redaction(app.config.get("GROQ_API_KEY", ""), app.config.get("ANTHROPIC_API_KEY", ""),
                          app.config.get("GEMINI_API_KEY", ""), app.config["SECRET_KEY"])
    if app.config.get("GENERATED_SECRET_KEY") and not app.config.get("TESTING"):
        app.logger.warning("FLASK_SECRET_KEY is not set; using a temporary key "
                           "(everyone is logged out when the server restarts).")

    db.init_app(app)
    app.extensions["ai_rate_limiter"] = RateLimiter()

    from .auth import bp as auth_bp, load_logged_in_user
    from .routes.api import bp as api_bp
    from .routes.shared import bp as shared_bp
    from .routes.student import bp as student_bp
    from .routes.teacher import bp as teacher_bp

    app.register_blueprint(auth_bp)
    app.register_blueprint(student_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(shared_bp)
    app.register_blueprint(api_bp)

    @app.before_request
    def _before():
        load_logged_in_user()
        verify_csrf()

    @app.after_request
    def _headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        return response

    @app.context_processor
    def _inject():
        from .services.ai_provider import get_ai_service

        service = get_ai_service(app.config)
        return {
            "csrf_token": csrf_token,
            "ai_configured": service.is_configured,
            "ai_model": service.model or None,
            "ai_name": service.display_name,
            "allow_fallback": app.config.get("ALLOW_RULE_BASED_FALLBACK", True),
        }

    @app.template_filter("fromjson")
    def _fromjson(value):
        try:
            return json.loads(value) if value else None
        except (TypeError, ValueError):
            return None

    def _error(exc):
        code = getattr(exc, "code", 500) or 500
        description = getattr(exc, "description", None) or "Something went wrong."
        if code == 500:
            description = "An unexpected error occurred. Please try again."
        if request.path.startswith("/api/"):
            return {"error": ERROR_TITLES.get(code, "Error"), "message": description}, code
        return render_template("error.html", code=code,
                               title=ERROR_TITLES.get(code, "Error"), message=description), code

    for code in ERROR_TITLES:
        app.register_error_handler(code, _error)

    if not app.config.get("TESTING"):
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    return app


def get_user():
    return getattr(g, "user", None)
