from flask import Flask

import routes.eda as eda_routes
from engine.dataframe import DataFrame
from extensions import db
from models import Project, Table, User
from routes.eda import eda_bp


def make_app(monkeypatch):
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI="sqlite://",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    app.register_blueprint(eda_bp)
    with app.app_context():
        db.create_all()
        user = User(id=1, email="eda@example.com")
        user.set_password("password")
        db.session.add(user)
        db.session.add(Project(id=9, name="EDA", user_id=1))
        db.session.add(Table(
            id=1,
            name="sales",
            filename="sales.csv",
            filepath="sales.csv",
            columns_schema={"amount": "int", "email": "str"},
            row_count=2,
            project_id=9,
        ))
        db.session.commit()
    monkeypatch.setattr(
        eda_routes,
        "get_dataframe",
        lambda table: DataFrame([
            {"amount": 10, "email": "one@example.com"},
            {"amount": 20, "email": "two@example.com"},
        ]),
    )
    eda_routes.eda_rate_limiter.clear()
    return app


def authenticate(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["active_project_id"] = 9


def test_eda_route_requires_authentication(monkeypatch):
    response = make_app(monkeypatch).test_client().post("/api/eda-report")
    assert response.status_code == 401


def test_eda_route_returns_local_privacy_limited_report(monkeypatch):
    app = make_app(monkeypatch)
    client = app.test_client()
    authenticate(client)

    response = client.post("/api/eda-report")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["success"] is True
    assert payload["report"]["overview"]["scanned_rows"] == 2
    assert payload["report"]["privacy"]["execution"] == "local_only"
    email = payload["report"]["tables"][0]["categorical_summary"][0]
    assert email["values_redacted"] is True
    assert email["top_values"] == []


def test_eda_route_hides_internal_errors(monkeypatch):
    app = make_app(monkeypatch)
    client = app.test_client()
    authenticate(client)
    monkeypatch.setattr(
        eda_routes,
        "generate_eda_report",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret path")),
    )

    response = client.post("/api/eda-report")
    payload = response.get_json()

    assert response.status_code == 500
    assert payload["error_type"] == "internal_error"
    assert "secret path" not in payload["error"]
