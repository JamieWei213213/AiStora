"""Connector management: scheduled extracts from Postgres or Google Sheets.

Configs live in the lake (``connectors/<id>/config.json``) so the pipeline
Lambda reads them without the app's database. Secrets never pass through
here as values: ``secret_ref`` names an SSM parameter (AWS) or an
environment variable (local) that the operator sets out of band.
"""

from __future__ import annotations

import re

from flask import Blueprint, current_app, jsonify, request, session

from models import Project
from pipeline.connectors.base import CONNECTOR_TYPES, ConnectorConfig, ConnectorError, ConnectorState
from pipeline.keys import InvalidKeyError, dataset_slug
from pipeline.manifest import LOAD_MODES
from services.load_service import LoadServiceError, lake_store, trigger_job
from services.rate_limit import SharedRateLimiter

connectors_bp = Blueprint("connectors", __name__)
connector_rate_limiter = SharedRateLimiter("connectors")

_ID = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


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


def _serialize(config: ConnectorConfig, state: ConnectorState | None = None) -> dict:
    payload = config.to_dict()
    payload["secret_ref"] = bool(config.secret_ref)  # never echo the name back
    payload["state"] = state.to_dict() if state else None
    return payload


def _owned(connector_id: str, project: Project) -> ConnectorConfig | None:
    if not _ID.match(str(connector_id or "")):
        return None
    try:
        config = ConnectorConfig.load(lake_store(), connector_id)
    except ConnectorError:
        return None
    return config if int(config.project_id) == int(project.id) else None


@connectors_bp.route("/api/connectors", methods=["GET"])
def list_connectors():
    if "user_id" not in session:
        return _unauthorized()
    project, error = _active_project()
    if error:
        return error
    store = lake_store()
    configs = ConnectorConfig.list_all(store, project.id)
    return jsonify({
        "success": True,
        "types": list(CONNECTOR_TYPES),
        "connectors": [_serialize(c, ConnectorState.load(store, c.id)) for c in configs],
    })


@connectors_bp.route("/api/connectors", methods=["POST"])
def create_connector():
    if "user_id" not in session:
        return _unauthorized()
    project, error = _active_project()
    if error:
        return error
    data = request.get_json(silent=True) or {}
    connector_id = str(data.get("id") or "").strip().lower()
    if not _ID.match(connector_id):
        return jsonify({"success": False, "error": "id must be 1-63 lowercase letters, digits, - or _."}), 400
    store = lake_store()
    try:
        ConnectorConfig.load(store, connector_id)
        return jsonify({"success": False, "error": "A connector with this id already exists."}), 409
    except ConnectorError:
        pass
    existing = ConnectorConfig.list_all(store, project.id)
    if len(existing) >= 10:
        return jsonify({"success": False, "error": "A database may have at most 10 connectors."}), 400
    try:
        config = ConnectorConfig(
            id=connector_id,
            type=str(data.get("type") or ""),
            project_id=int(project.id),
            dataset=dataset_slug(str(data.get("dataset") or "")),
            mode=str(data.get("mode") or "replace"),
            key_columns=[str(k).strip() for k in (data.get("key_columns") or []) if str(k).strip()][:8],
            keep_history=bool(data.get("keep_history")),
            schedule=str(data.get("schedule") or "rate(1 day)")[:80],
            options={str(k)[:64]: v for k, v in (data.get("options") or {}).items()} if isinstance(data.get("options"), dict) else {},
            secret_ref=(str(data.get("secret_ref")).strip()[:200] or None) if data.get("secret_ref") else None,
            enabled=bool(data.get("enabled", True)),
        ).validate()
    except (ConnectorError, InvalidKeyError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    if config.mode not in LOAD_MODES:
        return jsonify({"success": False, "error": "Invalid mode."}), 400
    config.save(store)
    return jsonify({"success": True, "connector": _serialize(config)}), 201


@connectors_bp.route("/api/connectors/<connector_id>", methods=["PUT"])
def update_connector(connector_id):
    if "user_id" not in session:
        return _unauthorized()
    project, error = _active_project()
    if error:
        return error
    config = _owned(connector_id, project)
    if config is None:
        return jsonify({"success": False, "error": "Connector not found"}), 404
    data = request.get_json(silent=True) or {}
    try:
        for field in ("mode", "schedule", "secret_ref", "type"):
            if field in data and data[field] is not None:
                setattr(config, field, str(data[field])[:200])
        if "dataset" in data:
            config.dataset = dataset_slug(str(data["dataset"]))
        if "key_columns" in data:
            config.key_columns = [str(k).strip() for k in (data["key_columns"] or []) if str(k).strip()][:8]
        if "keep_history" in data:
            config.keep_history = bool(data["keep_history"])
        if "enabled" in data:
            config.enabled = bool(data["enabled"])
        if isinstance(data.get("options"), dict):
            config.options = {str(k)[:64]: v for k, v in data["options"].items()}
        config.validate()
    except (ConnectorError, InvalidKeyError, ValueError) as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    config.save(lake_store())
    return jsonify({"success": True, "connector": _serialize(config, ConnectorState.load(lake_store(), config.id))})


@connectors_bp.route("/api/connectors/<connector_id>", methods=["DELETE"])
def delete_connector(connector_id):
    if "user_id" not in session:
        return _unauthorized()
    project, error = _active_project()
    if error:
        return error
    config = _owned(connector_id, project)
    if config is None:
        return jsonify({"success": False, "error": "Connector not found"}), 404
    config.delete(lake_store())
    return jsonify({"success": True})


@connectors_bp.route("/api/connectors/<connector_id>/run", methods=["POST"])
def run_connector_now(connector_id):
    if "user_id" not in session:
        return _unauthorized()
    project, error = _active_project()
    if error:
        return error
    config = _owned(connector_id, project)
    if config is None:
        return jsonify({"success": False, "error": "Connector not found"}), 404
    allowed, retry_after = connector_rate_limiter.check(
        f"connector-run:{session['user_id']}", limit=5, window_seconds=60,
    )
    if not allowed:
        response = jsonify({"success": False, "error": "Try again shortly.", "error_type": "rate_limit"})
        response.headers["Retry-After"] = str(int(retry_after or 60))
        return response, 429
    try:
        result = trigger_job("connector", connector_id=config.id)
    except LoadServiceError as exc:
        return jsonify({"success": False, "error": str(exc)}), 400
    except Exception as exc:  # noqa: BLE001
        current_app.logger.exception("connector %s failed", connector_id)
        return jsonify({"success": False, "error": f"Connector run failed: {str(exc)[:200]}"}), 500
    return jsonify({"success": True, **result})


@connectors_bp.before_request
def require_pipeline_enabled():
    if not current_app.config.get("PIPELINE_ENABLED", True):
        return jsonify(success=False, error="Advanced data pipelines are not enabled on this installation."), 503
