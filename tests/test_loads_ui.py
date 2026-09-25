"""Template and bundle checks for the data-loads UI.

There is no JS test runner in this project, so like ``test_agent_ui.py`` and
``test_frontend_escaping.py`` these tests render the app page and read the
bundle to pin the contract the pipeline frontend relies on: the element ids
scripts.js looks up, the CSP rules (no inline handlers/styles, no browser
dialogs in the loads flow) and escaping of API strings in the loads renderer.
"""

import re
from pathlib import Path

import pytest

from app import app


ROOT = Path(__file__).resolve().parents[1]
BUNDLE = ROOT / "static" / "js" / "scripts.js"
UPLOAD_TEMPLATE = ROOT / "templates" / "components" / "app" / "upload_screen.html"


@pytest.fixture(scope="module")
def source():
    return BUNDLE.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def loads_section(source):
    start = source.index("// === Data Loads (pipeline) Logic ===")
    end = source.index("// === Agentic Chat Logic ===")
    return source[start:end]


def test_load_controls_render_in_app_page():
    response = app.test_client().get("/app")
    html = response.get_data(as_text=True)

    assert response.status_code == 200
    for element_id in (
        "load-options",
        "load-mode",
        "load-key-columns",
        "load-dataset",
        "load-keep-history",
        "loads-list",
        "loads-refresh",
        "pipeline-health-toggle",
        "pipeline-health-panel",
    ):
        assert f'id="{element_id}"' in html, element_id

    # The three pipeline modes are offered, replace being the default.
    assert '<option value="replace" selected>' in html
    assert '<option value="append">' in html
    assert '<option value="merge">' in html


def test_upload_template_has_no_inline_handlers_or_styles():
    html = UPLOAD_TEMPLATE.read_text(encoding="utf-8")
    assert not re.search(r"\son\w+\s*=", html)
    assert "style=" not in html
    assert "<script" not in html


def test_upload_sends_load_options_and_handles_pipeline_response(source):
    assert "function readLoadOptions(" in source
    for field in ("mode", "key_columns", "dataset", "keep_history"):
        assert field in source.split("function readLoadOptions(")[1][:1500], field
    # Both response shapes: pipeline (loads array) and legacy (schema only).
    assert "Array.isArray(data.loads)" in source
    assert "ingestLoads(data.loads)" in source
    assert "/api/loads/${encodeURIComponent(loadId)}" in source


def test_loads_renderer_escapes_api_strings(loads_section):
    # Dataset names, filenames, error and check messages come from uploaded
    # CSVs or the pipeline; each must pass through escapeHtml().
    for expression in (
        "load.dataset",
        "load.original_filename",
        "load.error.message",
        "check.name",
        "check.message",
        "changes.blocked_reason",
        "item.dataset",
        "metrics.backend",
    ):
        raw = re.compile(r"\$\{\s*" + re.escape(expression) + r"\s*\}")
        assert not raw.search(loads_section), (
            f"{expression} is interpolated into HTML without escapeHtml()"
        )
        wrapped = re.compile(r"escapeHtml\(\s*" + re.escape(expression))
        assert wrapped.search(loads_section), f"{expression} is never escaped"
    assert loads_section.count("escapeHtml(") >= 20


def test_loads_flow_avoids_browser_dialogs_and_inline_handlers(loads_section):
    for forbidden in ("window.confirm", "confirm(", "alert(", "prompt(", "onclick", "style="):
        assert forbidden not in loads_section, forbidden
    # Rollback is confirmed in the DOM and buttons are delegated on the list.
    assert "load-rollback-confirm-btn" in loads_section
    assert 'loadsList.addEventListener("click"' in loads_section
    # The sparkline is built with DOM APIs and attribute setters.
    assert 'document.createElementNS(SVG_NS, "rect")' in loads_section
    assert "setAttribute(" in loads_section


def test_polling_is_bounded(loads_section):
    assert "LOAD_POLL_INTERVAL_MS = 1500" in loads_section
    assert "LOAD_POLL_MAX_MS = 5 * 60 * 1000" in loads_section
    assert "data.load.is_terminal" in loads_section
