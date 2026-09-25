"""Loads API: start, watch, list, roll back, and the pipeline dashboard.

Every endpoint resolves the project through ``session['user_id']`` so a
user can only see or act on loads in their own databases.
"""

from __future__ import annotations

import json

from flask import Blueprint, current_app, jsonify, request, session

from models import Load, Project, Table
from pipeline.keys import InvalidKeyError
from pipeline.manifest import LOAD_MODES
from services.load_service import (
    LoadServiceError,
    dataset_snapshots,
    project_pipeline_metrics,
    rollback_load,
    serialize_load,
    start_upload,
    sync_load,
    sync_project_loads,
    trigger_job,
    wait_for_loads,
)
from services.rate_limit import SharedRateLimiter
from services.schema_service import build_project_schema

loads_bp = Blueprint("loads", __name__)
loads_rate_limiter = SharedRateLimiter("loads")


def _unauthorized():
    return jsonify({"success": False, "error": "Unauthorized. Please log in."}), 401


def _active_project():
    project_id = session.get("active_project_id")
    if not project_id:
        return None, (jsonify({"success": False, "error": "No database selected"}), 400)
    project = Project.query.filter_by(id=project_id, user_id=session["user_id"]).first()
    if not project:
        return None, (jsonify({"success": False, "error": "Database not found"}), 404)
    return project, None


def _pipeline_enabled():
    return bool(current_app.config.get("PIPELINE_ENABLED", True))


def _parse_key_columns(raw) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, list):
        values = raw
    else:
        text = str(raw).strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
            values = parsed if isinstance(parsed, list) else [parsed]
        except json.JSONDecodeError:
            values = text.split(",")
    return [str(v).strip() for v in values if str(v).strip()][:8]


@loads_bp.route("/api/loads", methods=["POST"])
def create_loads():
    """Start one load per uploaded file.

    Form fields: ``files`` (one or more CSVs), optional ``dataset`` (only
    meaningful with a single file), ``mode`` (replace|append|merge),
    ``key_columns`` (comma-separated or JSON list) and ``keep_history``.
    Answers 202 with the loads; the client polls ``GET /api/loads/<id>``.
    On the local backend small files usually finish before the response.
    """
    if "user_id" not in session:
        return _unauthorized()
    if not _pipeline_enabled():
        return jsonify({"success": False, "error": "The data pipeline is disabled."}), 503
    project, error = _active_project()
    if error:
        return error
    allowed, retry_after = loads_rate_limiter.check(
        f"loads:{session['user_id']}",
        limit=int(current_app.config.get("LOADS_RATE_LIMIT_PER_MINUTE", 10) or 10),
        window_seconds=60,
    )
    if not allowed:
        response = jsonify({"success": False, "error": "Too many loads. Try again shortly.", "error_type": "rate_limit"})
        response.headers["Retry-After"] = str(int(retry_after or 60))
        return response, 429

    files = [f for f in request.files.getlist("files") if f and f.filename]
    if not files:
        return jsonify({"success": False, "error": "No files were uploaded."}), 400
    mode = (request.form.get("mode") or "replace").strip().lower()
    if mode not in LOAD_MODES:
        return jsonify({"success": False, "error": f"mode must be one of {', '.join(LOAD_MODES)}."}), 400
    dataset = (request.form.get("dataset") or "").strip() or None
    if dataset and len(files) > 1:
        return jsonify({"success": False, "error": "A dataset name applies to a single file."}), 400
    key_columns = _parse_key_columns(request.form.get("key_columns"))
    keep_history = str(request.form.get("keep_history") or "").lower() in {"1", "true", "yes", "on"}

    started = []
    try:
        for file_storage in files:
            started.append(start_upload(
                project=project, user_id=session["user_id"], file_storage=file_storage,
                dataset=dataset, mode=mode, key_columns=key_columns, keep_history=keep_history,
            ))
    except (LoadServiceError, InvalidKeyError) as exc:
        return jsonify({
            "success": False, "error": str(exc),
            "loads": [serialize_load(load) for load in started],
        }), 400

    wait_for_loads(started, float(current_app.config.get("PIPELINE_SYNC_WAIT_SECONDS", 20) or 0))
    loads = [serialize_load(load) for load in started]
    pending = any(not item["is_terminal"] for item in loads)
    rejected = [item for item in loads if item["status"] in {"quarantined", "failed"}]
    if not pending and rejected and len(rejected) == len(loads):
        # Every file was refused and we know why: answer like a validation
        # error so the existing upload UI shows the reason directly.
        first = rejected[0]["error"] or {}
        return jsonify({
            "success": False,
            "error": first.get("message") or "The upload was rejected by the pipeline.",
            "error_type": first.get("type") or "quality",
            "loads": loads,
            "schema": build_project_schema(project.id),
        }), 400
    return jsonify({
        "success": True,
        "loads": loads,
        "pending": pending,
        "rejected": len(rejected),
        "schema": build_project_schema(project.id),
    }), 202 if pending else 200


@loads_bp.route("/api/loads", methods=["GET"])
def list_loads():
    if "user_id" not in session:
        return _unauthorized()
    project, error = _active_project()
    if error:
        return error
    dataset = (request.args.get("dataset") or "").strip() or None
    limit = max(1, min(int(request.args.get("limit", 50) or 50), 200))
    loads = sync_project_loads(project.id)
    if dataset:
        loads = [load for load in loads if load.dataset == dataset]
    return jsonify({
        "success": True,
        "loads": [serialize_load(load) for load in loads[:limit]],
        "schema": build_project_schema(project.id),
    })


@loads_bp.route("/api/loads/<load_id>", methods=["GET"])
def get_load(load_id):
    if "user_id" not in session:
        return _unauthorized()
    load = Load.query.join(Project).filter(
        Load.load_id == load_id, Project.user_id == session["user_id"]
    ).first()
    if load is None:
        return jsonify({"success": False, "error": "Load not found"}), 404
    sync_load(load)
    payload = {"success": True, "load": serialize_load(load)}
    if load.status == "succeeded":
        payload["schema"] = build_project_schema(load.project_id)
    return jsonify(payload)


@loads_bp.route("/api/loads/<load_id>/rollback", methods=["POST"])
def rollback(load_id):
    if "user_id" not in session:
        return _unauthorized()
    load = Load.query.join(Project).filter(
        Load.load_id == load_id, Project.user_id == session["user_id"]
    ).first()
    if load is None:
        return jsonify({"success": False, "error": "Load not found"}), 404
    sync_load(load)
    try:
        result = rollback_load(load)
    except LoadServiceError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    return jsonify({
        "success": True, "result": result, "load": serialize_load(load),
        "schema": build_project_schema(load.project_id),
    })


@loads_bp.route("/api/datasets/<dataset>/snapshots", methods=["GET"])
def snapshots(dataset):
    if "user_id" not in session:
        return _unauthorized()
    project, error = _active_project()
    if error:
        return error
    table = Table.query.filter_by(project_id=project.id, dataset=dataset).first()
    if table is None:
        return jsonify({"success": False, "error": "Dataset not found"}), 404
    return jsonify({"success": True, "dataset": dataset, "snapshots": dataset_snapshots(project.id, dataset)})


@loads_bp.route("/api/pipeline/metrics", methods=["GET"])
def pipeline_metrics():
    if "user_id" not in session:
        return _unauthorized()
    project, error = _active_project()
    if error:
        return error
    return jsonify({"success": True, "metrics": project_pipeline_metrics(project.id)})


@loads_bp.route("/api/pipeline/jobs/<job>", methods=["POST"])
def run_job(job):
    """Kick a nightly job now (telemetry or compaction). Cheap enough to be
    a button; still rate limited like uploads."""
    if "user_id" not in session:
        return _unauthorized()
    if job not in {"telemetry", "compaction"}:
        return jsonify({"success": False, "error": "Unknown job"}), 404
    allowed, retry_after = loads_rate_limiter.check(
        f"jobs:{session['user_id']}", limit=3, window_seconds=60,
    )
    if not allowed:
        response = jsonify({"success": False, "error": "Try again shortly.", "error_type": "rate_limit"})
        response.headers["Retry-After"] = str(int(retry_after or 60))
        return response, 429
    try:
        result = trigger_job(job)
    except LoadServiceError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001 - job errors are reported, not leaked
        current_app.logger.exception("pipeline job %s failed", job)
        return jsonify({"success": False, "error": f"{job} failed: {str(exc)[:200]}"}), 500
    return jsonify({"success": True, **result})


@loads_bp.before_request
def require_pipeline_enabled():
    if not current_app.config.get("PIPELINE_ENABLED", True):
        return jsonify(success=False, error="Advanced data pipelines are not enabled on this installation."), 503
