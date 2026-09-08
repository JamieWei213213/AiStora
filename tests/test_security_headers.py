from flask import Flask, jsonify

from app import apply_security_headers
from config import Config


def build(secure=False):
    test_app = Flask(__name__)
    test_app.after_request(apply_security_headers)

    @test_app.get("/api/private")
    def private_api():
        return jsonify({"ok": True})

    @test_app.get("/page")
    def page():
        return "hello"

    base = "https://aistora.test" if secure else "http://aistora.test"
    return test_app.test_client(), base


def test_security_headers_and_private_api_cache_policy():
    client, base = build()
    response = client.get(f"{base}/api/private")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "SAMEORIGIN"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert response.headers["Permissions-Policy"] == "camera=(), microphone=(), geolocation=()"
    assert response.headers["Cache-Control"] == "no-store"


def test_content_security_policy_is_present_and_restrictive():
    client, base = build()
    policy = client.get(f"{base}/page").headers["Content-Security-Policy"]

    assert "default-src 'self'" in policy
    assert "object-src 'none'" in policy
    assert "base-uri 'self'" in policy
    # Inline script must not be allowed: it is what makes an injected
    # <img onerror> or <script> in a CSV header inert.
    assert "'unsafe-inline'" not in policy.split("script-src")[1].split(";")[0]
    assert "'unsafe-eval'" not in policy


def test_hsts_is_sent_only_over_https(monkeypatch):
    monkeypatch.setattr(Config, "HSTS_SECONDS", 31536000)

    client, _ = build()
    assert "Strict-Transport-Security" not in client.get("http://aistora.test/page").headers

    header = client.get("https://aistora.test/page").headers["Strict-Transport-Security"]
    assert "max-age=31536000" in header
    assert "includeSubDomains" in header
