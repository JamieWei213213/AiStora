"""Tests for the credential endpoints.

routes/auth.py was the least covered module in the project at 32% despite
being the only thing standing between an anonymous request and someone's
financial data.
"""

import tempfile

import pytest
from flask import Flask

import routes.auth as auth_routes
from extensions import db
from models import User
from routes.auth import auth_bp
from services.session_store import configure_sessions


GOOD_PASSWORD = "correct-horse-battery"


@pytest.fixture
def app():
    application = Flask(__name__)
    application.config.update(
        TESTING=True,
        SECRET_KEY="test",
        SQLALCHEMY_DATABASE_URI="sqlite://",
        SQLALCHEMY_TRACK_MODIFICATIONS=False,
        SESSION_TYPE="cachelib",
        SESSION_FILE_DIR=tempfile.mkdtemp(prefix="aistora-auth-test-"),
        IS_PRODUCTION=False,
    )
    db.init_app(application)
    configure_sessions(application)
    application.register_blueprint(auth_bp)
    with application.app_context():
        db.create_all()
    auth_routes.auth_rate_limiter.clear()
    return application


@pytest.fixture
def client(app):
    return app.test_client()


def register(client, email="user@example.com", password=GOOD_PASSWORD):
    return client.post("/api/register", json={"email": email, "password": password})


def test_register_then_login_then_logout(client):
    assert register(client).status_code == 200

    response = client.post("/api/login", json={
        "email": "user@example.com", "password": GOOD_PASSWORD,
    })
    assert response.status_code == 200
    assert response.get_json()["email"] == "user@example.com"

    assert client.get("/api/auth/status").get_json()["isLoggedIn"] is True
    assert client.post("/api/logout").status_code == 200
    assert client.get("/api/auth/status").get_json()["isLoggedIn"] is False


def test_email_is_normalised_so_case_does_not_create_two_accounts(client):
    assert register(client, email="User@Example.com").status_code == 200
    assert register(client, email="user@example.com").status_code == 409

    response = client.post("/api/login", json={
        "email": "USER@EXAMPLE.COM", "password": GOOD_PASSWORD,
    })
    assert response.status_code == 200


@pytest.mark.parametrize("email", ["notanemail", "no@domain", "@example.com", ""])
def test_malformed_emails_are_rejected(client, email):
    assert register(client, email=email).status_code == 400


@pytest.mark.parametrize("password", ["short", "", "         "])
def test_weak_passwords_are_rejected(client, password):
    assert register(client, password=password).status_code == 400


def test_login_does_not_reveal_whether_an_account_exists(client):
    register(client)

    missing = client.post("/api/login", json={
        "email": "nobody@example.com", "password": GOOD_PASSWORD,
    })
    wrong = client.post("/api/login", json={
        "email": "user@example.com", "password": "wrong-password-here",
    })

    assert missing.status_code == wrong.status_code == 401
    assert missing.get_json()["error"] == wrong.get_json()["error"]


def test_login_is_rate_limited_per_account(client, app):
    register(client)
    app.config["LOGIN_ATTEMPTS_PER_MINUTE"] = 3

    for _ in range(3):
        assert client.post("/api/login", json={
            "email": "user@example.com", "password": "wrong",
        }).status_code == 401

    blocked = client.post("/api/login", json={
        "email": "user@example.com", "password": "wrong",
    })
    assert blocked.status_code == 429
    assert blocked.headers["Retry-After"]


def test_registration_is_rate_limited(client, app):
    app.config["REGISTRATIONS_PER_HOUR"] = 2
    assert register(client, email="a@example.com").status_code == 200
    assert register(client, email="b@example.com").status_code == 200
    assert register(client, email="c@example.com").status_code == 429


def test_session_is_reset_on_login_so_a_fixed_session_cannot_be_reused(client):
    register(client)
    with client.session_transaction() as flask_session:
        flask_session["planted"] = "attacker-value"

    client.post("/api/login", json={
        "email": "user@example.com", "password": GOOD_PASSWORD,
    })

    with client.session_transaction() as flask_session:
        assert "planted" not in flask_session
        assert flask_session["user_id"]


def test_status_recovers_when_the_account_no_longer_exists(client, app):
    register(client)
    client.post("/api/login", json={
        "email": "user@example.com", "password": GOOD_PASSWORD,
    })
    with app.app_context():
        db.session.delete(User.query.filter_by(email="user@example.com").one())
        db.session.commit()

    # Previously returned isLoggedIn: True with a blank email.
    assert client.get("/api/auth/status").get_json()["isLoggedIn"] is False
