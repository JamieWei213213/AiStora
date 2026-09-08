from flask import Blueprint, jsonify, session

from config import Config
from services.eda_service import generate_eda_report, limits_from_config
from services.logger import get_logger
from services.rate_limit import SharedRateLimiter
from services.schema_service import active_schema
from services.state_manager import get_dataframe


eda_bp = Blueprint("eda", __name__)
logger = get_logger(__name__)
eda_rate_limiter = SharedRateLimiter('eda')


@eda_bp.route("/api/eda-report", methods=["POST"])
def create_eda_report():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    if not session.get("active_project_id"):
        return jsonify({"success": False, "error": "No database selected"}), 400
    schema = active_schema()
    if not schema:
        return jsonify({"success": False, "error": "Upload at least one table first"}), 400

    allowed, retry_after = eda_rate_limiter.check(
        f"eda:{session['user_id']}",
        limit=int(getattr(Config, "EDA_RATE_LIMIT_PER_MINUTE", 3)),
        window_seconds=60,
    )
    if not allowed:
        response = jsonify({
            "success": False,
            "error": "Too many EDA reports. Please wait and try again.",
            "error_type": "rate_limit",
        })
        response.headers["Retry-After"] = str(retry_after)
        return response, 429

    try:
        report = generate_eda_report(
            schema,
            get_dataframe,
            relationships=session.get("db_relationships", []),
            limits=limits_from_config(Config),
        )
        logger.info(
            "EDA report completed: user=%s project=%s status=%s rows=%s duration_ms=%s",
            session["user_id"],
            session["active_project_id"],
            report["status"],
            report["overview"]["scanned_rows"],
            report["duration_ms"],
        )
        return jsonify({"success": True, "report": report})
    except Exception:
        logger.exception(
            "EDA report failed: user=%s project=%s",
            session.get("user_id"),
            session.get("active_project_id"),
        )
        return jsonify({
            "success": False,
            "error": "The EDA report could not be completed safely. Please try again.",
            "error_type": "internal_error",
        }), 500
