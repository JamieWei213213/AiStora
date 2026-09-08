"""Guardrails against a client that spams the public deployment.

Each test reproduces the abuse first, then asserts the guardrail holds:
memory growth through limiter keys, a header that inflates every prompt,
a patient client that stays under the per-minute limit all day, and an
oversized upload answered with an HTML page the UI cannot parse.
"""

import io
import tempfile
from pathlib import Path

import pytest
from flask import Flask

import routes.auth as auth_routes
import routes.chat as chat_routes
import routes.data as data_routes
from app import create_app
from extensions import db
from models import Project, Table, User
from routes.auth import auth_bp
from routes.chat import chat_bp
from routes.data import data_bp
from routes.databases import databases_bp
from services.llm_service import AgentFunctionCall, AgentModelTurn
from services.rate_limit import (
    RedisSlidingWindowRateLimiter,
    SharedRateLimiter,
    SlidingWindowRateLimiter,
    normalise_key,
)
from services.request_router import route_request
from services.usage_budget import BudgetExceeded, DailyUsageBudget, usage_budget
from services.validation import ValidationError, validate_column_names


# --------------------------------------------------------------------------
# Rate limiter storage
# --------------------------------------------------------------------------

def test_limiter_hashes_huge_keys_instead_of_storing_them():
    limiter = SlidingWindowRateLimiter()
    huge = "a" * (5 * 1024 * 1024)
    limiter.check(huge, limit=10, window_seconds=60)

    stored = list(limiter._events)
    assert len(stored) == 1
    assert len(stored[0]) < 100
    assert normalise_key(huge) == stored[0]


def test_limiter_bounds_the_number_of_tracked_keys():
    limiter = SlidingWindowRateLimiter(max_keys=100)
    for index in range(1_000):
        limiter.check(f"client-{index}", limit=5, window_seconds=60, now=index)
    assert len(limiter._events) <= 100


def test_limiter_still_throttles_after_eviction():
    limiter = SlidingWindowRateLimiter(max_keys=3)
    for _ in range(3):
        limiter.check("attacker", limit=3, window_seconds=60, now=100)
    for index in range(10):
        limiter.check(f"other-{index}", limit=3, window_seconds=60, now=100)
    allowed, _ = limiter.check("attacker", limit=3, window_seconds=60, now=101)
    # The attacker's window may have been evicted (one free request), but a
    # new window is opened and the limit re-applies immediately after.
    for _ in range(3):
        limiter.check("attacker", limit=3, window_seconds=60, now=101)
    allowed, retry_after = limiter.check("attacker", limit=3, window_seconds=60, now=101)
    assert allowed is False
    assert retry_after >= 1


class FakeRedis:
    """Enough of redis-py for the sorted-set sliding window and counters."""

    def __init__(self):
        self.zsets = {}
        self.values = {}
        self.ttls = {}

    def pipeline(self):
        return FakePipeline(self)

    def zremrangebyscore(self, key, low, high):
        entries = self.zsets.setdefault(key, {})
        for member in [m for m, s in entries.items() if s <= float(high)]:
            del entries[member]

    def zadd(self, key, mapping):
        self.zsets.setdefault(key, {}).update(mapping)

    def zcard(self, key):
        return len(self.zsets.get(key, {}))

    def zrange(self, key, start, end, withscores=False):
        items = sorted(self.zsets.get(key, {}).items(), key=lambda kv: kv[1])
        return items[start:end + 1]

    def zrem(self, key, member):
        self.zsets.get(key, {}).pop(member, None)

    def expire(self, key, seconds):
        self.ttls[key] = seconds

    def incrby(self, key, amount):
        self.values[key] = self.values.get(key, 0) + amount
        return self.values[key]

    def get(self, key):
        return self.values.get(key)

    def scan(self, cursor=0, match=None, count=None):
        prefix = match.rstrip("*")
        keys = [k for k in list(self.zsets) + list(self.values) if k.startswith(prefix)]
        return 0, keys

    def delete(self, *keys):
        for key in keys:
            self.zsets.pop(key, None)
            self.values.pop(key, None)


class FakePipeline:
    def __init__(self, redis):
        self.redis = redis
        self.calls = []

    def __getattr__(self, name):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            return self
        return record

    def execute(self):
        return [getattr(self.redis, name)(*args, **kwargs) for name, args, kwargs in self.calls]


def test_redis_limiter_shares_the_window_across_processes():
    redis = FakeRedis()
    worker_a = RedisSlidingWindowRateLimiter(redis)
    worker_b = RedisSlidingWindowRateLimiter(redis)

    assert worker_a.check("ip:1", limit=2, window_seconds=60, now=1000)[0]
    assert worker_b.check("ip:1", limit=2, window_seconds=60, now=1001)[0]
    allowed, retry_after = worker_a.check("ip:1", limit=2, window_seconds=60, now=1002)
    assert allowed is False
    assert retry_after >= 1
    # A rejected attempt does not extend the window.
    assert redis.zcard("aistora:ratelimit:ip:1") == 2
    # The window slides.
    assert worker_b.check("ip:1", limit=2, window_seconds=60, now=1061)[0]


def test_shared_limiter_uses_redis_when_the_app_has_it():
    app = Flask(__name__)
    redis = FakeRedis()
    app.config["SESSION_REDIS"] = redis
    limiter = SharedRateLimiter("test")

    with app.app_context():
        limiter.check("k", limit=1, window_seconds=60)
        allowed, _ = limiter.check("k", limit=1, window_seconds=60)
    assert allowed is False
    assert any(key.startswith("aistora:ratelimit:test:") for key in redis.zsets)
    assert not limiter._fallback._events


# --------------------------------------------------------------------------
# Login endpoint: no unbounded key growth
# --------------------------------------------------------------------------

@pytest.fixture
def auth_client():
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI="sqlite://",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_TYPE="cachelib",
        SESSION_FILE_DIR=tempfile.mkdtemp(prefix="aistora-guard-"),
        IS_PRODUCTION=False,
    )
    db.init_app(app)
    from services.session_store import configure_sessions
    configure_sessions(app)
    app.register_blueprint(auth_bp)
    with app.app_context():
        db.create_all()
    auth_routes.auth_rate_limiter.clear()
    yield app.test_client()
    auth_routes.auth_rate_limiter.clear()


def test_login_does_not_retain_multi_megabyte_emails(auth_client):
    huge_email = "x" * (2 * 1024 * 1024) + "@example.com"
    response = auth_client.post("/api/login", json={"email": huge_email, "password": "p"})
    assert response.status_code == 401

    stored = auth_routes.auth_rate_limiter._fallback._events
    assert all(len(key) < 200 for key in stored), "raw email leaked into limiter keys"


# --------------------------------------------------------------------------
# Upload shape limits
# --------------------------------------------------------------------------

def test_validate_column_names_rejects_wide_and_long_headers():
    with pytest.raises(ValidationError):
        validate_column_names([f"c{i}" for i in range(201)], max_columns=200)
    with pytest.raises(ValidationError):
        validate_column_names(["a" * 65], max_name_chars=64)
    with pytest.raises(ValidationError):
        validate_column_names(["bad\x00name"])
    assert validate_column_names(["region", "amount"]) == ["region", "amount"]


def _upload_app(tmp_path, monkeypatch, **config):
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI="sqlite://",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        UPLOAD_FOLDER=str(tmp_path / "uploads"),
        DATASET_CACHE_DIR=str(tmp_path / "cache"),
        **config,
    )
    Path(app.config["UPLOAD_FOLDER"]).mkdir()
    Path(app.config["DATASET_CACHE_DIR"]).mkdir()
    db.init_app(app)
    app.register_blueprint(data_bp)
    app.register_blueprint(databases_bp)
    with app.app_context():
        db.create_all()
        user = User(email="guard@example.com")
        user.set_password("password")
        db.session.add(user)
        db.session.flush()
        project = Project(name="Guarded", user_id=user.id)
        db.session.add(project)
        db.session.commit()
        ids = (user.id, project.id)
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"], flask_session["active_project_id"] = ids
    return app, client


def _upload(client, body, name="wide.csv"):
    return client.post(
        "/api/upload",
        data={"files": (io.BytesIO(body), name)},
        content_type="multipart/form-data",
    )


def test_upload_rejects_thousands_of_columns(tmp_path, monkeypatch):
    app, client = _upload_app(tmp_path, monkeypatch, MAX_UPLOAD_COLUMNS=200)
    header = ",".join(f"col{i}" for i in range(2_000))
    response = _upload(client, f"{header}\n{','.join(['1'] * 2_000)}\n".encode())
    assert response.status_code == 400
    assert "200 columns" in response.get_json()["error"]
    with app.app_context():
        assert Table.query.count() == 0
    assert not list((tmp_path / "uploads").rglob("*.csv"))


def test_upload_rejects_absurd_column_names(tmp_path, monkeypatch):
    _, client = _upload_app(tmp_path, monkeypatch, MAX_COLUMN_NAME_CHARS=64)
    response = _upload(client, ("IGNORE " * 200 + ",amount\n1,2\n").encode())
    assert response.status_code == 400
    assert "64 characters" in response.get_json()["error"]


def test_upload_caps_tables_per_project(tmp_path, monkeypatch):
    _, client = _upload_app(tmp_path, monkeypatch, MAX_TABLES_PER_PROJECT=2)
    assert _upload(client, b"a,b\n1,2\n", "one.csv").status_code == 200
    assert _upload(client, b"a,b\n1,2\n", "two.csv").status_code == 200
    third = _upload(client, b"a,b\n1,2\n", "three.csv")
    assert third.status_code == 400
    assert third.get_json()["error_type"] == "input_limit"


def test_database_creation_is_capped_per_user(tmp_path, monkeypatch):
    _, client = _upload_app(tmp_path, monkeypatch, MAX_PROJECTS_PER_USER=2)
    assert client.post("/api/databases", json={"name": "Second"}).status_code == 200
    third = client.post("/api/databases", json={"name": "Third"})
    assert third.status_code == 400
    assert "at most 2" in third.get_json()["error"]


def test_oversized_request_returns_json_not_html(monkeypatch):
    monkeypatch.setenv("APP_ENV", "development")
    app = create_app()
    app.config["MAX_CONTENT_LENGTH"] = 1024
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = 1
        flask_session["active_project_id"] = 1
    response = _upload(client, b"a,b\n" + b"1,2\n" * 2_000)
    assert response.status_code == 413
    assert response.is_json
    assert response.get_json()["error_type"] == "input_limit"


# --------------------------------------------------------------------------
# Daily budgets
# --------------------------------------------------------------------------

def test_daily_budget_limits_requests_tokens_and_global_spend(monkeypatch):
    from config import Config

    budget = DailyUsageBudget()
    monkeypatch.setattr(Config, "AGENT_DAILY_REQUESTS_PER_USER", 2)
    monkeypatch.setattr(Config, "AGENT_DAILY_TOKENS_PER_USER", 1_000)
    monkeypatch.setattr(Config, "AGENT_DAILY_TOKENS_GLOBAL", 1_500)

    budget.reserve_request("u1")
    budget.reserve_request("u1")
    with pytest.raises(BudgetExceeded) as excinfo:
        budget.reserve_request("u1")
    assert excinfo.value.scope == "user_requests"
    assert excinfo.value.retry_after >= 1

    budget.record_tokens("u2", 1_000)
    with pytest.raises(BudgetExceeded) as excinfo:
        budget.reserve_request("u2")
    assert excinfo.value.scope == "user_tokens"

    # A third account does not get around the global ceiling.
    budget.record_tokens("u3", 600)
    with pytest.raises(BudgetExceeded) as excinfo:
        budget.reserve_request("u4")
    assert excinfo.value.scope == "global_tokens"


def test_daily_budget_uses_redis_when_available(monkeypatch):
    from config import Config

    monkeypatch.setattr(Config, "AGENT_DAILY_REQUESTS_PER_USER", 1)
    app = Flask(__name__)
    redis = FakeRedis()
    app.config["SESSION_REDIS"] = redis
    budget = DailyUsageBudget()
    with app.app_context():
        budget.reserve_request("u1")
        budget.record_tokens("u1", 42)
        with pytest.raises(BudgetExceeded):
            budget.reserve_request("u1")
    assert any(key.endswith(":requests:user:u1") for key in redis.values)
    assert any(key.endswith(":tokens:global") for key in redis.values)


class CountingModel:
    """Answers in one turn and reports token usage like the real session."""

    def start_agent(self, system_prompt, declarations):
        class Session:
            usage = {"input_tokens": 300, "output_tokens": 50, "total_tokens": 350}
            retry_count = 0

            def send(self, message):
                return AgentModelTurn("There are two rows.", [])

        return Session()


def _chat_app(monkeypatch):
    app = Flask(__name__)
    app.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI="sqlite://",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
    )
    db.init_app(app)
    app.register_blueprint(chat_bp)
    monkeypatch.setattr(chat_routes, "get_model", lambda tier="standard": CountingModel())
    with app.app_context():
        db.create_all()
        user = User(id=1, email="budget@example.com")
        user.set_password("password")
        db.session.add(user)
        db.session.add(Project(id=9, name="Chat", user_id=1))
        db.session.add(Table(
            id=1, name="sales", filename="sales.csv", filepath="sales.csv",
            columns_schema={"amount": "float", "customer_id": "int"}, row_count=2, project_id=9,
        ))
        db.session.add(Table(
            id=2, name="customers", filename="customers.csv", filepath="customers.csv",
            columns_schema={"id": "int", "region": "str"}, row_count=2, project_id=9,
        ))
        db.session.commit()
    client = app.test_client()
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = 1
        flask_session["active_project_id"] = 9
    return client


def test_chat_stops_after_the_daily_token_budget(monkeypatch):
    from config import Config

    usage_budget.clear()
    chat_routes.agent_rate_limiter.clear()
    monkeypatch.setattr(Config, "AGENT_DAILY_REQUESTS_PER_USER", 0)
    monkeypatch.setattr(Config, "AGENT_DAILY_TOKENS_PER_USER", 500)
    monkeypatch.setattr(Config, "AGENT_DAILY_TOKENS_GLOBAL", 0)
    client = _chat_app(monkeypatch)

    first = client.post("/api/chat", json={"query": "how many rows"})
    assert first.status_code == 200, first.get_json()
    assert usage_budget.snapshot(1)["tokens_today"] == 350

    second = client.post("/api/chat", json={"query": "how many rows"})
    assert second.status_code == 200  # 350 < 500, still allowed
    third = client.post("/api/chat", json={"query": "how many rows"})
    assert third.status_code == 429
    body = third.get_json()
    assert body["error_type"] == "daily_budget"
    assert body["budget_scope"] == "user_tokens"
    assert int(third.headers["Retry-After"]) >= 1
    usage_budget.clear()


def test_relationship_detection_counts_against_the_daily_budget(monkeypatch):
    from config import Config

    usage_budget.clear()
    chat_routes.agent_rate_limiter.clear()
    monkeypatch.setattr(Config, "AGENT_DAILY_REQUESTS_PER_USER", 1)
    monkeypatch.setattr(Config, "AGENT_DAILY_TOKENS_PER_USER", 0)
    monkeypatch.setattr(Config, "AGENT_DAILY_TOKENS_GLOBAL", 0)
    client = _chat_app(monkeypatch)
    client.post("/api/chat", json={"query": "how many rows"})
    response = client.post("/api/detect-relationships", json={})
    assert response.status_code == 429
    assert response.get_json()["error_type"] == "daily_budget"
    usage_budget.clear()


# --------------------------------------------------------------------------
# Router: cheap questions stay on the cheap model
# --------------------------------------------------------------------------

def test_simple_count_questions_stay_on_the_standard_tier():
    schema = {"sales": {"types": {"amount": "float"}}, "customers": {"types": {"id": "int"}}}
    for query in (
        "How many rows are in sales?",
        "What is the number of records in customers?",
        "show sales over 100",
    ):
        decision = route_request(query, schema)
        assert decision.model_tier == "standard", query
    assert route_request("sales over time", schema).model_tier == "advanced"
    assert route_request("compare sales and customers", schema).model_tier == "advanced"
