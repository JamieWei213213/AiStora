"""Run the stages in-process: docker-compose, development, tests, and the
optional Dagster wrapper all go through here.

On AWS the same functions are called one per Lambda invocation by the Step
Functions state machine in ``infra/terraform/pipeline``; this module is the
orchestrator you get when there is no orchestrator.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import threading
import traceback

from pipeline import events
from pipeline.ids import new_ulid
from pipeline.keys import LoadRef, dataset_slug
from pipeline.manifest import LoadManifest, TERMINAL_STATUSES
from pipeline.stages import STAGE_ORDER, RetryableStageError, StageContext, build_context, new_manifest, run_stage

logger = logging.getLogger(__name__)


def run_load(payload: dict, ctx: StageContext | None = None, *, max_lock_retries: int = 3) -> LoadManifest:
    """Execute every stage for one load and return the final manifest."""
    ctx = ctx or build_context()
    current = dict(payload)
    for stage in STAGE_ORDER:
        attempts = 0
        while True:
            try:
                current = run_stage(stage, current, ctx)
                break
            except RetryableStageError:
                attempts += 1
                if attempts > max_lock_retries:
                    current = run_stage(
                        "on_failure",
                        {**current, "error": {"Cause": "Timed out waiting for the dataset lock."}},
                        ctx,
                    )
                    return LoadManifest.load(ctx.store, _ref(current))
            except Exception as exc:  # noqa: BLE001 - every failure must land in the manifest
                logger.error("Stage %s failed for %s: %s", stage, current.get("load_id"), exc)
                logger.debug(traceback.format_exc())
                current = run_stage(
                    "on_failure",
                    {**current, "error": {"Cause": f"{type(exc).__name__}: {exc}"}},
                    ctx,
                )
                return LoadManifest.load(ctx.store, _ref(current))
        if current.get("status") in TERMINAL_STATUSES:
            break
    return LoadManifest.load(ctx.store, _ref(current))


def _ref(payload: dict) -> LoadRef:
    return LoadRef(int(payload["project_id"]), str(payload["dataset"]), str(payload["load_id"]))


def submit_file(
    ctx: StageContext,
    *,
    project_id: int,
    dataset: str,
    source_path: str,
    original_filename: str,
    mode: str = "replace",
    key_columns=None,
    keep_history: bool = False,
    source: str = "upload",
) -> LoadManifest:
    """Put a file into ``raw/`` with its manifest. Does not run the stages."""
    slug = dataset_slug(dataset)
    load_id = new_ulid()
    ref = LoadRef(int(project_id), slug, load_id)
    filename = os.path.basename(original_filename) or "upload.csv"
    raw_key = ref.raw_key(filename)
    manifest = new_manifest(
        project_id=project_id, dataset=slug, load_id=load_id, mode=mode,
        original_filename=filename, raw_key=raw_key,
        raw_bytes=os.path.getsize(source_path), key_columns=key_columns,
        keep_history=keep_history, source=source,
    )
    # Manifest first, object second: an S3 event for the object must always
    # find its manifest, but an orphan manifest is harmless.
    manifest.save(ctx.store)
    ctx.store.put_file(raw_key, source_path)
    events.emit(
        "pipeline.received", load_id=load_id, project_id=int(project_id), dataset=slug,
        mode=mode, source=source, raw_bytes=manifest.raw_bytes,
    )
    return manifest


_background: dict[str, threading.Thread] = {}


def run_in_background(payload: dict, ctx: StageContext) -> threading.Thread:
    """Local backend only: run the load on a daemon thread so the upload
    request returns at once and the UI polls, exactly as it does on AWS."""

    def target():
        try:
            run_load(payload, ctx)
        finally:
            _background.pop(payload.get("load_id", ""), None)

    thread = threading.Thread(target=target, name=f"load-{payload.get('load_id')}", daemon=True)
    _background[str(payload.get("load_id"))] = thread
    thread.start()
    return thread


def wait_for_background(timeout: float = 60.0) -> None:
    for thread in list(_background.values()):
        thread.join(timeout)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Run the AIStora load pipeline locally.")
    parser.add_argument("file", help="CSV file to load")
    parser.add_argument("--project", type=int, required=True)
    parser.add_argument("--dataset", help="dataset slug (defaults to the file name)")
    parser.add_argument("--mode", choices=("replace", "append", "merge"), default="replace")
    parser.add_argument("--key", action="append", default=[], help="merge key column (repeatable)")
    parser.add_argument("--history", action="store_true", help="keep type-2 history on merge")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    ctx = build_context()
    manifest = submit_file(
        ctx, project_id=args.project, dataset=args.dataset or os.path.basename(args.file),
        source_path=args.file, original_filename=os.path.basename(args.file),
        mode=args.mode, key_columns=args.key, keep_history=args.history, source="cli",
    )
    final = run_load(manifest.payload(), ctx)
    print(f"{final.load_id} {final.status} rows_in={final.counts.get('rows_in')} "
          f"rows_out={final.counts.get('rows_out')} snapshot={final.snapshot_id}")
    if final.error:
        print(f"  {final.error['type']}: {final.error['message']}")
    return 0 if final.status == "succeeded" else 1


if __name__ == "__main__":
    sys.exit(main())
