"""Comparison semantics for filter_rows.

The previous implementation compared ``str(actual)`` against ``str(expected)``
whenever either side failed to parse as a number. For the ordering operators
that produced a silent lexicographic comparison: "9" > "10" is true as text and
false as a number, so a filter could return confidently wrong rows.
"""

import pytest

from engine.dataframe import DataFrame
from services.agent_tools import AgentToolError, AgentToolRuntime


SCHEMA = {"t": {"types": {"amount": "float", "name": "str", "day": "str"}, "row_count": 5}}

ROWS = [
    {"amount": 9, "name": "Acme", "day": "2026-01-05"},
    {"amount": 10, "name": "Beta", "day": "2026-02-10"},
    {"amount": 100, "name": "Gamma", "day": "2026-03-15"},
    {"amount": None, "name": "Delta", "day": None},
    {"amount": "n/a", "name": "Epsilon", "day": "not a date"},
]


def make_runtime():
    return AgentToolRuntime(
        schema=SCHEMA,
        relationships=[],
        table_loader=lambda name: DataFrame(list(ROWS)),
        request_id="r1",
        user_id=1,
    )


def filtered(operator, value, column="amount"):
    runtime = make_runtime()
    runtime.execute("filter_rows", {
        "source": "t", "column": column, "operator": operator,
        "value": value, "save_as": "out",
    })
    return [row["name"] for row in runtime.results["out"].value._get_data()]


def test_numeric_ordering_is_numeric_even_when_the_value_arrives_as_text():
    # As text, "9" sorts after "10". As a number it does not.
    assert filtered("gt", "9") == ["Beta", "Gamma"]
    assert filtered("lt", "100") == ["Acme", "Beta"]


def test_uncomparable_rows_are_excluded_rather_than_string_compared():
    result = filtered("gte", "0")
    assert "Delta" not in result      # None
    assert "Epsilon" not in result    # "n/a"


def test_iso_dates_order_chronologically():
    assert filtered("gte", "2026-02-01", column="day") == ["Beta", "Gamma"]
    assert filtered("lt", "2026-02-01", column="day") == ["Acme"]


def test_ordering_a_non_orderable_value_is_an_explicit_error():
    runtime = make_runtime()
    with pytest.raises(AgentToolError) as excinfo:
        runtime.execute("filter_rows", {
            "source": "t", "column": "name", "operator": "gt",
            "value": "Acme", "save_as": "out",
        })
    assert "cannot be used with the 'gt' operator" in str(excinfo.value)


def test_equality_on_text_is_case_insensitive_and_still_works():
    assert filtered("eq", "acme", column="name") == ["Acme"]
    assert filtered("contains", "MM", column="name") == ["Gamma"]


def test_ambiguous_day_month_dates_are_not_guessed():
    from services.agent_tools import _as_date
    assert _as_date("2026-03-04") is not None   # unambiguous ISO
    assert _as_date("03/04/2026") is None       # 3 April or 4 March?
    assert _as_date("25/12/2026") is not None   # only one reading is valid


def test_base_tables_are_loaded_once_per_run():
    loads = []

    def loader(name):
        loads.append(name)
        return DataFrame(list(ROWS))

    runtime = AgentToolRuntime(
        schema=SCHEMA, relationships=[], table_loader=loader,
        request_id="r2", user_id=1,
    )
    runtime.execute("count_rows", {"source": "t", "save_as": "a"})
    runtime.execute("filter_rows", {
        "source": "t", "column": "amount", "operator": "gt",
        "value": "5", "save_as": "b",
    })
    runtime.execute("select_columns", {
        "source": "t", "columns": ["name"], "save_as": "c",
    })
    # Each load is a database round trip plus a storage materialise.
    assert loads == ["t"], f"base table re-read {len(loads)} times"
