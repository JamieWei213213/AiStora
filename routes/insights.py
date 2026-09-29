from flask import Blueprint, jsonify, session

from config import Config
from services.insights_service import generate_insights, limits_from_config
from services.logger import get_logger
from services.rate_limit import SharedRateLimiter
from services.schema_service import active_schema
from services.state_manager import get_dataframe


insights_bp = Blueprint("insights", __name__)
logger = get_logger(__name__)
insights_rate_limiter = SharedRateLimiter("insights")


@insights_bp.route("/api/insights", methods=["POST"])
def create_insights():
    """Auto analyze: a deterministic, chart-ready first look at the database.

    Replaces the earlier behaviour where the button asked the model to pick a
    single grouped total. No model call; see services/insights_service.py.
    """
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    if not session.get("active_project_id"):
        return jsonify({"success": False, "error": "No database selected"}), 400
    schema = active_schema()
    if not schema:
        return jsonify({"success": False, "error": "Upload at least one table first"}), 400

    allowed, retry_after = insights_rate_limiter.check(
        f"insights:{session['user_id']}",
        limit=int(getattr(Config, "INSIGHTS_RATE_LIMIT_PER_MINUTE", 4)),
        window_seconds=60,
    )
    if not allowed:
        response = jsonify({
            "success": False,
            "error": "Too many analyses. Please wait a moment and try again.",
            "error_type": "rate_limit",
        })
        response.headers["Retry-After"] = str(retry_after)
        return response, 429

    try:
        report = generate_insights(
            schema,
            get_dataframe,
            relationships=session.get("db_relationships", []),
            limits=limits_from_config(Config),
        )
        logger.info(
            "Insights completed: user=%s project=%s findings=%s duration_ms=%s",
            session["user_id"], session["active_project_id"],
            len(report["findings"]), report["duration_ms"],
        )
        return jsonify({"success": True, "report": report})
    except Exception:
        logger.exception(
            "Insights failed: user=%s project=%s",
            session.get("user_id"), session.get("active_project_id"),
        )
        return jsonify({
            "success": False,
            "error": "The analysis could not be completed. Please try again.",
            "error_type": "internal_error",
        }), 500
