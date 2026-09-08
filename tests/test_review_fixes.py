"""Regression tests for the 8 September 2026 review fixes.

Parser: silent truncation, unsupported encodings, duplicate headers, inf.
Chart: identifier labels must not leave the server.
Auth: session id rotates on login.
Storage: the S3 materialise cache is evicted.
Frontend: no CDN scripts, no inline scripts, strict CSP.
"""

import io
import os
import re
import tempfile
import time
from pathlib import Path

import pytest
from flask import Flask

import routes.auth as auth_routes
import routes.chat as chat_routes
from engine.dataframe import DataFrame
from engine.parser import CsvParseError, CsvParser, dedupe_header, detect_encoding
from extensions import db
from routes.auth import auth_bp
from services.agent_tools import AgentToolError, AgentToolRuntime, MAX_CHART_GROUPS
from services.session_store import configure_sessions
from services.storage_service import S3DatasetStorage


def _csv(tmp_path, body, name="data.csv"):
    path = tmp_path / name
    path.write_bytes(body)
    return str(path)


# --------------------------------------------------------------------------
# Parser
# --------------------------------------------------------------------------

def test_parser_reads_windows_1252_and_utf16(tmp_path):
    cp1252 = CsvParser(_csv(tmp_path, "name,city\nJosé,México\n".encode("cp1252")))
    assert cp1252.encoding == "cp1252"
    assert list(cp1252.parse()) == [{"name": "José", "city": "México"}]

    utf16 = CsvParser(_csv(tmp_path, "name,n\nZoë,1\n".encode("utf-16"), "u.csv"))
    assert utf16.encoding == "utf-16"
    assert list(utf16.parse()) == [{"name": "Zoë", "n": 1}]

    utf8 = CsvParser(_csv(tmp_path, "﻿name,n\nZoë,1\n".encode("utf-8"), "b.csv"))
    assert utf8.encoding == "utf-8-sig"
    assert utf8.header == ["name", "n"]


def test_parser_no_longer_truncates_silently_on_huge_fields(tmp_path):
    # Above the old 128 KB csv limit, below the new one: all rows survive.
    body = b"a,b\n1,\"" + b"z" * 200_000 + b"\"\n2,ok\n3,ok\n"
    assert len(list(CsvParser(_csv(tmp_path, body)).parse())) == 3

    # Above the new limit: a real error, not a shorter file.
    body = b"a,b\n1,\"" + b"z" * 1_100_000 + b"\"\n2,ok\n"
    with pytest.raises(CsvParseError):
        list(CsvParser(_csv(tmp_path, body, "huge.csv")).parse())


def test_duplicate_and_blank_headers_are_made_unique(tmp_path):
    assert dedupe_header(["amount", "amount", "", "amount"]) == [
        "amount", "amount_2", "column_3", "amount_3",
    ]
    parser = CsvParser(_csv(tmp_path, b"amount,amount\n1,2\n"))
    assert list(parser.parse()) == [{"amount": 1, "amount_2": 2}]


def test_non_finite_floats_become_missing(tmp_path):
    parser = CsvParser(_csv(tmp_path, b"x,y\n1,inf\n2,3.5\n3,-Infinity\n4,nan\n"))
    assert parser.column_types == {"x": "int", "y": "float"}
    assert [row["y"] for row in parser.parse()] == [None, 3.5, None, None]
    assert chat_routes._display_value(float("inf")) is None
    assert chat_routes._display_value(2.5) == 2.5


def test_empty_file_is_a_clear_error(tmp_path):
    with pytest.raises(CsvParseError):
        CsvParser(_csv(tmp_path, b"\n"))


# --------------------------------------------------------------------------
# Chart labels
# --------------------------------------------------------------------------

ROWS = [
    {"region": "West", "customer_name": "Ann", "amount": 100},
    {"region": "East", "customer_name": "Bob", "amount": 200},
]
SCHEMA = {"sales": {"types": {"region": "str", "customer_name": "str", "amount": "int"}, "row_count": 2}}


class Audit:
    def record(self, *args):
        pass


def _runtime():
    return AgentToolRuntime(
        schema=SCHEMA,
        relationships=[],
        table_loader=lambda name: DataFrame(ROWS),
        request_id="r1",
        user_id=1,
        audit=Audit(),
        approvals=["external_chart"],
    )


def test_chart_refuses_identifier_labels():
    tools = _runtime()
    tools.execute("aggregate_rows", {
        "source": "sales", "group_by": "customer_name", "value_column": "amount",
        "operation": "sum", "save_as": "by_customer",
    })
    with pytest.raises(AgentToolError) as excinfo:
        tools.execute("create_chart", {
            "source": "by_customer", "title": "t", "chart_type": "bar", "save_as": "c",
        })
    assert "customer_name" in str(excinfo.value)
    assert "chart" not in tools.results


def test_chart_allows_ordinary_labels_and_names_the_column():
    tools = _runtime()
    tools.execute("aggregate_rows", {
        "source": "sales", "group_by": "region", "value_column": "amount",
        "operation": "sum", "save_as": "by_region",
    })
    tools.approvals = []
    paused = tools.execute("create_chart", {
        "source": "by_region", "title": "t", "chart_type": "bar", "save_as": "c",
    })
    assert paused["approval_required"] is True
    assert "'region'" in tools.approval["message"]
    assert "2 group labels" in tools.approval["message"]


def test_chart_group_count_is_capped():
    rows = [{"region": f"r{i}", "amount": i} for i in range(MAX_CHART_GROUPS + 1)]
    tools = AgentToolRuntime(
        schema=SCHEMA, relationships=[], table_loader=lambda name: DataFrame(rows),
        request_id="r2", user_id=1, audit=Audit(), approvals=["external_chart"],
    )
    tools.execute("aggregate_rows", {
        "source": "sales", "group_by": "region", "value_column": "amount",
        "operation": "sum", "save_as": "many",
    })
    with pytest.raises(AgentToolError):
        tools.execute("create_chart", {
            "source": "many", "title": "t", "chart_type": "bar", "save_as": "c",
        })


# --------------------------------------------------------------------------
# Session rotation
# --------------------------------------------------------------------------

def test_login_rotates_the_session_id():
    app = Flask(__name__)
    app.config.update(
        TESTING=True, SECRET_KEY="test", SQLALCHEMY_DATABASE_URI="sqlite://",
        SQLALCHEMY_TRACK_MODIFICATIONS=False, SESSION_TYPE="cachelib",
        SESSION_FILE_DIR=tempfile.mkdtemp(prefix="aistora-rot-"), IS_PRODUCTION=False,
    )
    db.init_app(app)
    configure_sessions(app)
    app.register_blueprint(auth_bp)
    with app.app_context():
        db.create_all()
    auth_routes.auth_rate_limiter.clear()
    client = app.test_client()
    client.post("/api/register", json={"email": "rot@example.com", "password": "correct-horse-battery"})

    # Establish a pre-login session cookie (what a fixation attack plants).
    with client.session_transaction() as flask_session:
        flask_session["planted"] = True
    before = client.get_cookie("session")
    response = client.post("/api/login", json={"email": "rot@example.com", "password": "correct-horse-battery"})
    assert response.status_code == 200
    after = client.get_cookie("session")
    assert before is not None and after is not None
    assert before.value != after.value


# --------------------------------------------------------------------------
# S3 cache eviction
# --------------------------------------------------------------------------

class FakeS3:
    def __init__(self):
        self.objects = {}

    def head_object(self, Bucket, Key):
        return {"ContentLength": len(self.objects[Key]), "ETag": '"e"', "LastModified": "now"}

    def download_file(self, Bucket, Key, Filename):
        Path(Filename).write_bytes(self.objects[Key])


def test_materialise_cache_is_evicted_least_recently_used(tmp_path):
    client = FakeS3()
    storage = S3DatasetStorage(
        "bucket", "datasets", "us-west-2", tmp_path / "cache", client=client,
        max_cache_bytes=250,
    )
    for name in ("a", "b", "c"):
        client.objects[f"datasets/1/{name}/{name}.csv"] = (name * 100).encode()
    paths = []
    for name in ("a", "b", "c"):
        paths.append(storage.materialize(f"s3://bucket/datasets/1/{name}/{name}.csv"))
        # Ensure distinct access times on coarse filesystems.
        os.utime(paths[-1], (time.time() - (3 - len(paths)) * 10,) * 2)
    cached = sorted(p.name for p in (tmp_path / "cache").iterdir())
    assert len(cached) == 2, cached
    assert not Path(paths[0]).exists()  # oldest evicted
    assert Path(paths[2]).exists()


# --------------------------------------------------------------------------
# Frontend delivery
# --------------------------------------------------------------------------

def test_templates_load_no_third_party_scripts_and_no_inline_scripts():
    root = Path(__file__).parents[1]
    for template in (root / "templates").rglob("*.html"):
        html = template.read_text(encoding="utf-8")
        assert "cdn.tailwindcss.com" not in html, template
        assert "unpkg.com" not in html, template
        for block in re.findall(r"<script(?P<attrs>[^>]*)>(?P<body>.*?)</script>", html, re.S):
            attrs, body = block
            assert "src=" in attrs, f"inline script in {template}"
            assert not body.strip(), f"inline script body in {template}"
    assert (root / "static" / "css" / "tailwind.css").stat().st_size > 10_000
    assert list((root / "static" / "js" / "vendor").glob("lucide-*.min.js"))


def test_csp_trusts_only_the_origin_for_scripts_and_styles():
    from config import Config

    policy = Config.CONTENT_SECURITY_POLICY
    script_src = policy.split("script-src")[1].split(";")[0]
    style_src = policy.split("style-src")[1].split(";")[0]
    assert script_src.strip() == "'self'"
    assert style_src.strip() == "'self'"


def test_login_form_submits_on_enter():
    root = Path(__file__).parents[1]
    html = (root / "templates" / "components" / "app" / "auth_screen.html").read_text()
    assert '<form id="auth-form"' in html
    assert 'type="submit"' in html
    assert 'autocomplete="current-password"' in html
    script = (root / "static" / "js" / "scripts.js").read_text()
    assert 'authForm.addEventListener("submit"' in script
