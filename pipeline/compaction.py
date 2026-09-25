"""Nightly table maintenance.

Every append or merge adds data files; a dataset loaded daily for a year has
hundreds of small files and every scan opens all of them. Compaction rewrites
a table whose file count passed ``PIPELINE_COMPACTION_MIN_FILES`` as a fresh
snapshot with one file per ~128 MB, then expires snapshots older than the
retention window so the rewritten files can be deleted.

PyIceberg 0.12 has no ``rewrite_data_files``; ``overwrite`` of the full scan
is the honest equivalent at these volumes and it is a normal snapshot, so a
compaction is as reversible as a load.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

from pipeline import events
from pipeline.stages import StageContext

logger = logging.getLogger(__name__)


def _data_file_count(table) -> int:
    snapshot = table.current_snapshot()
    if snapshot is None:
        return 0
    try:
        return int((snapshot.summary.additional_properties or {}).get("total-data-files", 0) or 0)
    except (AttributeError, TypeError, ValueError):
        return 0


def _expire_snapshots(table, older_than: datetime) -> int:
    """Expire snapshots older than the cut-off; the current one is always kept."""
    maintenance = getattr(table, "maintenance", None)
    if maintenance is None:  # pragma: no cover - older PyIceberg
        return 0
    try:
        before = len(list(table.snapshots()))
        maintenance.expire_snapshots().older_than(older_than.replace(tzinfo=None)).commit()
        table.refresh()
        return before - len(list(table.snapshots()))
    except Exception:  # pragma: no cover - API drift across versions
        logger.warning("Snapshot expiry unavailable for %s", table.name(), exc_info=True)
        return 0


def run_compaction(ctx: StageContext) -> dict:
    lake = ctx.lakehouse
    catalog = lake.catalog
    namespace = ctx.settings.catalog_namespace
    started = time.perf_counter()
    report = {"tables": 0, "compacted": [], "expired_snapshots": 0, "skipped": []}
    cutoff = datetime.now(timezone.utc) - timedelta(days=ctx.settings.snapshot_retention_days)
    for identifier in catalog.list_tables(namespace):
        table = catalog.load_table(identifier)
        name = ".".join(identifier) if isinstance(identifier, tuple) else str(identifier)
        report["tables"] += 1
        files = _data_file_count(table)
        if files >= ctx.settings.compaction_min_files and table.current_snapshot() is not None:
            arrow = table.scan().to_arrow()
            table.overwrite(arrow, snapshot_properties={"aistora.maintenance": "compaction"})
            table.refresh()
            report["compacted"].append({"table": name, "files_before": files, "rows": arrow.num_rows})
            events.emit("pipeline.compaction", table=name, files_before=files, rows=arrow.num_rows)
        else:
            report["skipped"].append({"table": name, "files": files})
        report["expired_snapshots"] += _expire_snapshots(table, cutoff)
    report["duration_ms"] = int((time.perf_counter() - started) * 1000)
    events.emit("pipeline.maintenance", **{k: v for k, v in report.items() if not isinstance(v, list)})
    logger.info("compaction %s", report)
    return report
