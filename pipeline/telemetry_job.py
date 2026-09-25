"""Nightly telemetry job: run the dbt project and write the gold marts.

dbt reads the events and manifests straight from the lake with DuckDB and
writes each mart as a Parquet file under ``gold/``. The app's metrics API
reads those files; it never needs dbt installed.

dbt-core 1.x (Python) is used rather than the new Rust-based dbt v2: v2 went
GA in September 2026 and has not been exercised inside a Lambda container
long enough to bet the nightly job on it. The project parses cleanly under
the v2 parser, so the switch is a Dockerfile change when it is time.
"""

from __future__ import annotations

import logging
import os
import time

from pipeline import events
from pipeline.stages import StageContext

logger = logging.getLogger(__name__)


class TelemetryJobError(RuntimeError):
    pass


def dbt_environment(ctx: StageContext) -> dict:
    scratch = ctx.settings.scratch_dir
    os.makedirs(scratch, exist_ok=True)
    env = {
        "LAKE_URI": ctx.settings.lake_uri,
        "DBT_TARGET": "aws" if ctx.settings.is_aws else "local",
        "DBT_DUCKDB_PATH": os.path.join(scratch, "telemetry.duckdb"),
        "DBT_TARGET_PATH": os.path.join(scratch, "dbt-target"),
        "DBT_LOG_PATH": os.path.join(scratch, "dbt-logs"),
        "DBT_PACKAGES_PATH": os.path.join(scratch, "dbt-packages"),
        "DBT_PROFILES_DIR": ctx.settings.dbt_project_dir,
        "DBT_SEND_ANONYMOUS_USAGE_STATS": "false",
        "AWS_REGION": ctx.settings.aws_region,
    }
    return env


def run_dbt(ctx: StageContext, args: list[str] | None = None) -> dict:
    try:
        from dbt.cli.main import dbtRunner
    except ImportError as exc:  # pragma: no cover - app image
        raise TelemetryJobError(
            "dbt-core and dbt-duckdb are not installed in this image; the "
            "telemetry job runs from the pipeline image."
        ) from exc

    env = dbt_environment(ctx)
    if not ctx.settings.is_aws:
        # dbt-duckdb writes external Parquet with a plain COPY, which does
        # not create the directory.
        os.makedirs(os.path.join(ctx.settings.lake_uri, "gold"), exist_ok=True)
    previous = {key: os.environ.get(key) for key in env}
    os.environ.update(env)
    # A stale DuckDB file from a previous run holds the old views; start clean.
    try:
        os.remove(env["DBT_DUCKDB_PATH"])
    except FileNotFoundError:
        pass
    command = list(args or ["build"]) + [
        "--project-dir", ctx.settings.dbt_project_dir,
        "--profiles-dir", ctx.settings.dbt_project_dir,
        "--no-use-colors",
        "--target-path", env["DBT_TARGET_PATH"],
        "--log-path", env["DBT_LOG_PATH"],
    ]
    started = time.perf_counter()
    try:
        result = dbtRunner().invoke(command)
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    duration_ms = int((time.perf_counter() - started) * 1000)
    nodes = []
    if getattr(result, "result", None) is not None and hasattr(result.result, "results"):
        for item in result.result.results:
            nodes.append({
                "node": getattr(item.node, "name", str(item.node)),
                "status": str(getattr(item, "status", "")),
                "message": (getattr(item, "message", "") or "")[:300],
                "execution_time": round(float(getattr(item, "execution_time", 0) or 0), 3),
            })
    summary = {
        "success": bool(result.success),
        "command": command[0],
        "duration_ms": duration_ms,
        "nodes": nodes,
        "failed": [n for n in nodes if n["status"] not in {"success", "pass", "skipped"}],
    }
    if result.exception is not None:
        summary["exception"] = str(result.exception)[:500]
    return summary


def run_telemetry(ctx: StageContext) -> dict:
    summary = run_dbt(ctx, ["build"])
    events.emit(
        "pipeline.telemetry", success=summary["success"], duration_ms=summary["duration_ms"],
        nodes=len(summary["nodes"]), failed=len(summary["failed"]),
    )
    events.flush()
    if not summary["success"]:
        logger.error("dbt build failed: %s", summary.get("failed") or summary.get("exception"))
        raise TelemetryJobError(
            "dbt build failed: " + "; ".join(f"{n['node']} {n['status']}" for n in summary["failed"])
            if summary["failed"] else summary.get("exception", "unknown error")
        )
    return summary
