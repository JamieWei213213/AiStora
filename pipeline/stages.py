"""The six stages of a load, as pure functions of the manifest.

    validate -> profile -> gate -> transform -> curate -> register

Each stage:

1. loads the manifest for ``(project_id, dataset, load_id)``;
2. does its work with whatever is in the lake (raw file, working copy,
   silver Parquet) -- never with in-process state from a previous stage,
   because on AWS each stage is a separate Lambda invocation;
3. writes the manifest back and returns the small payload the orchestrator
   passes on. ``payload["status"]`` tells the state machine whether to
   continue (``running``) or stop (``quarantined`` / ``failed``).

A stage is idempotent: re-running it after a crash overwrites the same
keys and produces the same manifest.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
import warnings
from dataclasses import dataclass

from pipeline import events
from pipeline.config import PipelineSettings, settings_from_env
from pipeline.contract import Contract, diff_contract, evolve_contract, infer_contract
from pipeline.duck import connect
from pipeline.keys import LoadRef, contract_key, curated_key, parse_raw_key, dataset_slug
from pipeline.lakehouse import Lakehouse, read_parquet_as
from pipeline.manifest import (
    LOAD_MODES,
    STATUS_RECEIVED,
    LoadManifest,
    ManifestNotFound,
    utcnow_iso,
)
from pipeline.objectstore import ObjectNotFound, ObjectStore, store_from_settings
from pipeline.quality import run_quality_gate
from pipeline.transform import (
    RAW_VIEW,
    TYPED_VIEW,
    create_typed_view,
    register_raw_csv,
    write_parquet,
)
from pipeline.validate import ValidationFailure, validate_raw_file

logger = logging.getLogger(__name__)

STAGE_ORDER = ("validate", "profile", "gate", "transform", "curate", "register")

LOCK_WAIT_SECONDS = 90
LOCK_STALE_SECONDS = 15 * 60


class StageError(RuntimeError):
    """An unexpected failure; the orchestrator retries then calls on_failure."""


class RetryableStageError(StageError):
    """A transient condition (another load holds the dataset lock)."""


@dataclass
class StageContext:
    settings: PipelineSettings
    store: ObjectStore
    _lakehouse: Lakehouse | None = None

    @property
    def lakehouse(self) -> Lakehouse:
        if self._lakehouse is None:
            self._lakehouse = Lakehouse(self.settings, self.store)
        return self._lakehouse

    def scratch(self, ref: LoadRef) -> str:
        path = os.path.join(self.settings.scratch_dir, "loads", ref.load_id)
        os.makedirs(path, exist_ok=True)
        return path

    def cleanup(self, ref: LoadRef) -> None:
        shutil.rmtree(os.path.join(self.settings.scratch_dir, "loads", ref.load_id), ignore_errors=True)


def build_context(settings: PipelineSettings | None = None, store: ObjectStore | None = None) -> StageContext:
    settings = settings or settings_from_env()
    store = store or store_from_settings(settings)
    return StageContext(settings=settings, store=store)


def _ref_from_payload(payload: dict) -> LoadRef:
    try:
        return LoadRef(
            project_id=int(payload["project_id"]),
            dataset=str(payload["dataset"]),
            load_id=str(payload["load_id"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise StageError(f"Malformed stage payload: {payload!r}") from exc


def _fetch(ctx: StageContext, key: str, destination: str) -> str:
    """Download a lake object into scratch unless an identical copy is there."""
    if os.path.isfile(destination):
        try:
            if os.path.getsize(destination) == ctx.store.size(key):
                return destination
        except ObjectNotFound:
            pass
    ctx.store.get_file(key, destination)
    return destination


def _quarantine(ctx: StageContext, manifest: LoadManifest, reason: str, error_type: str, report: dict) -> None:
    ref = manifest.ref
    filename = os.path.basename(manifest.raw_key) or "file.csv"
    try:
        ctx.store.copy(manifest.raw_key, ref.quarantine_key(filename))
        manifest.quarantine_key = ref.quarantine_key(filename)
    except ObjectNotFound:
        manifest.quarantine_key = None
    ctx.store.put_json(ref.quarantine_key("report.json"), {
        "load_id": manifest.load_id,
        "dataset": manifest.dataset,
        "project_id": manifest.project_id,
        "reason": reason,
        "error_type": error_type,
        "quarantined_at": utcnow_iso(),
        **report,
    })
    manifest.mark_quarantined(reason, error_type)


def _finish(ctx: StageContext, manifest: LoadManifest, stage: str, status: str = "succeeded") -> dict:
    manifest.finish_stage(stage, status)
    manifest.save(ctx.store)
    entry = manifest.stages.get(stage, {})
    events.emit(
        "pipeline.stage",
        load_id=manifest.load_id, project_id=manifest.project_id, dataset=manifest.dataset,
        stage=stage, status=status, duration_ms=entry.get("duration_ms"), mode=manifest.mode,
    )
    if manifest.is_terminal:
        for name in ("working.csv", "header.json"):
            try:
                ctx.store.delete(manifest.ref.work_key(name))
            except Exception:  # pragma: no cover
                logger.debug("could not delete work file", exc_info=True)
        events.emit(
            "pipeline.load",
            load_id=manifest.load_id, project_id=manifest.project_id, dataset=manifest.dataset,
            status=manifest.status, mode=manifest.mode, source=manifest.source,
            rows_in=manifest.counts.get("rows_in"), rows_out=manifest.counts.get("rows_out"),
            rows_rejected=manifest.counts.get("rows_rejected"), raw_bytes=manifest.raw_bytes,
            duration_ms=manifest.total_duration_ms(), error_type=(manifest.error or {}).get("type"),
            quality_status=manifest.quality.get("status"),
        )
        events.flush()
        ctx.cleanup(manifest.ref)
    return manifest.payload()


# --------------------------------------------------------------------------
# Manifest bootstrap (S3 drop without an app-written manifest)
# --------------------------------------------------------------------------

def ensure_manifest_for_key(ctx: StageContext, raw_key: str, *, source: str = "drop") -> LoadManifest:
    ref, filename = parse_raw_key(raw_key)
    try:
        manifest = LoadManifest.load(ctx.store, ref)
        if not manifest.raw_key:
            manifest.raw_key = raw_key
        return manifest
    except ManifestNotFound:
        pass
    manifest = LoadManifest(
        load_id=ref.load_id,
        project_id=ref.project_id,
        dataset=ref.dataset,
        mode="replace",
        source=source,
        original_filename=filename,
        raw_key=raw_key,
    )
    try:
        manifest.raw_bytes = ctx.store.size(raw_key)
    except ObjectNotFound:
        pass
    manifest.save(ctx.store)
    return manifest


def parse_event(ctx: StageContext, event: dict) -> dict:
    """Turn an EventBridge ``Object Created`` event into a stage payload."""
    detail = event.get("detail") or event
    key = (detail.get("object") or {}).get("key") or detail.get("key")
    if not key:
        raise StageError("The event carries no object key.")
    manifest = ensure_manifest_for_key(ctx, key)
    payload = manifest.payload()
    payload["raw_key"] = key
    return payload


# --------------------------------------------------------------------------
# Stages
# --------------------------------------------------------------------------

def stage_validate(ctx: StageContext, payload: dict) -> dict:
    ref = _ref_from_payload(payload)
    manifest = LoadManifest.load(ctx.store, ref)
    manifest.start_stage("validate")
    if manifest.mode not in LOAD_MODES:
        _quarantine(ctx, manifest, f"Unknown load mode {manifest.mode!r}.", "validation", {})
        return _finish(ctx, manifest, "validate", "quarantined")
    scratch = ctx.scratch(ref)
    raw_path = os.path.join(scratch, "raw" + (os.path.splitext(manifest.original_filename)[1].lower() or ".csv"))
    try:
        _fetch(ctx, manifest.raw_key, raw_path)
    except ObjectNotFound:
        manifest.mark_failed("The raw object is missing from the lake.", "storage")
        return _finish(ctx, manifest, "validate", "failed")
    try:
        result = validate_raw_file(raw_path, manifest.original_filename, ctx.settings, scratch)
    except ValidationFailure as exc:
        _quarantine(ctx, manifest, str(exc), exc.error_type, {})
        return _finish(ctx, manifest, "validate", "quarantined")

    manifest.encoding = result.encoding
    manifest.delimiter = result.delimiter
    manifest.header_renames = result.renames
    manifest.raw_bytes = result.raw_bytes
    manifest.counts["rows_rejected"] = result.rows_rejected
    if result.rejected_samples:
        manifest.counts["rejected_samples"] = result.rejected_samples
    manifest.counts["columns"] = len(result.header)
    ctx.store.put_file(ref.work_key("working.csv"), result.working_path)
    ctx.store.put_json(ref.work_key("header.json"), result.header)
    return _finish(ctx, manifest, "validate")


def _open_working(ctx: StageContext, manifest: LoadManifest):
    ref = manifest.ref
    scratch = ctx.scratch(ref)
    working = _fetch(ctx, ref.work_key("working.csv"), os.path.join(scratch, "working.csv"))
    header = ctx.store.get_json(ref.work_key("header.json"))
    con = connect(ctx.settings)
    register_raw_csv(con, working, manifest.delimiter or ",", header)
    return con, header


def stage_profile(ctx: StageContext, payload: dict) -> dict:
    ref = _ref_from_payload(payload)
    manifest = LoadManifest.load(ctx.store, ref)
    manifest.start_stage("profile")
    con, header = _open_working(ctx, manifest)
    try:
        incoming = infer_contract(
            con, RAW_VIEW, header,
            dataset=manifest.dataset, project_id=manifest.project_id, load_id=manifest.load_id,
            key_columns=manifest.key_columns, tolerance=ctx.settings.cast_failure_threshold,
        )
    finally:
        con.close()

    missing_keys = [key for key in manifest.key_columns if key not in header]
    if missing_keys:
        _quarantine(
            ctx, manifest,
            f"Merge key column(s) not found in the file: {', '.join(missing_keys)}.",
            "schema", {"header": header},
        )
        return _finish(ctx, manifest, "profile", "quarantined")
    if manifest.mode == "merge" and not manifest.key_columns:
        _quarantine(ctx, manifest, "A merge load needs at least one key column.", "schema", {})
        return _finish(ctx, manifest, "profile", "quarantined")

    stored: Contract | None = None
    try:
        stored = Contract.from_dict(ctx.store.get_json(contract_key(ref.project_id, ref.dataset)))
    except ObjectNotFound:
        stored = None

    if stored is None:
        contract = incoming
        manifest.schema_changes = {"initial": True}
    else:
        if manifest.key_columns:
            stored.key_columns = list(manifest.key_columns)
        changes = diff_contract(stored, incoming)
        if changes.is_blocked and manifest.mode == "replace":
            # The documented escape hatch: a replace load resets the schema.
            contract = incoming
            contract.version = stored.version + 1
            manifest.reset_schema = True
            manifest.schema_changes = {**changes.to_dict(), "reset": True}
        elif changes.is_blocked:
            manifest.schema_changes = changes.to_dict()
            _quarantine(ctx, manifest, changes.reason() or "schema conflict", "schema", {
                "schema_changes": changes.to_dict(),
            })
            return _finish(ctx, manifest, "profile", "quarantined")
        else:
            contract = evolve_contract(stored, incoming, changes, manifest.load_id)
            manifest.schema_changes = changes.to_dict()
    if manifest.key_columns:
        contract.key_columns = list(manifest.key_columns)
    manifest.contract = contract.to_dict()
    manifest.columns = [column.to_dict() for column in contract.columns]
    manifest.counts["rows_in"] = contract.row_count
    return _finish(ctx, manifest, "profile")


def stage_gate(ctx: StageContext, payload: dict) -> dict:
    ref = _ref_from_payload(payload)
    manifest = LoadManifest.load(ctx.store, ref)
    manifest.start_stage("gate")
    contract = Contract.from_dict(manifest.contract)
    con, _ = _open_working(ctx, manifest)
    try:
        create_typed_view(con, contract, manifest.load_id)
        report = run_quality_gate(
            con, RAW_VIEW, TYPED_VIEW, contract, ctx.settings,
            rows_rejected=int(manifest.counts.get("rows_rejected", 0) or 0),
            key_columns=manifest.key_columns if manifest.mode == "merge" else None,
        )
    finally:
        con.close()
    manifest.quality = report.to_dict()
    if report.failed:
        _quarantine(ctx, manifest, report.failure_reason(), "quality", {"quality": report.to_dict()})
        return _finish(ctx, manifest, "gate", "quarantined")
    return _finish(ctx, manifest, "gate")


def stage_transform(ctx: StageContext, payload: dict) -> dict:
    ref = _ref_from_payload(payload)
    manifest = LoadManifest.load(ctx.store, ref)
    manifest.start_stage("transform")
    contract = Contract.from_dict(manifest.contract)
    con, _ = _open_working(ctx, manifest)
    try:
        create_typed_view(con, contract, manifest.load_id)
        destination = os.path.join(ctx.scratch(ref), "silver.parquet")
        rows = write_parquet(con, destination)
    finally:
        con.close()
    manifest.silver_key = ref.silver_key()
    ctx.store.put_file(manifest.silver_key, destination)
    manifest.counts["rows_out"] = rows
    manifest.counts["silver_bytes"] = os.path.getsize(destination)
    return _finish(ctx, manifest, "transform")


class _DatasetLock:
    """One writer per dataset. PyIceberg has no commit retry, so the state
    machine's Retry on ``RetryableStageError`` is the queue."""

    def __init__(self, ctx: StageContext, ref: LoadRef):
        self.ctx, self.ref, self.key = ctx, ref, ref.lock_key()

    def __enter__(self):
        deadline = time.monotonic() + LOCK_WAIT_SECONDS
        body = f"{self.ref.load_id} {utcnow_iso()}".encode("utf-8")
        while True:
            if self.ctx.store.put_bytes_if_absent(self.key, body):
                return self
            try:
                owner, stamp = self.ctx.store.get_bytes(self.key).decode("utf-8").split(" ", 1)
            except (ObjectNotFound, ValueError):
                owner, stamp = "", ""
            if owner == self.ref.load_id:
                return self  # our own lock from a retried invocation
            if stamp and _age_seconds(stamp) > LOCK_STALE_SECONDS:
                logger.warning("Breaking stale lock %s held by %s", self.key, owner)
                self.ctx.store.delete(self.key)
                continue
            if time.monotonic() > deadline:
                raise RetryableStageError(
                    f"Dataset {self.ref.dataset} is being written by load {owner}; retry later."
                )
            time.sleep(2)

    def __exit__(self, *exc):
        try:
            self.ctx.store.delete(self.key)
        except Exception:  # pragma: no cover
            logger.exception("Could not release %s", self.key)


def _age_seconds(iso: str) -> float:
    from datetime import datetime, timezone

    try:
        then = datetime.fromisoformat(iso)
    except ValueError:
        return 0.0
    if then.tzinfo is None:
        then = then.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - then).total_seconds()


def stage_curate(ctx: StageContext, payload: dict) -> dict:
    ref = _ref_from_payload(payload)
    manifest = LoadManifest.load(ctx.store, ref)
    manifest.start_stage("curate")
    contract = Contract.from_dict(manifest.contract)
    silver = _fetch(ctx, manifest.silver_key, os.path.join(ctx.scratch(ref), "silver.parquet"))
    arrow = read_parquet_as(silver, contract)
    with _DatasetLock(ctx, ref), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        result = ctx.lakehouse.load(
            ref.project_id, ref.dataset, contract, arrow,
            mode=manifest.mode, load_id=manifest.load_id,
            key_columns=manifest.key_columns or None,
            keep_history=manifest.keep_history,
            reset_schema=manifest.reset_schema,
        )
        current = os.path.join(ctx.scratch(ref), "current.parquet")
        total = ctx.lakehouse.materialize_current(ref.project_id, ref.dataset, current)
        manifest.curated_key = curated_key(ref.project_id, ref.dataset)
        ctx.store.put_file(manifest.curated_key, current)
    manifest.counts.update(result.counts())
    manifest.counts["rows_current"] = total
    manifest.snapshot_id = result.snapshot_id
    manifest.parent_snapshot_id = result.parent_snapshot_id
    return _finish(ctx, manifest, "curate")


def stage_register(ctx: StageContext, payload: dict) -> dict:
    ref = _ref_from_payload(payload)
    manifest = LoadManifest.load(ctx.store, ref)
    manifest.start_stage("register")
    ctx.store.put_json(contract_key(ref.project_id, ref.dataset), manifest.contract)
    manifest.mark_succeeded()
    return _finish(ctx, manifest, "register")


def stage_on_failure(ctx: StageContext, payload: dict, error: str | None = None, error_type: str = "internal") -> dict:
    ref = _ref_from_payload(payload)
    try:
        manifest = LoadManifest.load(ctx.store, ref)
    except ManifestNotFound:
        return {**payload, "status": "failed"}
    stage = manifest.stage or "unknown"
    manifest.mark_failed(error or "The pipeline failed unexpectedly.", error_type)
    if stage in manifest.stages and manifest.stages[stage].get("status") == "running":
        manifest.finish_stage(stage, "failed")
    manifest.save(ctx.store)
    for name in ("working.csv", "header.json"):
        try:
            ctx.store.delete(ref.work_key(name))
        except Exception:  # pragma: no cover
            logger.debug("could not delete work file", exc_info=True)
    events.emit(
        "pipeline.load", load_id=manifest.load_id, project_id=manifest.project_id,
        dataset=manifest.dataset, status="failed", stage=stage, error_type=error_type,
        mode=manifest.mode, source=manifest.source,
    )
    events.flush()
    ctx.cleanup(ref)
    return manifest.payload()


def _error_message(error) -> str | None:
    """Flatten a Step Functions error object.

    A Lambda exception arrives as ``{"Error": "<class>", "Cause": "<json>"}``
    where Cause is the serialized ``{"errorMessage": ..., "errorType": ...}``;
    a timeout arrives as ``{"Error": "States.Timeout", "Cause": "..."}``.
    """
    import json

    if not error:
        return None
    if not isinstance(error, dict):
        return str(error)[:1000]
    cause = error.get("Cause") or error.get("message")
    if isinstance(cause, str) and cause.startswith("{"):
        try:
            parsed = json.loads(cause)
            detail = parsed.get("errorMessage") or parsed.get("message")
            if detail:
                return f"{parsed.get('errorType', error.get('Error', 'Error'))}: {detail}"[:1000]
        except (ValueError, AttributeError):
            pass
    if cause:
        return f"{error.get('Error')}: {cause}"[:1000] if error.get("Error") and error.get("Error") not in str(cause) else str(cause)[:1000]
    return str(error.get("Error") or error)[:1000]


STAGES = {
    "validate": stage_validate,
    "profile": stage_profile,
    "gate": stage_gate,
    "transform": stage_transform,
    "curate": stage_curate,
    "register": stage_register,
}


def run_stage(name: str, payload: dict, ctx: StageContext | None = None) -> dict:
    ctx = ctx or build_context()
    if name == "parse_event":
        return parse_event(ctx, payload)
    if name == "on_failure":
        return stage_on_failure(ctx, payload, error=_error_message(payload.get("error")))
    try:
        stage = STAGES[name]
    except KeyError:
        raise StageError(f"Unknown stage {name!r}")
    return stage(ctx, payload)


def new_manifest(
    *, project_id: int, dataset: str, load_id: str, mode: str, original_filename: str,
    raw_key: str, raw_bytes: int = 0, key_columns=None, keep_history: bool = False, source: str = "upload",
) -> LoadManifest:
    if mode not in LOAD_MODES:
        raise ValueError(f"mode must be one of {LOAD_MODES}")
    return LoadManifest(
        load_id=load_id,
        project_id=int(project_id),
        dataset=dataset_slug(dataset),
        mode=mode,
        key_columns=[str(k) for k in (key_columns or [])],
        keep_history=bool(keep_history),
        source=source,
        original_filename=original_filename,
        raw_key=raw_key,
        raw_bytes=int(raw_bytes or 0),
        status=STATUS_RECEIVED,
    )
