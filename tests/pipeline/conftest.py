import io
import os
from pathlib import Path

import pytest
from flask import Flask

from extensions import db
from models import Project, User
from pipeline import events as pipeline_events
from pipeline.config import PipelineSettings
from pipeline.objectstore import LocalObjectStore
from pipeline.stages import StageContext
from routes.connectors import connectors_bp
from routes.data import data_bp
from routes.loads import loads_bp
from routes.tables import tables_bp
from routes.chat import chat_bp


@pytest.fixture()
def settings(tmp_path) -> PipelineSettings:
    return PipelineSettings(
        backend="local",
        lake_root=str(tmp_path / "lake"),
        scratch_dir=str(tmp_path / "scratch"),
        events_enabled=True,
        events_flush_rows=10_000,
        events_flush_seconds=3600,
    ).validate()


@pytest.fixture()
def store(settings) -> LocalObjectStore:
    return LocalObjectStore(settings.lake_root)


@pytest.fixture()
def ctx(settings, store) -> StageContext:
    sink = pipeline_events.EventSink(settings, store)
    pipeline_events.configure_sink(sink)
    yield StageContext(settings=settings, store=store)
    pipeline_events.configure_sink(None)


def write_csv(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


@pytest.fixture()
def app(tmp_path, settings):
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI="sqlite://",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_FOLDER=str(tmp_path / "uploads"),
        DATASET_CACHE_DIR=str(tmp_path / "cache"),
        DATASET_STORAGE_BACKEND="local",
        PIPELINE_ENABLED=True,
        PIPELINE_BACKEND="local",
        LAKE_ROOT=settings.lake_root,
        PIPELINE_SYNC_WAIT_SECONDS=30,
        MAX_UPLOAD_COLUMNS=200,
        MAX_COLUMN_NAME_CHARS=64,
        MAX_TABLES_PER_PROJECT=20,
        AGENT_RATE_LIMIT_PER_MINUTE=100,
    )
    os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)
    os.makedirs(app.config["DATASET_CACHE_DIR"], exist_ok=True)
    os.environ["PIPELINE_SCRATCH_DIR"] = settings.scratch_dir
    from routes.loads import loads_rate_limiter
    from routes.connectors import connector_rate_limiter

    loads_rate_limiter.clear()
    connector_rate_limiter.clear()
    db.init_app(app)
    for blueprint in (data_bp, loads_bp, connectors_bp, tables_bp, chat_bp):
        app.register_blueprint(blueprint)
    with app.app_context():
        db.create_all()
        user = User(email="loads@example.com")
        user.set_password("password-123")
        db.session.add(user)
        db.session.flush()
        project = Project(name="Books", user_id=user.id)
        db.session.add(project)
        db.session.commit()
        app.config["_USER_ID"] = user.id
        app.config["_PROJECT_ID"] = project.id
    yield app
    os.environ.pop("PIPELINE_SCRATCH_DIR", None)


@pytest.fixture()
def client(app):
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = app.config["_USER_ID"]
        flask_session["active_project_id"] = app.config["_PROJECT_ID"]
    return client


def upload(client, text: str, filename: str = "sales.csv", **form):
    data = {"files": (io.BytesIO(text.encode("utf-8")), filename)}
    data.update({k: str(v) for k, v in form.items()})
    return client.post("/api/loads", data=data, content_type="multipart/form-data")
