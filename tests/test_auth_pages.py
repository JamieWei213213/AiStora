"""Sign-in and sign-up are separate pages served by the pages blueprint."""
import pytest
from flask import Flask

from routes.pages import pages_bp


@pytest.fixture
def client():
    application = Flask(__name__, template_folder="../templates", static_folder="../static")
    application.config.update(TESTING=True, PIPELINE_ENABLED=False)
    application.register_blueprint(pages_bp)
    return application.test_client()


@pytest.mark.parametrize("path, title", [
    ("/login", "Sign in | AIStora"),
    ("/signup", "Create your account | AIStora"),
])
def test_auth_pages_render_with_their_own_title(client, path, title):
    response = client.get(path)
    body = response.get_data(as_text=True)
    assert response.status_code == 200
    assert f"<title>{title}</title>" in body
    assert 'id="auth-screen"' in body


def test_signin_page_links_to_signup_page(client):
    body = client.get("/login").get_data(as_text=True)
    assert '<a id="auth-toggle-mode" href="/signup"' in body


def test_register_alias_redirects_to_signup(client):
    response = client.get("/register")
    assert response.status_code == 301
    assert response.headers["Location"].endswith("/signup")


def test_app_page_keeps_default_title(client):
    assert "<title>Aistora | App</title>" in client.get("/app").get_data(as_text=True)
