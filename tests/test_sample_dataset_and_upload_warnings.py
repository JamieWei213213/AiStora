"""Sample-dataset onboarding, upload warnings and delimiter handling end to end."""
import io
from pathlib import Path

import pytest
from flask import Flask

from extensions import db
from models import Project, Table, User
from routes.data import data_bp
from routes.databases import databases_bp
from routes.pages import pages_bp

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def app(tmp_path):
    application = Flask(
        __name__,
        static_folder=str(REPO_ROOT / "static"),
        template_folder=str(REPO_ROOT / "templates"),
    )
    application.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI="sqlite://",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_FOLDER=str(tmp_path / "uploads"),
        DATASET_CACHE_DIR=str(tmp_path / "cache"),
        DATASET_STORAGE_BACKEND="local",
        PIPELINE_ENABLED=False,
        MAX_PROJECTS_PER_USER=2,
        MAX_TABLES_PER_PROJECT=5,
        MAX_CONTENT_LENGTH=5 * 1024 * 1024,
        AGENT_DAILY_REQUESTS_PER_USER=10,
    )
    Path(application.config["UPLOAD_FOLDER"]).mkdir()
    Path(application.config["DATASET_CACHE_DIR"]).mkdir()
    db.init_app(application)
    application.register_blueprint(data_bp)
    application.register_blueprint(databases_bp)
    application.register_blueprint(pages_bp)
    with application.app_context():
        db.create_all()
        user = User(email="sample@example.com")
        user.set_password("password")
        db.session.add(user)
        db.session.commit()
        application.config["_USER_ID"] = user.id
    return application


@pytest.fixture
def client(app):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = app.config["_USER_ID"]
    return client


def test_sample_dataset_creates_a_ready_to_use_database(app, client):
    response = client.post("/api/databases/sample")
    payload = response.get_json()

    assert response.status_code == 200, payload
    assert payload["success"] is True
    assert payload["database"]["name"] == "Sample: Coffee Shop"
    assert set(payload["schema"]) == {"customers", "orders", "products"}
    assert payload["schema"]["orders"]["row_count"] == 1200
    assert payload["schema"]["orders"]["types"]["order_total"] == "float"
    assert payload["warnings"] == []
    with client.session_transaction() as flask_session:
        assert flask_session["active_project_id"] == payload["database"]["id"]

    # A second click makes a second, distinctly named copy.
    second = client.post("/api/databases/sample").get_json()
    assert second["database"]["name"] == "Sample: Coffee Shop 2"

    # The account cap still applies.
    third = client.post("/api/databases/sample")
    assert third.status_code == 400
    assert "at most 2 databases" in third.get_json()["error"]
    with app.app_context():
        assert Project.query.count() == 2
        assert Table.query.count() == 6


def _upload(client, filename, body):
    return client.post(
        "/api/upload",
        data={"files": (io.BytesIO(body), filename)},
        content_type="multipart/form-data",
    )


def test_upload_reports_skipped_rows_and_delimiters(client):
    created = client.post("/api/databases", json={"name": "Warnings"}).get_json()
    client.post("/api/databases/select", json={"id": created["database"]["id"]})

    ragged = _upload(client, "ragged.csv", b"id,name,score\n1,Alice,90\n2,Bob,85,extra\n3,Carol\n")
    payload = ragged.get_json()
    assert ragged.status_code == 200
    assert payload["schema"]["ragged"]["row_count"] == 1
    assert any("2 rows skipped" in note for note in payload["warnings"])

    european = _upload(client, "sales_eu.csv", "Datum;Kunde;Betrag\n2025-01-03;Kunde 1;12.5\n".encode("latin-1"))
    payload = european.get_json()
    assert european.status_code == 200
    assert payload["schema"]["sales_eu"]["types"] == {"Datum": "str", "Kunde": "str", "Betrag": "float"}
    assert any("semicolons" in note for note in payload["warnings"])

    header_only = _upload(client, "empty.csv", b"id,name\n")
    assert any("no data rows" in note for note in header_only.get_json()["warnings"])


def test_guide_page_renders_server_limits(client):
    response = client.get("/guide")
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert "Getting started with AIStora" in body
    assert "5 MB" in body and "5</strong> tables" in body and "10</strong> per account per day" in body
    assert "/static/samples/coffee_shop/orders.csv" in body


def test_eda_time_summary_includes_period_series():
    from engine.dataframe import DataFrame
    from services.eda_service import generate_eda_report

    rows = [{"order_date": f"2025-{month:02d}-{day:02d}", "amount": month * day}
            for month in (1, 2, 3, 6) for day in (1, 15)] + [{"order_date": "2025-06-30", "amount": 1}]
    schema = {"orders": {"types": {"order_date": "str", "amount": "int"}, "row_count": len(rows)}}
    report = generate_eda_report(schema, lambda name: DataFrame(rows))
    table = report["tables"][0]
    summary = table["time_summary"][0]

    assert summary["granularity"] == "month"
    assert [point["period"] for point in summary["series"]] == ["2025-01", "2025-02", "2025-03", "2025-06"]
    assert summary["busiest_period"] == {"period": "2025-06", "rows": 3}
    assert summary["quietest_period"]["rows"] == 2
    assert summary["span_days"] == 180
