import uuid

from flask import Flask

import routes.chat as chat_routes
import services.agent_tools as agent_tools
from engine.dataframe import DataFrame
from extensions import db
from models import Project, User
from routes.chat import chat_bp
from services.llm_service import AgentFunctionCall, AgentModelTurn


class TextSession:
    def send(self, message):
        return AgentModelTurn("I can help with that.", [])


class FakeModel:
    def start_agent(self, system_prompt, declarations):
        return TextSession()


def make_app(monkeypatch, model=None):
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI="sqlite://",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    app.register_blueprint(chat_bp)
    monkeypatch.setattr(
        chat_routes,
        "get_model",
        lambda tier="standard": model or FakeModel(),
    )
    with app.app_context():
        db.create_all()
        user = User(id=1, email="chat@example.com")
        user.set_password("password")
        db.session.add(user)
        db.session.add(Project(id=9, name="Chat", user_id=1))
        db.session.commit()
    return app


def authenticate(client):
    with client.session_transaction() as session:
        session["user_id"] = 1
        session["active_project_id"] = 9
        session["db_schema"] = {
            "sales": {"types": {"amount": "float"}, "row_count": 2}
        }


def test_chat_route_runs_agent_and_saves_scoped_memory(monkeypatch):
    app = make_app(monkeypatch)
    client = app.test_client()
    authenticate(client)

    response = client.post("/api/chat", json={
        "query": "hello",
        "request_id": str(uuid.uuid4()),
    })

    assert response.status_code == 200
    assert response.get_json()["type"] == "text"
    assert response.get_json()["verification"]["passed"] is True
    with client.session_transaction() as session:
        assert session["agent_memories"]["9"][0]["user"] == "hello"


def test_memory_and_cancel_routes_validate_requests(monkeypatch):
    app = make_app(monkeypatch)
    client = app.test_client()
    authenticate(client)

    assert client.delete("/api/chat/memory").get_json()["success"] is True
    invalid = client.post("/api/chat/cancel", json={"request_id": "../bad"})
    assert invalid.status_code == 400


def test_column_based_suggestions_route(monkeypatch):
    app = make_app(monkeypatch)
    client = app.test_client()
    authenticate(client)

    response = client.get("/api/chat/suggestions")
    data = response.get_json()

    assert response.status_code == 200
    assert data["success"] is True
    assert any("amount" in item["question"] for item in data["suggestions"])


def test_chat_requires_authentication(monkeypatch):
    app = make_app(monkeypatch)
    response = app.test_client().post("/api/chat", json={"query": "hello"})
    assert response.status_code == 401


def test_chat_route_returns_named_tool_result_and_trace(monkeypatch):
    class ToolSession:
        def __init__(self):
            self.turn = 0

        def send(self, message):
            return AgentModelTurn("", [AgentFunctionCall("top_rows", {
                "source": "sales",
                "sort_column": "amount",
                "limit": 2,
                "descending": True,
                "save_as": "top_sales",
            })])

        def send_tool_results(self, results):
            self.turn += 1
            return AgentModelTurn("", [AgentFunctionCall("finish", {
                "source": "top_sales",
                "message": "Top sales are ready.",
            })])

    class ToolModel:
        def start_agent(self, system_prompt, declarations):
            return ToolSession()

    class NoOpAudit:
        def record(self, *args):
            pass

    monkeypatch.setattr(
        agent_tools, "AgentAuditLogger", lambda: NoOpAudit()
    )
    monkeypatch.setattr(
        chat_routes,
        "get_dataframe",
        lambda _: DataFrame([{"amount": 10}, {"amount": 20}]),
    )
    app = make_app(monkeypatch, ToolModel())
    client = app.test_client()
    authenticate(client)

    response = client.post("/api/chat", json={
        "query": "show top sales",
        "request_id": str(uuid.uuid4()),
    })
    data = response.get_json()

    assert response.status_code == 200
    assert data["type"] == "table"
    assert data["result_name"] == "top_sales"
    assert data["data"][0]["amount"] == 20
    assert data["verification"]["passed"] is True
    assert data["agent"]["routing_tier"] == "standard"
    assert [item["tool"] for item in data["trace"]] == [
        "top_rows",
        "finish",
        "verify_result",
    ]

    feedback = client.post("/api/chat/feedback", json={
        "request_id": data["request_id"],
        "rating": "up",
    })
    assert feedback.status_code == 200
    metrics = client.get("/api/chat/metrics").get_json()["metrics"]
    assert metrics["total_runs"] == 1
    assert metrics["positive_feedback_rate"] == 1.0
    cleared = client.delete("/api/chat/history")
    assert cleared.get_json() == {"success": True, "deleted": 1}
    assert client.get("/api/chat/metrics").get_json()["metrics"]["total_runs"] == 0


def test_chat_route_returns_friendly_quota_response_with_completed_plan(monkeypatch):
    class QuotaSession:
        def send(self, message):
            return AgentModelTurn("", [AgentFunctionCall("record_plan", {
                "steps": ["Group flights by carrier", "Return total air time"],
            })])

        def send_tool_results(self, results):
            raise RuntimeError(
                "429 RESOURCE_EXHAUSTED: You exceeded your current quota"
            )

    class QuotaModel:
        def start_agent(self, system_prompt, declarations):
            return QuotaSession()

    class NoOpAudit:
        def record(self, *args):
            pass

    monkeypatch.setattr(agent_tools, "AgentAuditLogger", lambda: NoOpAudit())
    app = make_app(monkeypatch, QuotaModel())
    client = app.test_client()
    authenticate(client)

    response = client.post("/api/chat", json={
        "query": "total air time by carrier",
        "request_id": str(uuid.uuid4()),
    })
    data = response.get_json()

    assert response.status_code == 429
    assert data["type"] == "quota"
    assert "key and model are working" in data["data"]
    assert "RESOURCE_EXHAUSTED" not in data["data"]
    assert data["plan"] == [
        "Group flights by carrier",
        "Return total air time",
    ]
