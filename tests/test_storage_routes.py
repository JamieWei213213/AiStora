import io
from pathlib import Path

from flask import Flask

import routes.data as data_routes
from extensions import db
from models import Project, Table, User
from routes.data import data_bp


class RecordingStorage:
    def __init__(self):
        self.uploads = []
        self.deleted = []

    def put_file(self, source_path, project_id, filename):
        self.uploads.append({
            "bytes": Path(source_path).read_bytes(),
            "project_id": project_id,
            "filename": filename,
        })
        return f"s3://private-bucket/datasets/{project_id}/object/{filename}"

    def delete(self, reference):
        self.deleted.append(reference)


def test_upload_persists_s3_reference_and_schema(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI="sqlite://",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_FOLDER=str(tmp_path / "uploads"),
        DATASET_CACHE_DIR=str(tmp_path / "cache"),
    )
    Path(app.config["UPLOAD_FOLDER"]).mkdir()
    Path(app.config["DATASET_CACHE_DIR"]).mkdir()
    db.init_app(app)
    app.register_blueprint(data_bp)
    storage = RecordingStorage()
    monkeypatch.setattr(data_routes, "get_dataset_storage", lambda: storage)

    with app.app_context():
        db.create_all()
        user = User(email="storage@example.com")
        user.set_password("password")
        db.session.add(user)
        db.session.flush()
        project = Project(name="Cloud", user_id=user.id)
        db.session.add(project)
        db.session.commit()
        user_id = user.id
        project_id = project.id

    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = user_id
        flask_session["active_project_id"] = project_id

    response = client.post(
        "/api/upload",
        data={
            "files": (
                io.BytesIO(b"region,amount\nWest,10\nEast,20\n"),
                "sales.csv",
            ),
        },
        content_type="multipart/form-data",
    )

    assert response.status_code == 200
    assert response.get_json()["schema"]["sales"]["row_count"] == 2
    assert storage.uploads[0]["project_id"] == project_id
    with app.app_context():
        table = Table.query.one()
        assert table.filepath.startswith("s3://private-bucket/datasets/")
        assert table.columns_schema == {"region": "str", "amount": "int"}
