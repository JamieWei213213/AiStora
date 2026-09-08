"""Regression test for the 4 KB session cookie overflow.

Every piece of session state used to live in Flask's signed client-side cookie:
the project schema, detected relationships, per-project agent memory, pending
approvals and cleaning previews. Browsers cap a cookie at roughly 4093 bytes.
Measured against the real serializer, six tables of twenty-five columns plus a
full agent memory produced 6.6 KB and the application's own configured ceiling
(twenty tables, sixty columns) produced 11.4 KB. Over the limit the browser
silently discards the cookie and the user is logged out mid-session.
"""

import tempfile

from flask import Flask, session

from extensions import db
from models import Project, Table, User
from routes.databases import databases_bp
from services.session_store import configure_sessions


COOKIE_LIMIT_BYTES = 4093


def make_app():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI="sqlite://",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_TYPE="cachelib",
        SESSION_FILE_DIR=tempfile.mkdtemp(prefix="aistora-test-sessions-"),
        IS_PRODUCTION=False,
    )
    db.init_app(app)
    configure_sessions(app)
    app.register_blueprint(databases_bp)

    @app.get("/_probe")
    def probe():
        # Simulate the heaviest legitimate session payload.
        session["agent_memories"] = {
            "1": [
                {"user": "u" * 300, "assistant": "a" * 300}
                for _ in range(8)
            ]
        }
        session["db_relationships"] = [
            {
                "from_table": f"table_{i}", "from_column": "id",
                "to_table": f"table_{i + 1}", "to_column": "fk",
            }
            for i in range(20)
        ]
        return "ok"

    with app.app_context():
        db.create_all()
        user = User(id=1, email="size@example.com")
        user.set_password("password")
        db.session.add(user)
        db.session.add(Project(id=1, name="Big", user_id=1))
        # The application's own configured ceiling: EDA_MAX_TABLES=20,
        # EDA_MAX_COLUMNS_PER_TABLE=60.
        for table_index in range(20):
            db.session.add(Table(
                id=table_index + 1,
                name=f"table_{table_index}",
                filename=f"export_{table_index}.csv",
                filepath=f"export_{table_index}.csv",
                columns_schema={
                    f"column_{c}_descriptive_name": "float" for c in range(60)
                },
                row_count=123456,
                project_id=1,
            ))
        db.session.commit()
    return app


def _cookie_bytes(response):
    return sum(
        len(value)
        for header, value in response.headers
        if header.lower() == "set-cookie"
    )


def test_selecting_a_large_project_keeps_the_cookie_small():
    app = make_app()
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = 1

    response = client.post("/api/databases/select", json={"id": 1})

    assert response.status_code == 200
    # The schema is still returned to the browser in the response body...
    assert len(response.get_json()["schema"]) == 20
    # ...but it is not carried in the cookie.
    assert _cookie_bytes(response) < COOKIE_LIMIT_BYTES


def test_full_agent_memory_and_relationships_stay_under_the_cookie_limit():
    app = make_app()
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = 1

    client.post("/api/databases/select", json={"id": 1})
    response = client.get("/_probe")

    assert response.status_code == 200
    assert _cookie_bytes(response) < COOKIE_LIMIT_BYTES


def test_schema_is_not_written_into_the_session():
    app = make_app()
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = 1

    client.post("/api/databases/select", json={"id": 1})

    with client.session_transaction() as flask_session:
        assert "db_schema" not in flask_session
        assert flask_session["active_project_id"] == 1


def test_switching_projects_clears_the_previous_projects_pending_state():
    app = make_app()
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = 1
        flask_session["pending_agent"] = {"type": "approval", "goal": "old"}
        flask_session["cleaning_previews"] = {"token": {"table_id": 99}}

    client.post("/api/databases/select", json={"id": 1})

    with client.session_transaction() as flask_session:
        assert "pending_agent" not in flask_session
        assert "cleaning_previews" not in flask_session
