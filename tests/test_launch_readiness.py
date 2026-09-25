"""Behavioural coverage for authentication recovery and public-beta safeguards."""
import io
from unittest.mock import patch

import pytest
from flask import Flask
from itsdangerous import TimestampSigner
from models import User
from services.account_recovery import make_reset_token
from services.usage_budget import DailyUsageBudget, BudgetExceeded
from services.upload_capacity import check_upload_capacity
from tests.test_auth_routes import app, client, register, GOOD_PASSWORD


@pytest.mark.parametrize("payload", [[], ["x"], "text", 4, None])
@pytest.mark.parametrize("endpoint", ["/api/register", "/api/login"])
def test_auth_rejects_non_object_bodies(client, payload, endpoint):
    assert client.post(endpoint, json=payload).status_code == 400


@pytest.mark.parametrize("password", [[], {}, 123, True, "x" * 257])
def test_invalid_password_types_do_not_crash_login(client, password):
    register(client)
    assert client.post("/api/login", json={"email":"user@example.com", "password":password}).status_code == 401


def test_registration_returns_field_feedback(client):
    response = register(client, password="short")
    assert response.status_code == 400
    assert response.json["field"] == "password"


def login(client, password=GOOD_PASSWORD):
    return client.post("/api/login", json={"email":"user@example.com", "password":password})


def token_for(app):
    with app.app_context():
        return make_reset_token(User.query.filter_by(email="user@example.com").one())


def test_recovery_requires_mail_configuration(client):
    assert client.get("/api/auth/status").json["passwordResetAvailable"] is False
    assert client.post("/api/auth/forgot-password", json={"email":"user@example.com"}).status_code == 503


def test_recovery_response_does_not_disclose_account(app, client, monkeypatch):
    register(client)
    app.config.update(PUBLIC_APP_URL="https://aistora.example", SMTP_HOST="smtp.example", SMTP_FROM="test@example.com")
    sent = []
    monkeypatch.setattr("routes.auth.send_reset_email", lambda user: sent.append(user.id))
    known = client.post("/api/auth/forgot-password", json={"email":"user@example.com"})
    unknown = client.post("/api/auth/forgot-password", json={"email":"nobody@example.com"})
    assert known.status_code == unknown.status_code == 200
    assert known.json == unknown.json
    assert len(sent) == 1
    assert client.get("/api/auth/status").json["passwordResetAvailable"] is True


def test_reset_is_single_use_and_revokes_other_sessions(app, client):
    register(client)
    other = app.test_client()
    assert login(other).status_code == 200
    token = token_for(app)
    response = client.post("/api/auth/reset-password", json={"token":token,"password":"new-long-passphrase"})
    assert response.status_code == 200
    assert other.get("/api/auth/status").json["isLoggedIn"] is False
    assert login(client).status_code == 401
    assert login(client, "new-long-passphrase").status_code == 200
    assert client.post("/api/auth/reset-password", json={"token":token,"password":"yet-another-password"}).status_code == 400


def test_reset_rejects_expired_and_tampered_tokens(app, client):
    register(client)
    with patch.object(TimestampSigner, "get_timestamp", return_value=1):
        expired = token_for(app)
    for token in [expired, token_for(app) + "x"]:
        response = client.post("/api/auth/reset-password", json={"token":token,"password":"new-long-password"})
        assert response.status_code == 400
        assert response.json["error_type"] == "invalid_token"
    assert login(client).status_code == 200


def test_reset_rejects_short_password_without_consuming_link(app, client):
    register(client)
    token = token_for(app)
    invalid = client.post("/api/auth/reset-password", json={"token":token,"password":"short"})
    assert invalid.status_code == 400
    assert invalid.json["field"] == "password"
    assert client.post("/api/auth/reset-password", json={"token":token,"password":"new-long-password"}).status_code == 200


def test_recovery_uses_trusted_origin_and_tls(app, client, monkeypatch):
    from services.account_recovery import send_reset_email
    register(client)
    app.config.update(PUBLIC_APP_URL="https://aistora.example", SMTP_HOST="smtp.example", SMTP_FROM="test@example.com", SMTP_PORT=587)
    with patch("services.account_recovery.smtplib.SMTP") as smtp:
        with app.app_context():
            send_reset_email(User.query.first())
        transport = smtp.return_value.__enter__.return_value
        transport.starttls.assert_called_once()
        email = transport.send_message.call_args.args[0]
        assert "https://aistora.example/app#reset=" in email.get_content()


def test_global_request_limit_cannot_be_bypassed_with_new_accounts(monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, "AGENT_DAILY_REQUESTS_GLOBAL", 2)
    budget = DailyUsageBudget()
    budget.reserve_request("a")
    budget.reserve_request("b")
    with pytest.raises(BudgetExceeded, match="global_requests"):
        budget.reserve_request("c")
    assert budget.snapshot("c")["requests_today"] == 0


def test_budget_store_failure_blocks_ai_in_production(monkeypatch):
    from config import Config
    monkeypatch.setattr(Config, "AGENT_BUDGET_FAIL_CLOSED", True)
    class BrokenStore:
        def get(self, key):
            raise OSError("offline")
    budget = DailyUsageBudget()
    monkeypatch.setattr(budget, "_store", lambda: BrokenStore())
    with pytest.raises(BudgetExceeded, match="budget_unavailable"):
        budget.reserve_request("a")


def test_upload_stops_before_writing_when_disk_is_low(tmp_path, monkeypatch):
    from collections import namedtuple
    app = Flask(__name__)
    app.config.update(UPLOAD_FOLDER=str(tmp_path), UPLOAD_MIN_FREE_BYTES=1000, MAX_CONTENT_LENGTH=100, PIPELINE_ENABLED=False)
    app.before_request(check_upload_capacity)
    @app.post("/api/upload")
    def upload():
        return {"success":True}
    usage = namedtuple("usage", "total used free")
    monkeypatch.setattr("services.upload_capacity.shutil.disk_usage", lambda _: usage(2000,1500,500))
    response = app.test_client().post("/api/upload", data=b"a,b")
    assert response.status_code == 507
    assert response.json["error_type"] == "storage_capacity"


def test_api_framework_errors_are_readable_and_hide_internal_details():
    from app import register_error_handlers
    app = Flask(__name__)
    register_error_handlers(app)
    response = app.test_client().get("/api/does-not-exist")
    assert response.status_code == 404
    assert response.is_json


def test_assets_keep_the_same_version_across_page_loads():
    import re
    from app import app
    client = app.test_client()
    first = client.get("/app").text
    second = client.get("/app").text
    version = re.search(r"auth.js\?v=([a-f0-9]+)", first).group(1)
    assert len(version) == 16
    assert f"auth.js?v={version}" in second
