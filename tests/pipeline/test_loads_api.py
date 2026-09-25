"""End-to-end through the Flask API on the local backend: upload -> pipeline
-> table registered -> merge -> rollback -> metrics."""

from pathlib import Path

from models import Load, Table
from tests.pipeline.conftest import upload


INVOICES_V1 = (
    "Invoice ID,Customer,Amount ($),Invoice Date,Paid\n"
    "1001,Acme,\"$1,200.50\",01/05/2024,yes\n"
    "1002,Globex,\"(20.00)\",02/10/2024,no\n"
    "1003,Initech,15,03/01/2024,yes\n"
)
INVOICES_V2 = (
    "Invoice ID,Customer,Amount ($),Invoice Date,Paid,Region\n"
    "1003,Initech,16,03/01/2024,yes,west\n"
    "1004,Umbrella,99.5,04/01/2024,no,east\n"
)


def test_upload_runs_pipeline_and_registers_parquet_table(app, client):
    response = upload(client, INVOICES_V1, "invoices.csv", mode="replace", key_columns="Invoice ID")
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert body["success"] is True
    load = body["loads"][0]
    assert load["status"] == "succeeded", load
    assert load["dataset"] == "invoices"
    assert load["rows_in"] == 3 and load["rows_out"] == 3
    assert load["header_renames"]["Amount ($)"] == "Amount"
    assert load["quality"]["status"] == "pass"
    assert [c["type"] for c in load["columns"]] == ["int", "str", "float", "date", "bool"]
    assert load["columns"][2]["transform"] == "currency"
    assert set(load["stages"]) == {"validate", "profile", "gate", "transform", "curate", "register"}

    schema = body["schema"]["invoices"]
    assert schema["row_count"] == 3
    assert schema["types"] == {
        "Invoice ID": "int", "Customer": "str", "Amount": "float", "Invoice Date": "str", "Paid": "str",
    }
    with app.app_context():
        table = Table.query.filter_by(dataset="invoices").one()
        assert table.source_format == "parquet"
        assert Path(table.filepath).as_posix().endswith("curated/project=%d/dataset=invoices/current/part-0.parquet" % table.project_id)
        assert table.key_columns == ["Invoice ID"]
        assert Load.query.one().table_id == table.id


def test_merge_evolves_schema_and_rollback_restores_previous_snapshot(app, client):
    first = upload(client, INVOICES_V1, "invoices.csv", mode="replace", key_columns="Invoice ID")
    assert first.status_code == 200
    second = upload(client, INVOICES_V2, "invoices.csv", mode="merge", keep_history="true")
    assert second.status_code == 200, second.get_json()
    load = second.get_json()["loads"][0]
    assert load["status"] == "succeeded", load
    assert load["counts"]["rows_inserted"] == 1
    assert load["counts"]["rows_updated"] == 1
    assert load["counts"]["history_rows"] == 2
    assert [c["column"] for c in load["schema_changes"]["added"]] == ["Region"]
    assert load["can_rollback"] is True
    assert second.get_json()["schema"]["invoices"]["row_count"] == 4

    listing = client.get("/api/loads?dataset=invoices").get_json()
    assert [item["mode"] for item in listing["loads"]] == ["merge", "replace"]

    snapshots = client.get("/api/datasets/invoices/snapshots").get_json()["snapshots"]
    assert any(s["is_current"] for s in snapshots)
    assert {s["load_id"] for s in snapshots} >= {listing["loads"][0]["load_id"], listing["loads"][1]["load_id"]}

    rolled = client.post(f"/api/loads/{load['load_id']}/rollback")
    assert rolled.status_code == 200, rolled.get_json()
    assert rolled.get_json()["schema"]["invoices"]["row_count"] == 3
    assert rolled.get_json()["load"]["status"] == "rolled_back"

    again = client.post(f"/api/loads/{load['load_id']}/rollback")
    assert again.status_code == 400

    # The agent reads the restored curated file through the normal path.
    with app.app_context():
        from engine.dataframe import DataFrame
        from services.storage_service import get_dataset_storage

        table = Table.query.filter_by(dataset="invoices").one()
        rows = list(DataFrame(get_dataset_storage().materialize(table.filepath))._get_data())
        assert sorted(r["Invoice ID"] for r in rows) == [1001, 1002, 1003]
        assert "__load_id" not in rows[0]


def test_schema_conflict_is_quarantined_with_a_reason(app, client):
    assert upload(client, INVOICES_V1, "invoices.csv", mode="replace", key_columns="Invoice ID").status_code == 200
    bad = "Invoice ID,Customer,Amount ($),Invoice Date,Paid\nA9,Acme,abc,01/05/2024,yes\n"
    response = upload(client, bad, "invoices.csv", mode="merge")
    assert response.status_code == 400
    body = response.get_json()
    assert body["success"] is False
    assert body["error_type"] == "schema"
    assert "Invoice ID" in body["error"] and "replace" in body["error"]
    assert body["loads"][0]["status"] == "quarantined"
    assert body["schema"]["invoices"]["row_count"] == 3  # untouched


def test_quality_gate_rejects_duplicate_merge_keys(app, client):
    assert upload(client, INVOICES_V1, "invoices.csv", mode="replace", key_columns="Invoice ID").status_code == 200
    dupes = "Invoice ID,Customer,Amount ($),Invoice Date,Paid\n1001,A,1,01/05/2024,yes\n1001,B,2,01/06/2024,no\n"
    response = upload(client, dupes, "invoices.csv", mode="merge")
    assert response.status_code == 400
    body = response.get_json()
    assert body["error_type"] == "quality"
    assert "repeat a merge key" in body["error"]
    assert body["loads"][0]["quality"]["failed_checks"] == ["key_unique"]


def test_merge_into_unknown_dataset_is_refused_before_landing(app, client):
    response = upload(client, INVOICES_V1, "ghost.csv", mode="merge", key_columns="Invoice ID")
    assert response.status_code == 400
    assert "replace mode first" in response.get_json()["error"]
    with app.app_context():
        assert Load.query.count() == 0


def test_metrics_endpoint_reports_live_numbers(app, client):
    upload(client, INVOICES_V1, "invoices.csv", mode="replace")
    upload(client, "a,b\n", "empty.csv", mode="replace")  # header only -> quarantined
    metrics = client.get("/api/pipeline/metrics").get_json()["metrics"]
    assert metrics["backend"] == "local"
    live = metrics["live"]
    assert live["loads_total"] == 2
    assert live["by_status"] == {"succeeded": 1, "quarantined": 1}
    assert live["quarantine_rate"] == 0.5
    assert live["rows_loaded"] == 3
    assert live["duration_ms"]["p95"] is not None
    assert metrics["gold"]["available"] is False


def test_other_users_cannot_see_or_roll_back_loads(app, client):
    response = upload(client, INVOICES_V1, "invoices.csv", mode="replace")
    load_id = response.get_json()["loads"][0]["load_id"]
    with app.app_context():
        from extensions import db
        from models import Project, User

        other = User(email="other@example.com")
        other.set_password("password-123")
        db.session.add(other)
        db.session.flush()
        db.session.add(Project(name="Theirs", user_id=other.id))
        db.session.commit()
        other_id = other.id
    stranger = app.test_client()
    with stranger.session_transaction() as flask_session:
        flask_session["user_id"] = other_id
    assert stranger.get(f"/api/loads/{load_id}").status_code == 404
    assert stranger.post(f"/api/loads/{load_id}/rollback").status_code == 404
    assert app.test_client().get(f"/api/loads/{load_id}").status_code == 401
