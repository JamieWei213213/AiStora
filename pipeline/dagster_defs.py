"""Optional Dagster definitions over the same stage functions.

On AWS the orchestrator is Step Functions (no server to run, free at this
volume). For local development, or for anyone who prefers a Dagster UI, this
module exposes the pipeline as Dagster assets and jobs. It is optional:
``pip install dagster dagster-webserver`` then ``dagster dev -m
pipeline.dagster_defs``. Nothing else in the package imports it.

The assets call exactly the functions the Lambda calls; Dagster adds the
graph, the run log and a schedule, not logic.
"""

from __future__ import annotations

import os

try:  # pragma: no cover - exercised only when dagster is installed
    from dagster import (
        AssetExecutionContext,
        Config,
        Definitions,
        ScheduleDefinition,
        asset,
        define_asset_job,
    )
except ImportError as exc:  # pragma: no cover
    raise ImportError("Install dagster to use pipeline.dagster_defs: pip install dagster") from exc

from pipeline.compaction import run_compaction
from pipeline.local_runner import run_load, submit_file
from pipeline.stages import build_context
from pipeline.telemetry_job import run_telemetry


class LoadConfig(Config):
    file: str
    project_id: int
    dataset: str
    mode: str = "replace"
    key_columns: list[str] = []
    keep_history: bool = False


@asset(group_name="ingest", description="Validate, profile, gate, transform, curate and register one CSV.")
def curated_dataset(context: AssetExecutionContext, config: LoadConfig) -> dict:
    ctx = build_context()
    manifest = submit_file(
        ctx, project_id=config.project_id, dataset=config.dataset, source_path=config.file,
        original_filename=os.path.basename(config.file), mode=config.mode,
        key_columns=config.key_columns, keep_history=config.keep_history, source="dagster",
    )
    final = run_load(manifest.payload(), ctx)
    context.log.info("load %s %s counts=%s", final.load_id, final.status, final.counts)
    context.add_output_metadata({"load_id": final.load_id, "status": final.status, **final.counts})
    return final.to_dict()


@asset(group_name="telemetry", description="dbt build of the gold marts from events and manifests.")
def gold_marts(context: AssetExecutionContext) -> dict:
    summary = run_telemetry(build_context())
    context.add_output_metadata({"nodes": len(summary["nodes"]), "duration_ms": summary["duration_ms"]})
    return summary


@asset(group_name="maintenance", description="Compact small files and expire old Iceberg snapshots.")
def compacted_tables(context: AssetExecutionContext) -> dict:
    report = run_compaction(build_context())
    context.add_output_metadata({"tables": report["tables"], "compacted": len(report["compacted"])})
    return report


nightly_job = define_asset_job("nightly", selection=[gold_marts, compacted_tables])

defs = Definitions(
    assets=[curated_dataset, gold_marts, compacted_tables],
    jobs=[nightly_job],
    schedules=[ScheduleDefinition(job=nightly_job, cron_schedule="0 3 * * *", execution_timezone="UTC")],
)
