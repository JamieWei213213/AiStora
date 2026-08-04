from pathlib import Path

from app import app


def test_agent_controls_render_in_app_page():
    response = app.test_client().get("/app")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="agent-activity-panel"' in html
    assert 'id="chat-cancel"' in html
    assert 'id="clear-agent-memory"' in html
    assert 'id="clear-agent-learning"' in html
    assert 'id="agent-metrics"' in html
    assert 'id="auto-analyze"' in html
    assert 'id="agent-suggestions"' in html
    assert 'id="clean-data-modal"' in html
    assert 'id="clean-data-apply"' in html


def test_request_id_fallback_remains_uuid_compatible():
    script = (
        Path(__file__).parents[1] / "static" / "js" / "scripts.js"
    ).read_text(encoding="utf-8")

    assert "function createRequestId()" in script
    assert 'typeof cryptoApi.randomUUID === "function"' in script
    assert "cryptoApi.getRandomValues(bytes)" in script
    assert "activeRequestId = createRequestId();" in script
    assert "`${Date.now()}-${Math.random()}`" not in script
