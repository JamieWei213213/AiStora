import csv

from flask import Flask

import routes.tables as table_routes
from extensions import db
from models import Project, Table, User
from routes.tables import tables_bp


class NoOpAudit:
    def record(self, *args):
        pass


def build_app(tmp_path, monkeypatch):
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI="sqlite://",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_FOLDER=str(tmp_path),
        CLEANING_MAX_ROWS=1000,
    )
    db.init_app(app)
    app.register_blueprint(tables_bp)
    monkeypatch.setattr(table_routes, "AgentAuditLogger", lambda: NoOpAudit())
    with app.app_context():
        db.create_all()
        user = User(email="clean@example.com")
        user.set_password("password")
        db.session.add(user)
        db.session.flush()
        project = Project(name="Cleaning", user_id=user.id)
        db.session.add(project)
        db.session.flush()

        source = tmp_path / "dirty.csv"
        with source.open("w", encoding="utf-8", newline="") as output:
            writer = csv.writer(output)
            writer.writerow([" Name ", "Amount"])
            writer.writerow([" Alice ", "10"])
            writer.writerow([" Alice ", "10"])

        table = Table(
            name="dirty",
            filename="dirty.csv",
            filepath=str(source),
            columns_schema={"Name": "str", "Amount": "int"},
            row_count=2,
            project_id=project.id,
        )
        db.session.add(table)
        db.session.commit()
        return app, user.id, project.id, table.id, source


def authenticate(client, user_id, project_id):
    with client.session_transaction() as session:
        session["user_id"] = user_id
        session["active_project_id"] = project_id


def test_cleaning_requires_preview_approval_and_creates_copy(tmp_path, monkeypatch):
    app, user_id, project_id, table_id, source = build_app(tmp_path, monkeypatch)
    client = app.test_client()
    authenticate(client, user_id, project_id)

    denied = client.post(
        f"/api/tables/{table_id}/clean/apply",
        json={"approved": False},
    )
    assert denied.status_code == 400

    preview = client.post(f"/api/tables/{table_id}/clean/preview").get_json()
    assert preview["requires_cleaning"] is True

    applied = client.post(
        f"/api/tables/{table_id}/clean/apply",
        json={
            "approved": True,
            "approval_token": preview["approval_token"],
        },
    )
    result = applied.get_json()

    assert applied.status_code == 200
    assert result["cleaned_table"] == "dirty_cleaned"
    assert result["input_rows"] == 2
    assert result["output_rows"] == 1
    assert source.exists()
    with app.app_context():
        assert Table.query.filter_by(project_id=project_id).count() == 2
