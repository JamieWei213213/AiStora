# app.py
import os
import hashlib
from pathlib import Path
from werkzeug.exceptions import HTTPException

from flask import Flask, current_app, jsonify, request
from flask_migrate import Migrate
from sqlalchemy import text
from werkzeug.middleware.proxy_fix import ProxyFix

from config import Config
from extensions import db
from services.llm_service import configure_llm
from services.logger import get_logger
from services.session_store import configure_sessions

from routes.pages import pages_bp
from routes.auth import auth_bp
from routes.data import data_bp
from routes.chat import chat_bp
from routes.databases import databases_bp
from routes.tables import tables_bp
from routes.eda import eda_bp
from routes.loads import loads_bp
from routes.connectors import connectors_bp


logger = get_logger(__name__)
migrate = Migrate()


def apply_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )

    # Defence in depth behind the output escaping in static/js/scripts.js.
    # Uploaded CSV column names are rendered in the schema drawer, and a CSV is
    # by definition an untrusted file from a client.
    policy = Config.CONTENT_SECURITY_POLICY
    if policy:
        response.headers.setdefault("Content-Security-Policy", policy)

    hsts_seconds = int(getattr(Config, "HSTS_SECONDS", 0) or 0)
    if hsts_seconds > 0 and request.is_secure:
        response.headers.setdefault(
            "Strict-Transport-Security",
            f"max-age={hsts_seconds}; includeSubDomains",
        )

    if request.path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _warn_about_insecure_settings(app):
    if app.config.get("IS_PRODUCTION") and not app.config.get("SESSION_COOKIE_SECURE"):
        logger.warning(
            "SESSION_COOKIE_SECURE is disabled in production. Session cookies "
            "will be sent over plain HTTP and can be read in transit. Terminate "
            "TLS at the load balancer (set the Terraform certificate_arn "
            "variable) and remove SESSION_COOKIE_SECURE=false."
        )
    if app.config.get("IS_PRODUCTION") and app.config.get("SESSION_TYPE") != "redis":
        logger.warning(
            "Running in production without a Redis session backend. Sessions "
            "are stored per container and will not be shared across tasks."
        )


def register_error_handlers(app):
    """JSON error bodies for limits the UI must be able to read.

    Werkzeug answers an oversized request with an HTML page, which the
    frontend's ``response.json()`` cannot parse, so the user saw a generic
    "upload failed" with no mention of the size limit.
    """

    @app.errorhandler(HTTPException)
    def api_http_error(error):
        if not request.path.startswith("/api/"):
            return error
        messages = {404: "This action could not be found. Refresh the page and try again.",
                    405: "This action is not supported. Refresh the page and try again."}
        message = messages.get(error.code, "We couldn't complete this request. Please try again.")
        return jsonify(success=False, error=message, error_type="request_error"), error.code

    @app.errorhandler(500)
    def api_server_error(error):
        db.session.rollback()
        if not request.path.startswith("/api/"):
            return error
        return jsonify(success=False, error="AIStora is temporarily unavailable. Please try again shortly.",
                       error_type="server_error"), 500

    @app.errorhandler(413)
    def request_too_large(_error):
        limit_mb = round(int(app.config.get("MAX_CONTENT_LENGTH", 0) or 0) / (1024 * 1024), 1)
        message = f"The upload is too large. The limit is {limit_mb:g} MB per request."
        return jsonify({
            "success": False,
            "type": "error",
            "error": message,
            "data": message,
            "error_type": "input_limit",
        }), 413


def configure_telemetry(app):
    """Point the process-wide event sink at the app's lake.

    Telemetry is never load-bearing: with the pipeline disabled, or the
    lake misconfigured, events are dropped and the app runs as before.
    """
    from pipeline import events as pipeline_events

    if not app.config.get("PIPELINE_ENABLED", True) or app.config.get("TESTING"):
        pipeline_events.configure_sink(None)
        return
    try:
        with app.app_context():
            from services.load_service import pipeline_context

            ctx = pipeline_context()
        sink = pipeline_events.EventSink(ctx.settings, ctx.store)
        pipeline_events.configure_sink(sink)
    except Exception:
        logger.exception("Telemetry sink could not be configured; events are disabled")
        pipeline_events.configure_sink(None)


def create_app(config_object=Config):
    app = Flask(__name__)
    app.config.from_object(config_object)
    # One content version per process/release, rather than a cache-busting timestamp
    # on every page load. File names also contribute to the digest.
    digest = hashlib.sha256()
    for asset in sorted(Path(app.static_folder).rglob("*")):
        if asset.is_file():
            digest.update(asset.relative_to(app.static_folder).as_posix().encode())
            digest.update(asset.read_bytes())
    app.config["ASSET_VERSION"] = digest.hexdigest()[:16]
    app.wsgi_app = ProxyFix(
        app.wsgi_app,
        x_for=1,
        x_proto=1,
        x_host=1,
    )

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
    os.makedirs(app.config["DATASET_CACHE_DIR"], exist_ok=True)
    if app.config.get("PIPELINE_ENABLED", True) and str(app.config.get("PIPELINE_BACKEND", "local")) == "local":
        os.makedirs(app.config.get("LAKE_ROOT") or os.path.join("instance", "lake"), exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    configure_sessions(app)

    # Schema is owned by Alembic (`flask db upgrade`), not by create_all().
    # The convenience path stays available for local development and tests.
    if app.config.get("AUTO_CREATE_TABLES", not app.config.get("IS_PRODUCTION")):
        with app.app_context():
            from models import AgentRun, User, Project, Table  # noqa: F401

            db.create_all()

    configure_llm()
    app.after_request(apply_security_headers)
    _warn_about_insecure_settings(app)

    register_error_handlers(app)
    from services.upload_capacity import check_upload_capacity
    app.before_request(check_upload_capacity)

    @app.get("/health")
    def health():
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify({"status": "ok", "database": "connected"})
        except Exception:
            return jsonify({"status": "unhealthy", "database": "unavailable"}), 503

    @app.context_processor
    def inject_version():
        """Injects a unique version ID into all templates."""
        return dict(version_id=app.config["ASSET_VERSION"])

    app.register_blueprint(pages_bp)
    app.register_blueprint(auth_bp)
    app.register_blueprint(data_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(databases_bp)
    app.register_blueprint(tables_bp)
    app.register_blueprint(eda_bp)
    app.register_blueprint(loads_bp)
    app.register_blueprint(connectors_bp)

    configure_telemetry(app)
    return app


app = create_app()


if __name__ == '__main__':
    app.run(debug=True, port=5000)
