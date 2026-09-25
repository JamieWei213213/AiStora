"""The app's side of the data platform.

The pipeline writes manifests and curated Parquet to the lake and knows
nothing about the app's database. This module is the bridge:

* ``start_upload`` lands a file in ``raw/`` with a manifest, records a
  ``Load`` row, and (local backend) runs the stages on a background thread;
* ``sync_load`` copies a manifest's current state into its ``Load`` row and,
  on success, creates or updates the ``Table`` that points at the curated
  Parquet, so the agent, EDA and schema endpoints see it like any table;
* ``rollback`` restores the dataset to the snapshot before a load.

Everything is scoped by ``user_id`` through the project, as every other
lookup in the app is.
"""

from __future__ import annotations

import logging
import os
import tempfile
import time
from datetime import datetime, timezone

from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import Load, Project, Table
from pipeline.config import PipelineSettings, settings_from_env
from pipeline.contract import Contract, engine_type
from pipeline.keys import dataset_slug, validate_dataset_slug
from pipeline.lakehouse import Lakehouse, TableNotFound
from pipeline.manifest import LoadManifest, ManifestNotFound, TERMINAL_STATUSES, list_manifests
from pipeline.metrics import gold_metrics, live_pipeline_metrics
from pipeline.objectstore import ObjectStore, store_from_settings
from pipeline.stages import StageContext
from pipeline.keys import curated_key
from pipeline import local_runner

logger = logging.getLogger(__name__)


class LoadServiceError(RuntimeError):
    """Safe to show to the user."""


# --------------------------------------------------------------------------
# Context
# --------------------------------------------------------------------------

def pipeline_settings() -> PipelineSettings:
    app = current_app._get_current_object()
    cached = app.extensions.get("aistora_pipeline_settings")
    if cached is not None:
        return cached
    overrides = {
        "backend": str(app.config.get("PIPELINE_BACKEND", "local")).lower(),
        "lake_root": app.config.get("LAKE_ROOT") or os.path.join("instance", "lake"),
        "lake_bucket": app.config.get("LAKE_BUCKET"),
        "aws_region": app.config.get("AWS_REGION", "us-west-2"),
        "s3_endpoint_url": app.config.get("S3_ENDPOINT_URL"),
        "max_columns": int(app.config.get("MAX_UPLOAD_COLUMNS", 200) or 0),
        "max_column_name_chars": int(app.config.get("MAX_COLUMN_NAME_CHARS", 64) or 0),
    }
    if overrides["backend"] == "aws":
        overrides["catalog_type"] = "glue"
    settings = settings_from_env(**overrides)
    app.extensions["aistora_pipeline_settings"] = settings
    return settings


def pipeline_context() -> StageContext:
    app = current_app._get_current_object()
    ctx = app.extensions.get("aistora_pipeline_context")
    if ctx is None:
        settings = pipeline_settings()
        ctx = StageContext(settings=settings, store=store_from_settings(settings))
        app.extensions["aistora_pipeline_context"] = ctx
    return ctx


def lake_store() -> ObjectStore:
    return pipeline_context().store


# --------------------------------------------------------------------------
# Starting loads
# --------------------------------------------------------------------------

def resolve_dataset(project_id: int, filename: str, requested: str | None) -> str:
    """Dataset slug for an upload: explicit choice, else derived from the file."""
    if requested:
        return validate_dataset_slug(dataset_slug(requested))
    return dataset_slug(filename)


def start_upload(
    *,
    project: Project,
    user_id: int,
    file_storage,
    dataset: str | None,
    mode: str,
    key_columns: list[str],
    keep_history: bool,
) -> Load:
    """Land one uploaded file and return its ``Load`` row (status received)."""
    ctx = pipeline_context()
    filename = os.path.basename(file_storage.filename or "") or "upload.csv"
    slug = resolve_dataset(project.id, filename, dataset)
    existing = Table.query.filter_by(project_id=project.id, dataset=slug).first()
    if existing is None:
        max_tables = int(current_app.config.get("MAX_TABLES_PER_PROJECT", 20) or 0)
        if max_tables and Table.query.filter_by(project_id=project.id).count() >= max_tables:
            raise LoadServiceError(f"A database may hold at most {max_tables} tables.")
    if existing is not None and not existing.is_pipeline_managed:
        raise LoadServiceError(
            f"'{existing.name}' was uploaded before the pipeline existed; delete it or "
            "pick another dataset name."
        )
    if mode != "replace" and existing is None:
        raise LoadServiceError(
            f"Dataset '{slug}' does not exist yet; load it once in replace mode first."
        )
    if mode == "merge":
        if not key_columns and existing is not None and existing.key_columns:
            key_columns = list(existing.key_columns)
        if not key_columns:
            raise LoadServiceError("A merge load needs at least one key column.")

    handle = tempfile.NamedTemporaryFile(
        prefix="aistora-load-", suffix=".csv",
        dir=current_app.config.get("DATASET_CACHE_DIR") or None, delete=False,
    )
    temp_path = handle.name
    handle.close()
    try:
        file_storage.save(temp_path)
        if os.path.getsize(temp_path) == 0:
            raise LoadServiceError(f"{filename} is empty.")
        manifest = local_runner.submit_file(
            ctx, project_id=project.id, dataset=slug, source_path=temp_path,
            original_filename=filename, mode=mode, key_columns=key_columns,
            keep_history=keep_history or bool(existing and existing.keep_history), source="upload",
        )
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass

    load = Load(
        load_id=manifest.load_id, project_id=project.id, user_id=user_id,
        table_id=existing.id if existing else None, dataset=slug, mode=mode,
        source="upload", original_filename=filename, status=manifest.status,
        raw_bytes=manifest.raw_bytes, manifest=manifest.to_dict(),
    )
    db.session.add(load)
    db.session.commit()

    if not ctx.settings.is_aws:
        local_runner.run_in_background(manifest.payload(), ctx)
    return load


def wait_for_loads(loads: list[Load], timeout_seconds: float) -> None:
    """Local backend convenience: give small files a chance to finish so the
    upload response can already carry the new schema."""
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while time.monotonic() < deadline:
        pending = [load for load in loads if sync_load(load).status not in TERMINAL_STATUSES]
        if not pending:
            return
        time.sleep(0.2)


# --------------------------------------------------------------------------
# Syncing state from the lake
# --------------------------------------------------------------------------

def _parse_ts(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def sync_load(load: Load, manifest: LoadManifest | None = None) -> Load:
    """Refresh a ``Load`` row from its manifest; register the table on success."""
    if load.is_terminal and (load.status != "succeeded" or load.table_id is not None):
        return load
    if manifest is None:
        try:
            manifest = LoadManifest.load(lake_store(), _ref(load))
        except ManifestNotFound:
            return load
    changed = manifest.status != load.status or manifest.stage != load.stage
    load.status = manifest.status
    load.stage = manifest.stage
    load.raw_bytes = manifest.raw_bytes or load.raw_bytes
    load.rows_in = manifest.counts.get("rows_in")
    load.rows_out = manifest.counts.get("rows_out")
    load.rows_rejected = manifest.counts.get("rows_rejected")
    load.duration_ms = manifest.total_duration_ms()
    load.quality_status = (manifest.quality or {}).get("status")
    load.error_type = (manifest.error or {}).get("type")
    load.error_message = (manifest.error or {}).get("message")
    load.snapshot_id = manifest.snapshot_id
    load.parent_snapshot_id = manifest.parent_snapshot_id
    load.manifest = manifest.to_dict()
    load.finished_at = _parse_ts(manifest.finished_at)
    if manifest.status == "succeeded":
        table = _register_table(load, manifest)
        load.table_id = table.id
        changed = True
    if changed or db.session.is_modified(load):
        try:
            db.session.commit()
        except SQLAlchemyError:
            db.session.rollback()
            logger.exception("Could not sync load %s", load.load_id)
    return load


def _ref(load: Load):
    from pipeline.keys import LoadRef

    return LoadRef(project_id=load.project_id, dataset=load.dataset, load_id=load.load_id)


def _register_table(load: Load, manifest: LoadManifest) -> Table:
    """Create or update the app table that fronts the dataset's curated file."""
    store = lake_store()
    contract = Contract.from_dict(manifest.contract) if manifest.contract else None
    types = {column.name: engine_type(column.type) for column in contract.columns} if contract else {}
    reference = store.url(manifest.curated_key or curated_key(load.project_id, load.dataset))
    table = Table.query.filter_by(project_id=load.project_id, dataset=load.dataset).first()
    if table is None:
        name = load.dataset
        suffix = 2
        while Table.query.filter_by(project_id=load.project_id, name=name).first():
            name = f"{load.dataset}_{suffix}"
            suffix += 1
        table = Table(name=name, project_id=load.project_id, filename=load.original_filename or f"{load.dataset}.csv")
        db.session.add(table)
    table.filepath = reference
    table.filename = load.original_filename or table.filename
    table.columns_schema = types
    table.row_count = manifest.counts.get("rows_current", manifest.counts.get("rows_out"))
    table.dataset = load.dataset
    table.source_format = "parquet"
    table.load_mode = manifest.mode
    table.key_columns = list(manifest.key_columns or [])
    table.keep_history = bool(manifest.keep_history)
    table.last_load_id = manifest.load_id
    db.session.flush()
    return table


def sync_project_loads(project_id: int) -> list[Load]:
    """Pull every manifest for the project into ``Load`` rows.

    Covers loads the app did not start (connectors, files dropped straight
    into ``raw/``) so the UI shows them too.
    """
    store = lake_store()
    known = {load.load_id: load for load in Load.query.filter_by(project_id=project_id).all()}
    for manifest in list_manifests(store, project_id):
        load = known.get(manifest.load_id)
        if load is None:
            load = Load(
                load_id=manifest.load_id, project_id=project_id, dataset=manifest.dataset,
                mode=manifest.mode, source=manifest.source, original_filename=manifest.original_filename,
                status=manifest.status, raw_bytes=manifest.raw_bytes,
                created_at=_parse_ts(manifest.created_at) or datetime.now(timezone.utc),
            )
            db.session.add(load)
            db.session.flush()
            known[load.load_id] = load
        sync_load(load, manifest)
    db.session.commit()
    return sorted(known.values(), key=lambda item: item.load_id, reverse=True)


# --------------------------------------------------------------------------
# Rollback
# --------------------------------------------------------------------------

def rollback_load(load: Load) -> dict:
    """Restore the dataset to the snapshot that preceded this load."""
    if load.status != "succeeded":
        raise LoadServiceError("Only a successful load can be rolled back.")
    if load.parent_snapshot_id is None:
        raise LoadServiceError("This was the dataset's first load; delete the table instead.")
    table = Table.query.filter_by(project_id=load.project_id, dataset=load.dataset).first()
    if table is None:
        raise LoadServiceError("The dataset's table no longer exists.")
    if table.last_load_id != load.load_id:
        raise LoadServiceError(
            "Only the most recent load can be rolled back. Roll back the newer loads first."
        )
    ctx = pipeline_context()
    lakehouse = Lakehouse(ctx.settings, ctx.store)
    try:
        lakehouse.rollback_to(load.project_id, load.dataset, int(load.parent_snapshot_id))
        with tempfile.TemporaryDirectory(prefix="aistora-rollback-") as tmp:
            current = os.path.join(tmp, "current.parquet")
            rows = lakehouse.materialize_current(load.project_id, load.dataset, current)
            ctx.store.put_file(curated_key(load.project_id, load.dataset), current)
    except TableNotFound as exc:
        raise LoadServiceError("The dataset has no curated table.") from exc
    previous = (
        Load.query.filter(
            Load.project_id == load.project_id, Load.dataset == load.dataset,
            Load.status == "succeeded", Load.load_id < load.load_id,
        ).order_by(Load.load_id.desc()).first()
    )
    load.status = "rolled_back"
    load.error_type = "rolled_back"
    load.error_message = f"Rolled back to snapshot {load.parent_snapshot_id}."
    table.row_count = rows
    table.last_load_id = previous.load_id if previous else None
    if previous is not None and previous.manifest:
        contract = (previous.manifest or {}).get("contract") or {}
        columns = contract.get("columns") or []
        if columns:
            table.columns_schema = {c["name"]: engine_type(c["type"]) for c in columns}
    db.session.commit()
    from pipeline import events

    events.emit("pipeline.rollback", load_id=load.load_id, project_id=load.project_id,
                dataset=load.dataset, snapshot_id=load.parent_snapshot_id, rows=rows)
    return {"rows": rows, "snapshot_id": load.parent_snapshot_id, "load_id": load.load_id}


# --------------------------------------------------------------------------
# Read models
# --------------------------------------------------------------------------

def serialize_load(load: Load) -> dict:
    manifest = load.manifest or {}
    return {
        "id": load.id,
        "load_id": load.load_id,
        "dataset": load.dataset,
        "table_id": load.table_id,
        "mode": load.mode,
        "source": load.source,
        "original_filename": load.original_filename,
        "status": load.status,
        "stage": load.stage,
        "stages": manifest.get("stages") or {},
        "raw_bytes": load.raw_bytes,
        "rows_in": load.rows_in,
        "rows_out": load.rows_out,
        "rows_rejected": load.rows_rejected,
        "counts": manifest.get("counts") or {},
        "duration_ms": load.duration_ms,
        "quality": manifest.get("quality") or {},
        "quality_status": load.quality_status,
        "schema_changes": manifest.get("schema_changes") or {},
        "columns": manifest.get("columns") or [],
        "header_renames": manifest.get("header_renames") or {},
        "key_columns": manifest.get("key_columns") or [],
        "keep_history": bool(manifest.get("keep_history")),
        "snapshot_id": load.snapshot_id,
        "parent_snapshot_id": load.parent_snapshot_id,
        "can_rollback": load.status == "succeeded" and load.parent_snapshot_id is not None,
        "error": {"type": load.error_type, "message": load.error_message} if load.error_type else None,
        "created_at": load.created_at.isoformat() if load.created_at else None,
        "finished_at": load.finished_at.isoformat() if load.finished_at else None,
        "is_terminal": load.is_terminal or load.status == "rolled_back",
    }


def dataset_snapshots(project_id: int, dataset: str) -> list[dict]:
    ctx = pipeline_context()
    return Lakehouse(ctx.settings, ctx.store).snapshots(project_id, dataset)


def project_pipeline_metrics(project_id: int) -> dict:
    ctx = pipeline_context()
    return {
        "live": live_pipeline_metrics(ctx.store, project_id),
        "gold": gold_metrics(ctx.settings, ctx.store, project_id),
        "backend": ctx.settings.backend,
    }


def trigger_job(job: str, **payload) -> dict:
    """Run a pipeline job now: in-process locally, via Lambda on AWS."""
    ctx = pipeline_context()
    if ctx.settings.is_aws:
        function = current_app.config.get("PIPELINE_FUNCTION_NAME")
        if not function:
            raise LoadServiceError("PIPELINE_FUNCTION_NAME is not configured.")
        import json

        import boto3

        client = boto3.client("lambda", region_name=ctx.settings.aws_region)
        client.invoke(
            FunctionName=function, InvocationType="Event",
            Payload=json.dumps({"job": job, **payload}).encode("utf-8"),
        )
        return {"status": "queued", "job": job}
    if job == "telemetry":
        from pipeline.telemetry_job import run_telemetry

        return {"status": "done", "job": job, "result": run_telemetry(ctx)}
    if job == "compaction":
        from pipeline.compaction import run_compaction

        return {"status": "done", "job": job, "result": run_compaction(ctx)}
    if job == "connector":
        from pipeline.connectors.runner import run_connector

        return {"status": "done", "job": job, "result": run_connector(ctx, str(payload.get("connector_id") or ""))}
    raise LoadServiceError(f"Unknown job {job!r}.")
