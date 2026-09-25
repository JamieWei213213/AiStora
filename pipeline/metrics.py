"""Read-side of the platform: numbers for the app's pipeline dashboard.

Two sources, used together:

* **live** -- computed from the manifests for one project on request. Always
  available, always current, cheap at a few hundred loads.
* **gold** -- the nightly dbt marts under ``gold/``. Daily series and agent
  cost, read with DuckDB straight from Parquet. Absent until the first
  telemetry run, and the API says so instead of pretending.
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timedelta, timezone

from pipeline.config import PipelineSettings
from pipeline.duck import connect
from pipeline.keys import gold_key
from pipeline.manifest import list_manifests
from pipeline.objectstore import ObjectStore

logger = logging.getLogger(__name__)


def _percentile(values: list[int], share: float) -> int | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(share * (len(ordered) - 1)))))
    return int(ordered[index])


def live_pipeline_metrics(store: ObjectStore, project_id: int, *, recent: int = 20) -> dict:
    manifests = list_manifests(store, project_id)
    statuses = Counter(m.status for m in manifests)
    durations = [m.total_duration_ms() for m in manifests if m.status == "succeeded"]
    rows_loaded = sum(int(m.counts.get("rows_out", 0) or 0) for m in manifests if m.status == "succeeded")
    rows_rejected = sum(int(m.counts.get("rows_rejected", 0) or 0) for m in manifests)
    raw_bytes = sum(int(m.raw_bytes or 0) for m in manifests)
    finished = sum(statuses[s] for s in ("succeeded", "quarantined", "failed"))
    datasets: dict[str, dict] = {}
    for m in manifests:
        entry = datasets.setdefault(m.dataset, {
            "dataset": m.dataset, "loads": 0, "succeeded": 0, "quarantined": 0, "failed": 0,
            "rows_current": None, "last_load_id": None, "last_status": None, "last_mode": None,
            "last_finished_at": None, "columns": None, "quality_status": None,
        })
        entry["loads"] += 1
        if m.status in ("succeeded", "quarantined", "failed"):
            entry[m.status] += 1
        entry["last_load_id"] = m.load_id
        entry["last_status"] = m.status
        entry["last_mode"] = m.mode
        entry["last_finished_at"] = m.finished_at
        if m.status == "succeeded":
            entry["rows_current"] = m.counts.get("rows_current")
            entry["columns"] = m.counts.get("columns")
            entry["quality_status"] = (m.quality or {}).get("status")
    last_day = datetime.now(timezone.utc) - timedelta(days=1)
    loads_24h = sum(1 for m in manifests if _parse(m.created_at) and _parse(m.created_at) >= last_day)
    return {
        "loads_total": len(manifests),
        "loads_24h": loads_24h,
        "by_status": dict(statuses),
        "quarantine_rate": round(statuses["quarantined"] / finished, 4) if finished else 0.0,
        "failure_rate": round(statuses["failed"] / finished, 4) if finished else 0.0,
        "rows_loaded": rows_loaded,
        "rows_rejected": rows_rejected,
        "raw_bytes": raw_bytes,
        "duration_ms": {
            "avg": int(sum(durations) / len(durations)) if durations else None,
            "p50": _percentile(durations, 0.5),
            "p95": _percentile(durations, 0.95),
            "max": max(durations) if durations else None,
        },
        "throughput_rows_per_second": (
            round(rows_loaded / (sum(durations) / 1000.0), 1) if durations and sum(durations) else None
        ),
        "datasets": sorted(datasets.values(), key=lambda d: d["dataset"]),
        "recent": [_summarize(m) for m in manifests[-recent:]][::-1],
    }


def _parse(value):
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _summarize(m) -> dict:
    return {
        "load_id": m.load_id,
        "dataset": m.dataset,
        "mode": m.mode,
        "source": m.source,
        "status": m.status,
        "stage": m.stage,
        "created_at": m.created_at,
        "finished_at": m.finished_at,
        "duration_ms": m.total_duration_ms(),
        "rows_in": m.counts.get("rows_in"),
        "rows_out": m.counts.get("rows_out"),
        "rows_rejected": m.counts.get("rows_rejected"),
        "quality_status": (m.quality or {}).get("status"),
        "error": m.error,
    }


def gold_metrics(settings: PipelineSettings, store: ObjectStore, project_id: int, *, days: int = 30) -> dict:
    """Daily series from the dbt marts, or ``{"available": False}``."""
    marts = {name: gold_key(name) for name in ("agg_daily_pipeline", "agg_daily_agent", "agg_dataset_health")}
    try:
        present = {name: store.exists(key) for name, key in marts.items()}
    except Exception:
        logger.debug("gold availability check failed", exc_info=True)
        present = {name: False for name in marts}
    if not any(present.values()):
        return {"available": False, "reason": "The nightly telemetry job has not produced marts yet."}
    con = connect(settings)
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).date().isoformat()
        out = {"available": True, "days": days}
        if present["agg_daily_pipeline"]:
            out["daily_pipeline"] = _rows(con, f"""
                SELECT dt, loads, succeeded, quarantined, failed, quarantine_rate, rows_loaded,
                       rows_rejected, raw_bytes, avg_duration_ms, p95_duration_ms
                FROM read_parquet('{store.url(marts['agg_daily_pipeline'])}', hive_partitioning=false)
                WHERE project_id = {int(project_id)} AND dt >= DATE '{since}' ORDER BY dt""")
        if present["agg_daily_agent"]:
            out["daily_agent"] = _rows(con, f"""
                SELECT dt, runs, finished, success_rate, verification_pass_rate, advanced_runs,
                       input_tokens, output_tokens, estimated_cost_usd, avg_duration_ms, p95_duration_ms
                FROM read_parquet('{store.url(marts['agg_daily_agent'])}', hive_partitioning=false)
                WHERE project_id = {int(project_id)} AND dt >= DATE '{since}' ORDER BY dt""")
        if present["agg_dataset_health"]:
            out["dataset_health"] = _rows(con, f"""
                SELECT dataset, loads, succeeded, quarantined, failed, first_load_at, last_load_at,
                       last_rows_out, last_quality_status, rows_rejected_total, avg_duration_ms
                FROM read_parquet('{store.url(marts['agg_dataset_health'])}', hive_partitioning=false)
                WHERE project_id = {int(project_id)} ORDER BY dataset""")
        return out
    except Exception as exc:
        logger.warning("gold marts unreadable: %s", exc)
        return {"available": False, "reason": f"gold marts unreadable: {str(exc)[:200]}"}
    finally:
        con.close()


def _rows(con, sql: str) -> list[dict]:
    cursor = con.execute(sql)
    names = [d[0] for d in cursor.description]
    rows = []
    for record in cursor.fetchall():
        row = {}
        for name, value in zip(names, record):
            if hasattr(value, "isoformat"):
                value = value.isoformat()
            row[name] = value
        rows.append(row)
    return rows
