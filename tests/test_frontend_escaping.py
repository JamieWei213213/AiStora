"""Guard against unescaped interpolation in the browser bundle.

Uploaded CSV column names, table names and database names are rendered into
innerHTML. A CSV is by definition a file supplied by someone else, so a header
such as ``<img src=x onerror=...>`` is a realistic stored-XSS vector against
the accountant who opens it. There is no JS test runner in this project, so
this test reads the bundle and asserts that the known user-controlled values
are wrapped in escapeHtml().
"""

import re
from pathlib import Path

import pytest


BUNDLE = Path(__file__).resolve().parents[1] / "static" / "js" / "scripts.js"

# Values that originate from user input or from an uploaded file.
USER_CONTROLLED = [
    "db.name",
    "tableName",
    "colName",
    "colType",
    "details.filename",
    "rel.from_table",
    "rel.from_column",
    "rel.to_table",
    "rel.to_column",
    "table.name",
    "action.description",
]


@pytest.fixture(scope="module")
def source():
    return BUNDLE.read_text(encoding="utf-8")


@pytest.mark.parametrize("expression", USER_CONTROLLED)
def test_user_controlled_values_are_escaped(expression, source):
    # Any `${ expression }` must be `${escapeHtml( expression )}`.
    raw = re.compile(r"\$\{\s*" + re.escape(expression) + r"\s*\}")
    offenders = raw.findall(source)
    assert not offenders, (
        f"{expression} is interpolated into HTML without escapeHtml()"
    )


def test_escape_helper_covers_attribute_delimiters(source):
    # The old helper used textContent -> innerHTML, which does not escape
    # quotes, so a value inside value="..." could break out of the attribute.
    assert 'const HTML_ESCAPES' in source
    for character in ['"&": "&amp;"', '"<": "&lt;"', '">": "&gt;"', "'\"': \"&quot;\""]:
        assert character in source, f"escape map is missing {character}"
    assert "textContent" not in source.split("function escapeHtml")[1][:400]


def test_escape_helper_is_defined_once(source):
    assert source.count("function escapeHtml(") == 1
