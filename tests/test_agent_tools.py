import pytest

from engine.dataframe import DataFrame
from services.agent_audit import AgentAuditLogger
from services.agent_tools import (
    AgentCancelled,
    AgentLimits,
    AgentToolRuntime,
    ResourceLimitExceeded,
)


SCHEMA = {
    "sales": {
        "types": {"region": "str", "amount": "float", "status": "str"},
        "row_count": 4,
    }
}

ROWS = [
    {"region": "West", "amount": 100, "status": "paid"},
    {"region": "West", "amount": 50, "status": "open"},
    {"region": "East", "amount": 200, "status": "paid"},
    {"region": "East", "amount": 25, "status": "paid"},
]


class Audit:
    def __init__(self):
        self.entries = []

    def record(self, *args):
        self.entries.append(args)


def runtime(**kwargs):
    audit = kwargs.pop("audit", Audit())
    return AgentToolRuntime(
        schema=SCHEMA,
        relationships=[],
        table_loader=lambda name: DataFrame(ROWS) if name == "sales" else None,
        request_id="request-1",
        user_id=7,
        audit=audit,
        **kwargs,
    )


def test_named_results_can_be_reused_across_tools():
    tools = runtime()
    filtered = tools.execute("filter_rows", {
        "source": "sales",
        "column": "status",
        "operator": "eq",
        "value": "paid",
        "save_as": "paid_sales",
    })
    aggregate = tools.execute("aggregate_rows", {
        "source": "paid_sales",
        "group_by": "region",
        "value_column": "amount",
        "operation": "sum",
        "save_as": "sales_by_region",
    })

    assert filtered == {
        "status": "ok",
        "name": "paid_sales",
        "kind": "dataframe",
        "columns": ["region", "amount", "status"],
        "rows": 3,
    }
    assert aggregate["groups"] == 2
    assert tools.results["sales_by_region"].value["East"]["sum_amount"] == 225


def test_top_rows_can_rank_a_named_aggregate():
    tools = runtime()
    tools.execute("aggregate_rows", {
        "source": "sales",
        "group_by": "region",
        "value_column": "amount",
        "operation": "sum",
        "save_as": "sales_by_region",
    })

    ranked = tools.execute("top_rows", {
        "source": "sales_by_region",
        "sort_column": "sum_amount",
        "limit": 1,
        "descending": True,
        "save_as": "top_region",
    })

    assert ranked["status"] == "ok"
    assert ranked["rows"] == 1
    assert tools.results["top_region"].value == [{
        "region": "East",
        "sum_amount": 225.0,
    }]


def test_observations_do_not_expose_row_or_scalar_values():
    tools = runtime()
    top = tools.execute("top_rows", {
        "source": "sales",
        "sort_column": "amount",
        "limit": 2,
        "descending": True,
        "save_as": "top_sales",
    })
    count = tools.execute("count_rows", {
        "source": "sales",
        "save_as": "sales_count",
    })

    assert "West" not in str(top)
    assert 200 not in top.values()
    assert count == {"status": "ok", "name": "sales_count", "kind": "number"}
    assert tools.results["sales_count"].value == 4


def test_chart_pauses_until_external_sharing_is_approved():
    tools = runtime()
    tools.execute("aggregate_rows", {
        "source": "sales",
        "group_by": "region",
        "value_column": "amount",
        "operation": "sum",
        "save_as": "totals",
    })
    paused = tools.execute("create_chart", {
        "source": "totals",
        "title": "Totals",
        "chart_type": "bar",
        "save_as": "chart",
    })

    assert paused["approval_required"] is True
    assert tools.approval["scope"] == "external_chart"
    assert "chart" not in tools.results


def test_resource_and_cancellation_limits_are_enforced():
    limited = runtime(limits=AgentLimits(max_materialized_rows=1))
    with pytest.raises(ResourceLimitExceeded):
        limited.execute("filter_rows", {
            "source": "sales",
            "column": "status",
            "operator": "eq",
            "value": "paid",
            "save_as": "too_many",
        })

    cancelled = runtime(cancelled=lambda: True)
    with pytest.raises(AgentCancelled):
        cancelled.execute("inspect_schema", {})


def test_audit_log_excludes_filter_values(tmp_path):
    audit_path = tmp_path / "audit.jsonl"
    audit = AgentAuditLogger(str(audit_path))
    tools = runtime(audit=audit)
    tools.execute("filter_rows", {
        "source": "sales",
        "column": "status",
        "operator": "eq",
        "value": "sensitive-value",
        "save_as": "paid",
    })

    audit_text = audit_path.read_text(encoding="utf-8")
    assert "sensitive-value" not in audit_text
    assert '"tool": "filter_rows"' in audit_text


def test_bucket_dates_enables_monthly_trends():
    dated_rows = [
        {"order_date": "2025-01-05", "amount": 10},
        {"order_date": "2025-01-20", "amount": 5},
        {"order_date": "2025-02-01T09:30:00", "amount": 7},
        {"order_date": "14 Mar 2025", "amount": 1},
        {"order_date": "not a date", "amount": 99},
    ]
    tools = AgentToolRuntime(
        schema={"orders": {"types": {"order_date": "str", "amount": "int"}, "row_count": 5}},
        relationships=[],
        table_loader=lambda name: DataFrame(dated_rows) if name == "orders" else None,
        request_id="request-2",
        user_id=7,
        audit=Audit(),
    )
    bucketed = tools.execute("bucket_dates", {
        "source": "orders", "column": "order_date", "granularity": "month", "save_as": "monthly",
    })
    assert bucketed["columns"] == ["order_date", "amount", "order_date_month"]
    assert tools.results["monthly"].metadata["unparsed_dates"] == 1

    tools.execute("aggregate_rows", {
        "source": "monthly", "group_by": "order_date_month", "value_column": "amount",
        "operation": "sum", "save_as": "amount_by_month",
    })
    totals = tools.results["amount_by_month"].value
    assert totals["2025-01"]["sum_amount"] == 15
    assert totals["2025-02"]["sum_amount"] == 7
    assert totals["2025-03"]["sum_amount"] == 1
    assert None not in totals  # unparsed dates are excluded from groups

    quarters = tools.execute("bucket_dates", {
        "source": "orders", "column": "order_date", "granularity": "quarter", "save_as": "quarterly",
    })
    assert quarters["rows"] == 5
    assert {row["order_date_quarter"] for row in tools.results["quarterly"].value._get_data()} == {"2025-Q1", None}

    with pytest.raises(Exception):
        tools.execute("bucket_dates", {
            "source": "orders", "column": "order_date", "granularity": "decade", "save_as": "x",
        })
