"""AWS Lambda entry point. One image, one handler, dispatched on the event.

Step Functions calls it with ``{"stage": "<name>", "payload": {...}}`` for
each state of the ingest state machine; EventBridge Scheduler calls it with
``{"job": "telemetry" | "compaction" | "connector", ...}`` for the nightly
and scheduled jobs. Keeping one function keeps one IAM role, one log group
and one image to build.
"""

from __future__ import annotations

import json
import logging
import os

from pipeline import events
from pipeline.stages import RetryableStageError, StageError, build_context, run_stage

logger = logging.getLogger()
logger.setLevel(os.environ.get("LOG_LEVEL", "INFO"))

_ctx = None


def _context():
    global _ctx
    if _ctx is None:
        _ctx = build_context()
    return _ctx


class RetryableError(Exception):
    """Named so the state machine's Retry block can match on it."""


def handler(event, context=None):
    logger.info("event %s", json.dumps(event, default=str)[:2000])
    ctx = _context()
    try:
        if "stage" in event:
            stage = str(event["stage"])
            payload = event.get("payload") or event
            if stage == "parse_event":
                return run_stage("parse_event", event.get("event") or payload, ctx)
            if stage == "on_failure":
                merged = dict(payload)
                merged["error"] = event.get("error") or payload.get("error")
                return run_stage("on_failure", merged, ctx)
            return run_stage(stage, payload, ctx)
        job = str(event.get("job") or "")
        if job == "telemetry":
            from pipeline.telemetry_job import run_telemetry

            return run_telemetry(ctx)
        if job == "compaction":
            from pipeline.compaction import run_compaction

            return run_compaction(ctx)
        if job == "connector":
            from pipeline.connectors.runner import run_connector

            return run_connector(ctx, str(event.get("connector_id") or ""))
        raise StageError(f"Unrecognised event: {list(event)[:5]}")
    except RetryableStageError as exc:
        raise RetryableError(str(exc)) from exc
    finally:
        events.flush()
