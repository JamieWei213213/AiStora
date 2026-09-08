import pytest

from services.validation import (
    ValidationError,
    validate_database_name,
    validate_table_name,
)


@pytest.mark.parametrize("value", [
    "Acme Ltd",
    "Q3 2026 - Acme",
    "O'Brien & Sons (Holdings)",
    "client_exports.2026",
])
def test_reasonable_business_names_are_accepted(value):
    assert validate_database_name(value) == value


@pytest.mark.parametrize("value", [
    "<img src=x onerror=alert(1)>",
    "name<script>",
    "bad\x00null",
    "bell\x07char",
    "",
    "   ",
    None,
    "x" * 81,
])
def test_dangerous_or_empty_database_names_are_rejected(value):
    with pytest.raises(ValidationError):
        validate_database_name(value)


def test_whitespace_is_collapsed_not_preserved():
    assert validate_database_name("  Acme    Ltd  ") == "Acme Ltd"
    # Tabs and newlines are whitespace, so they collapse rather than reject.
    assert validate_database_name("Acme\tLtd") == "Acme Ltd"


@pytest.mark.parametrize("value", ["sales", "sales_2026", "Q3 sales", "sales-clean.v2"])
def test_valid_table_names(value):
    assert validate_table_name(value) == value


@pytest.mark.parametrize("value", ["<b>x</b>", "_leading", "a" * 65, "drop;table"])
def test_invalid_table_names(value):
    with pytest.raises(ValidationError):
        validate_table_name(value)
