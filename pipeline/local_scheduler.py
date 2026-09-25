"""A small scheduler for the local backend (docker-compose).

On AWS, EventBridge Scheduler invokes the nightly jobs and each connector.
Locally there is no EventBridge, so this loop does the same: the telemetry
build and compaction once a day at ``PIPELINE_NIGHTLY_UTC_HOUR`` (03:00 by
default), and every enabled connector on its ``rate(...)`` schedule. It is a
separate compose service so a long dbt build never blocks a web worker.

Run: ``python -m pipeline.local_scheduler`` (or ``--once`` for a single pass).
"""

from __future__ import annotations

import argparse
import logging
import re
import time
from datetime import datetime, timezone

from pipeline import events
from pipeline.connectors.base import ConnectorConfig, ConnectorState
from pipeline.stages import build_context

logger = logging.getLogger(__name__)

_RATE = re.compile(r"^rate\((\d+)\s+(minute|minutes|hour|hours|day|days)\)$")


def rate_seconds(schedule: str, default: int = 6 * 3600) -> int:
    """``rate(6 hours)`` -> 21600. ``cron(...)`` is EventBridge-only locally."""
    match = _RATE.match(str(schedule or "").strip())
    if not match:
        return default
    value, unit = int(match.group(1)), match.group(2)
    if unit.startswith("minute"):
        return value * 60
    if unit.startswith("hour"):
        return value * 3600
    return value * 86400


def _parse(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        parsed = datetime.fromisoformat(ts)
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def run_due_connectors(ctx, now: datetime | None = None) -> list[dict]:
    from pipeline.connectors.runner import run_connector

    now = now or datetime.now(timezone.utc)
    reports = []
    for config in ConnectorConfig.list_all(ctx.store):
        if not config.enabled:
            continue
        state = ConnectorState.load(ctx.store, config.id)
        last = _parse(state.last_run_at)
        if last is not None and (now - last).total_seconds() < rate_seconds(config.schedule):
            continue
        logger.info("running connector %s", config.id)
        reports.append(run_connector(ctx, config.id))
    return reports


def run_nightly(ctx) -> dict:
    from pipeline.compaction import run_compaction
    from pipeline.telemetry_job import TelemetryJobError, run_telemetry

    report = {}
    try:
        report["telemetry"] = {"success": run_telemetry(ctx)["success"]}
    except TelemetryJobError as exc:
        report["telemetry"] = {"success": False, "error": str(exc)}
        logger.error("telemetry build failed: %s", exc)
    report["compaction"] = {k: v for k, v in run_compaction(ctx).items() if not isinstance(v, list)}
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Local pipeline scheduler")
    parser.add_argument("--once", action="store_true", help="run due work once and exit")
    parser.add_argument("--nightly-hour", type=int, default=None, help="UTC hour for the nightly jobs (default 3)")
    parser.add_argument("--interval", type=int, default=60, help="seconds between checks")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    import os

    nightly_hour = args.nightly_hour if args.nightly_hour is not None else int(os.environ.get("PIPELINE_NIGHTLY_UTC_HOUR", "3"))
    ctx = build_context()
    last_nightly_day = None
    while True:
        now = datetime.now(timezone.utc)
        try:
            run_due_connectors(ctx, now)
            if args.once or (now.hour == nightly_hour and last_nightly_day != now.date()):
                logger.info("nightly jobs: %s", run_nightly(ctx))
                last_nightly_day = now.date()
        except Exception:  # noqa: BLE001 - the loop must survive
            logger.exception("scheduler pass failed")
        finally:
            events.flush()
        if args.once:
            return 0
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
