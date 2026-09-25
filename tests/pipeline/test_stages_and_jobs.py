"""Stage orchestration, lakehouse semantics, failure handling, events,
connectors, the Lambda dispatcher, compaction and (when dbt is installed)
the telemetry build."""

import gzip
import json
import os

import pyarrow as pa
import pytest

from pipeline import events as pipeline_events
from pipeline import stages as stage_module
from pipeline.compaction import run_compaction
from pipeline.connectors import runner as connector_runner
from pipeline.connectors.base import Connector, ConnectorConfig, ConnectorError, ConnectorState, ExtractResult
from pipeline.contract import Contract
from pipeline.keys import LoadRef, contract_key, curated_key
from pipeline.lakehouse import Lakehouse
from pipeline.lambda_handler import RetryableError, handler
from pipeline.local_runner import run_load, submit_file
from pipeline.manifest import LoadManifest, list_manifests
from pipeline.metrics import live_pipeline_metrics
from pipeline.stages import RetryableStageError, run_stage
from tests.pipeline.conftest import write_csv

V1 = "id,name,amount\n1,a,10\n2,b,20\n"
V2 = "id,name,amount,region\n2,b,21,west\n3,c,30,east\n"


def _load(ctx, tmp_path, text, name, **kw):
    path = write_csv(tmp_path / "in" / name, text)
    manifest = submit_file(ctx, project_id=1, dataset="items", source_path=path, original_filename=name, **kw)
    return run_load(manifest.payload(), ctx)


def test_full_lifecycle_replace_merge_append_and_history(ctx, tmp_path, store):
    first = _load(ctx, tmp_path, V1, "items.csv", mode="replace", key_columns=["id"])
    assert first.status == "succeeded", first.error
    assert first.counts["rows_current"] == 2 and first.parent_snapshot_id is None
    assert store.exists(contract_key(1, "items")) and store.exists(curated_key(1, "items"))
    assert not list(store.list("work/"))

    second = _load(ctx, tmp_path, V2, "items.csv", mode="merge", key_columns=["id"], keep_history=True)
    assert second.status == "succeeded", second.error
    assert second.counts["rows_inserted"] == 1 and second.counts["rows_updated"] == 1
    assert second.counts["history_rows"] == 2 and second.counts["rows_current"] == 3
    assert [c["column"] for c in second.schema_changes["added"]] == ["region"]
    assert second.parent_snapshot_id == first.snapshot_id

    third = _load(ctx, tmp_path, V2, "items.csv", mode="append")
    assert third.status == "succeeded"
    assert third.counts["rows_skipped"] == 2 and third.counts["rows_inserted"] == 0
    assert third.snapshot_id == second.snapshot_id  # nothing written, no new snapshot

    lake = Lakehouse(ctx.settings, store)
    snaps = lake.snapshots(1, "items")
    assert snaps[-1]["is_current"] and {s["load_id"] for s in snaps} == {first.load_id, second.load_id}
    history = lake.load_table(1, "items", history=True).scan().to_arrow().to_pylist()
    assert sorted((r["id"], r["__change"]) for r in history) == [(2, "update"), (3, "insert")]

    lake.rollback_to(1, "items", first.snapshot_id)
    assert lake.row_count(1, "items") == 2

    reset = _load(ctx, tmp_path, "id,name,amount\nA,x,1\n", "items.csv", mode="replace")
    assert reset.status == "succeeded" and reset.reset_schema is True
    assert reset.schema_changes["reset"] is True
    assert Contract.from_dict(store.get_json(contract_key(1, "items"))).column("id").type == "str"

    manifests = list_manifests(store, 1, "items")
    assert [m.status for m in manifests] == ["succeeded"] * 4
    metrics = live_pipeline_metrics(store, 1)
    assert metrics["loads_total"] == 4 and metrics["by_status"] == {"succeeded": 4}
    assert metrics["datasets"][0]["rows_current"] == 1


def test_quarantine_paths_and_reports(ctx, tmp_path, store):
    empty = _load(ctx, tmp_path, "a,b\n", "items.csv", mode="replace")
    assert empty.status == "quarantined" and empty.error["type"] == "quality"
    assert empty.stage == "gate"
    report = store.get_json(empty.ref.quarantine_key("report.json"))
    assert report["quality"]["failed_checks"] == ["row_count"]
    assert store.exists(empty.ref.quarantine_key("items.csv"))

    merge_without_table = _load(ctx, tmp_path, V1, "items.csv", mode="merge", key_columns=["nope"])
    assert merge_without_table.status == "quarantined" and merge_without_table.error["type"] == "schema"
    assert "nope" in merge_without_table.error["message"]

    bad_mode = LoadManifest(load_id="01ARZ3NDEKTSV4RRFFQ69G5FAV", project_id=1, dataset="items", mode="upsert",
                            raw_key="raw/x.csv")
    bad_mode.save(store)
    result = run_stage("validate", bad_mode.payload(), ctx)
    assert result["status"] == "quarantined"


def test_stage_exception_lands_in_manifest_as_failed(ctx, tmp_path, store, monkeypatch):
    def boom(ctx_, payload):
        raise RuntimeError("disk on fire")

    monkeypatch.setitem(stage_module.STAGES, "transform", boom)
    final = _load(ctx, tmp_path, V1, "items.csv", mode="replace")
    assert final.status == "failed"
    assert final.error == {"type": "internal", "message": "RuntimeError: disk on fire"}
    assert final.stages["gate"]["status"] == "succeeded"
    assert not list(store.list("work/"))


def test_rerunning_a_stage_is_idempotent(ctx, tmp_path, store):
    path = write_csv(tmp_path / "in" / "items.csv", V1)
    manifest = submit_file(ctx, project_id=1, dataset="items", source_path=path, original_filename="items.csv")
    payload = manifest.payload()
    for stage in ("validate", "profile", "profile", "gate", "transform", "transform", "curate", "register"):
        payload = run_stage(stage, payload, ctx)
    final = LoadManifest.load(store, manifest.ref)
    assert final.status == "succeeded" and final.counts["rows_current"] == 2


def test_dataset_lock_blocks_concurrent_writer(ctx, tmp_path, store, monkeypatch):
    monkeypatch.setattr(stage_module, "LOCK_WAIT_SECONDS", 0)
    path = write_csv(tmp_path / "in" / "items.csv", V1)
    manifest = submit_file(ctx, project_id=1, dataset="items", source_path=path, original_filename="items.csv")
    payload = manifest.payload()
    for stage in ("validate", "profile", "gate", "transform"):
        payload = run_stage(stage, payload, ctx)
    assert store.put_bytes_if_absent(manifest.ref.lock_key(), b"OTHERLOAD 2099-01-01T00:00:00+00:00")
    with pytest.raises(RetryableStageError):
        run_stage("curate", payload, ctx)
    store.delete(manifest.ref.lock_key())
    assert run_stage("curate", payload, ctx)["status"] == "running"


def test_stale_lock_is_broken(ctx, tmp_path, store, monkeypatch):
    monkeypatch.setattr(stage_module, "LOCK_WAIT_SECONDS", 0)
    path = write_csv(tmp_path / "in" / "items.csv", V1)
    manifest = submit_file(ctx, project_id=1, dataset="items", source_path=path, original_filename="items.csv")
    payload = manifest.payload()
    for stage in ("validate", "profile", "gate", "transform"):
        payload = run_stage(stage, payload, ctx)
    store.put_bytes_if_absent(manifest.ref.lock_key(), b"DEADLOAD 2020-01-01T00:00:00+00:00")
    assert run_stage("curate", payload, ctx)["status"] == "running"
    assert not store.exists(manifest.ref.lock_key())


def test_parse_event_creates_manifest_for_a_raw_drop(ctx, store, tmp_path, monkeypatch):
    import pipeline.lambda_handler as lh

    monkeypatch.setattr(lh, "_ctx", ctx)
    ref = LoadRef(3, "dropped", "01ARZ3NDEKTSV4RRFFQ69G5FAV")
    store.put_bytes(ref.raw_key("dropped.csv"), b"x,y\n1,2\n")
    payload = handler({"stage": "parse_event", "event": {"detail": {"object": {"key": ref.raw_key("dropped.csv")}}}})
    assert payload["load_id"] == ref.load_id and payload["dataset"] == "dropped"
    manifest = LoadManifest.load(store, ref)
    assert manifest.source == "drop" and manifest.mode == "replace" and manifest.raw_bytes == 8
    final = run_load(payload, ctx)
    assert final.status == "succeeded"


def test_lambda_handler_dispatch(ctx, tmp_path, store, monkeypatch):
    import pipeline.lambda_handler as lh

    monkeypatch.setattr(lh, "_ctx", ctx)
    path = write_csv(tmp_path / "in" / "items.csv", V1)
    manifest = submit_file(ctx, project_id=1, dataset="items", source_path=path, original_filename="items.csv")
    out = handler({"stage": "validate", "payload": manifest.payload()})
    assert out["status"] == "running" and out["stage"] == "validate"
    failed = handler({"stage": "on_failure", "payload": out, "error": {"Error": "States.Timeout", "Cause": "took too long"}})
    assert failed["status"] == "failed"
    assert LoadManifest.load(store, manifest.ref).error["message"] == "States.Timeout: took too long"
    with pytest.raises(Exception):
        handler({"nonsense": True})

    def locked(name, payload, ctx_=None):
        raise RetryableStageError("busy")

    monkeypatch.setattr(lh, "run_stage", locked)
    with pytest.raises(RetryableError):
        handler({"stage": "curate", "payload": out})


def test_event_sink_batches_gzipped_jsonl(ctx, store):
    sink = pipeline_events.get_sink()
    sink.emit("unit.test", project_id=1, nested={"a": 1}, skip=None)
    sink.emit("unit.test", project_id=2)
    assert sink.pending == 2
    assert sink.flush() == 2
    keys = list(store.list("events/"))
    assert len(keys) == 1 and keys[0].endswith(".jsonl.gz")
    lines = gzip.decompress(store.get_bytes(keys[0])).decode().splitlines()
    first = json.loads(lines[0])
    assert first["event"] == "unit.test" and first["nested"] == {"a": 1} and "skip" not in first
    assert sink.flush() == 0


class FakeSource(Connector):
    type = "fake"
    rows = "id,v\n1,a\n2,b\n"

    def extract(self, state, workdir):
        if self.secret == "boom":
            raise ConnectorError("cannot reach source")
        if state.cursor == 2:
            return ExtractResult(path=None, rows=0, cursor=2)
        os.makedirs(workdir, exist_ok=True)
        path = os.path.join(workdir, "e.csv")
        open(path, "w").write(self.rows)
        return ExtractResult(path=path, rows=2, cursor=2, filename="crm_orders.csv")


def test_connector_runner_lands_files_and_tracks_cursor(ctx, store, monkeypatch):
    from pipeline.connectors import base as connector_base

    monkeypatch.setitem(connector_runner.IMPLEMENTATIONS, "fake", FakeSource)
    monkeypatch.setattr(connector_base, "CONNECTOR_TYPES", connector_base.CONNECTOR_TYPES + ("fake",))
    monkeypatch.setenv("CRM_DSN", "postgresql://example")
    config = ConnectorConfig(id="crm-orders", type="postgres", project_id=1, dataset="Orders",
                             mode="merge", key_columns=["id"], secret_ref="CRM_DSN")
    config.type = "fake"
    config.save(store)
    report = connector_runner.run_connector(ctx, "crm-orders")
    assert report["status"] == "landed" and report["load_status"] == "succeeded", report
    state = ConnectorState.load(store, "crm-orders")
    assert state.cursor == 2 and state.runs == 1 and state.last_status == "succeeded"
    manifest = list_manifests(store, 1, "orders")[0]
    assert manifest.source == "connector:crm-orders" and manifest.original_filename == "crm_orders.csv"

    again = connector_runner.run_connector(ctx, "crm-orders")
    assert again["status"] == "empty"
    assert ConnectorState.load(store, "crm-orders").runs == 2

    monkeypatch.setenv("CRM_DSN", "boom")
    failed = connector_runner.run_connector(ctx, "crm-orders")
    assert failed["status"] == "failed" and "cannot reach" in failed["error"]
    assert ConnectorState.load(store, "crm-orders").last_status == "failed"

    with pytest.raises(ConnectorError):
        ConnectorConfig(id="x", type="postgres", project_id=1, dataset="d", mode="merge").validate()
    with pytest.raises(ConnectorError):
        ConnectorConfig(id="Bad Id", type="postgres", project_id=1, dataset="d").validate()


def test_postgres_source_builds_safe_sql():
    from pipeline.connectors.postgres_source import _ident

    assert _ident("public.orders") == '"public"."orders"'
    with pytest.raises(ConnectorError):
        _ident("orders; drop table x")


def test_compaction_rewrites_fragmented_tables(ctx, tmp_path, store):
    settings = type(ctx.settings)(**{**ctx.settings.__dict__, "compaction_min_files": 3})
    ctx.settings = settings
    for i in range(4):
        m = _load(ctx, tmp_path, f"id,v\n{i},x\n", "items.csv", mode="append" if i else "replace")
        assert m.status == "succeeded"
    lake = Lakehouse(settings, store)
    table = lake.load_table(1, "items")
    before = int(table.current_snapshot().summary.additional_properties["total-data-files"])
    assert before == 4
    report = run_compaction(ctx)
    assert [c["table"] for c in report["compacted"]] == ["aistora.p1__items"]
    table.refresh()
    assert int(table.current_snapshot().summary.additional_properties["total-data-files"]) == 1
    assert lake.row_count(1, "items") == 4


def test_telemetry_build_produces_gold_marts(ctx, tmp_path, store):
    pytest.importorskip("dbt.cli.main")
    from pipeline.metrics import gold_metrics
    from pipeline.telemetry_job import run_telemetry

    assert _load(ctx, tmp_path, V1, "items.csv", mode="replace").status == "succeeded"
    assert _load(ctx, tmp_path, "a,b\n", "items.csv", mode="replace").status == "quarantined"
    pipeline_events.emit("agent.run", request_id="r1", project_id=1, user_id=1, status="finished",
                         routing_tier="standard", duration_ms=1200, turns=2, tool_calls=3,
                         input_tokens=100, output_tokens=20, estimated_cost_usd=0.001, verification_passed=True)
    pipeline_events.flush()
    summary = run_telemetry(ctx)
    assert summary["success"] and not summary["failed"]
    assert {os.path.basename(k) for k in store.list("gold/")} >= {
        "fct_loads.parquet", "fct_agent_runs.parquet", "agg_daily_pipeline.parquet",
        "agg_daily_agent.parquet", "agg_dataset_health.parquet",
    }
    gold = gold_metrics(ctx.settings, store, 1)
    assert gold["available"] is True
    assert gold["daily_pipeline"][0]["loads"] == 2 and gold["daily_pipeline"][0]["quarantined"] == 1
    assert gold["daily_agent"][0]["runs"] == 1 and gold["daily_agent"][0]["input_tokens"] == 100
    assert gold["dataset_health"][0]["dataset"] == "items"


def test_local_scheduler_runs_due_connectors_only(ctx, store, monkeypatch):
    from pipeline import local_scheduler
    from pipeline.connectors import base as connector_base

    monkeypatch.setitem(connector_runner.IMPLEMENTATIONS, "fake", FakeSource)
    monkeypatch.setattr(connector_base, "CONNECTOR_TYPES", connector_base.CONNECTOR_TYPES + ("fake",))
    assert local_scheduler.rate_seconds("rate(6 hours)") == 21600
    assert local_scheduler.rate_seconds("rate(15 minutes)") == 900
    assert local_scheduler.rate_seconds("cron(0 3 * * ? *)") == 21600
    config = ConnectorConfig(id="sheet", type="postgres", project_id=1, dataset="sheet", schedule="rate(1 hours)")
    config.type = "fake"
    config.save(store)
    first = local_scheduler.run_due_connectors(ctx)
    assert [r["status"] for r in first] == ["landed"]
    assert local_scheduler.run_due_connectors(ctx) == []   # not due yet
    config.enabled = False
    config.save(store)
    from datetime import datetime, timedelta, timezone

    assert local_scheduler.run_due_connectors(ctx, datetime.now(timezone.utc) + timedelta(hours=2)) == []
