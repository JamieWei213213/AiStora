"""Run one connector: extract, land the file, let the pipeline take over."""

from __future__ import annotations

import logging
import os
import shutil
import time

from pipeline import events
from pipeline.connectors.base import Connector, ConnectorConfig, ConnectorError, ConnectorState
from pipeline.connectors.google_sheets import GoogleSheetsSource
from pipeline.connectors.postgres_source import PostgresSource
from pipeline.connectors.secrets import resolve_secret
from pipeline.local_runner import run_load, submit_file
from pipeline.manifest import utcnow_iso
from pipeline.stages import StageContext

logger = logging.getLogger(__name__)

IMPLEMENTATIONS: dict[str, type[Connector]] = {
    PostgresSource.type: PostgresSource,
    GoogleSheetsSource.type: GoogleSheetsSource,
}


def build_connector(config: ConnectorConfig, secret: str | None) -> Connector:
    try:
        implementation = IMPLEMENTATIONS[config.type]
    except KeyError as exc:
        raise ConnectorError(f"No implementation for connector type {config.type!r}.") from exc
    return implementation(config, secret)


def run_connector(ctx: StageContext, connector_id: str, *, run_stages: bool | None = None) -> dict:
    """Extract and land a file. Returns a small report for logs and the API.

    ``run_stages`` defaults to running the load in-process on the local
    backend (there is no event trigger) and to *not* doing so on AWS, where
    the S3 ``Object Created`` event starts the state machine.
    """
    config = ConnectorConfig.load(ctx.store, connector_id)
    state = ConnectorState.load(ctx.store, connector_id)
    if run_stages is None:
        run_stages = not ctx.settings.is_aws
    started = time.perf_counter()
    workdir = os.path.join(ctx.settings.scratch_dir, "connectors", connector_id)
    report = {"connector_id": connector_id, "type": config.type, "dataset": config.dataset}
    if not config.enabled:
        report["status"] = "disabled"
        return report
    try:
        secret = resolve_secret(ctx.settings, config.secret_ref)
        connector = build_connector(config, secret)
        result = connector.extract(state, workdir)
        state.runs += 1
        state.last_run_at = utcnow_iso()
        if result.path is None or result.rows == 0:
            state.last_status = "empty"
            state.last_error = None
            state.last_rows = 0
            report.update(status="empty", rows=0)
        else:
            manifest = submit_file(
                ctx, project_id=config.project_id, dataset=config.dataset,
                source_path=result.path, original_filename=result.filename,
                mode=config.mode, key_columns=config.key_columns,
                keep_history=config.keep_history, source=f"connector:{connector_id}",
            )
            state.cursor = result.cursor
            state.last_load_id = manifest.load_id
            state.last_rows = result.rows
            state.last_status = "succeeded"
            state.last_error = None
            report.update(status="landed", rows=result.rows, load_id=manifest.load_id, cursor=result.cursor)
            if run_stages:
                final = run_load(manifest.payload(), ctx)
                report["load_status"] = final.status
                if final.error:
                    report["load_error"] = final.error
        state.save(ctx.store, connector_id)
    except ConnectorError as exc:
        state.runs += 1
        state.last_run_at = utcnow_iso()
        state.last_status = "failed"
        state.last_error = str(exc)[:500]
        state.save(ctx.store, connector_id)
        report.update(status="failed", error=str(exc))
        logger.error("connector %s failed: %s", connector_id, exc)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    report["duration_ms"] = int((time.perf_counter() - started) * 1000)
    events.emit("pipeline.connector", connector_id=connector_id, type=config.type,
                project_id=config.project_id, dataset=config.dataset,
                status=report.get("status"), rows=report.get("rows"), duration_ms=report["duration_ms"])
    return report
