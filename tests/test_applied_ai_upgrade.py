import pytest

from engine.dataframe import DataFrame
from services.agent_evaluation import AgentEvaluationCase, evaluate_suite
from services.agent_service import AgentOutcome, run_agent
from services.agent_tools import AgentLimits, AgentToolError, AgentToolRuntime, ResourceLimitExceeded, StoredResult
from services.llm_service import AgentFunctionCall, AgentModelTurn
from services.privacy import (
    PrivacyPolicy,
    classify_column,
    protect_result_rows,
    redact_relationships,
    redact_schema,
)
from services.request_router import route_request


class NoAudit:
    def record(self, *args):
        pass


def runtime(**kwargs):
    return AgentToolRuntime(
        schema={"sales": {"types": {"amount": "float", "email": "str", "api_key": "str"}, "row_count": 2}},
        relationships=[],
        table_loader=lambda _: DataFrame([{"amount": 10, "email": "a@x.test"}, {"amount": 20, "email": "b@x.test"}]),
        request_id="request",
        user_id=1,
        audit=NoAudit(),
        **kwargs,
    )


def test_privacy_classifies_schema_and_masks_display_rows():
    policy = PrivacyPolicy(schema_mode="classified", result_mode="masked")
    safe = redact_schema(runtime().schema, policy)
    assert "api_key" not in safe["sales"]["columns"]
    assert safe["sales"]["classifications"]["email"] == "direct_identifier"
    assert protect_result_rows([{"email": "a@x.test", "amount": 10}], policy) == [
        {"email": "[REDACTED]", "amount": 10}
    ]
    assert redact_relationships([{
        "from_table": "sales", "from_column": "api_key",
        "to_table": "sales", "to_column": "amount",
    }], safe) == []


def test_privacy_roles_do_not_confuse_product_labels_with_personal_names():
    assert classify_column("product_name") == "ordinary"
    assert classify_column("customer_id") == "identifier"
    assert classify_column("free_text_summary") == "unstructured_text"
    assert classify_column("relationship_manager") == "direct_identifier"

    policy = PrivacyPolicy(result_mode="masked")
    assert protect_result_rows([{
        "product_name": "Premium plan",
        "customer_id": 123,
        "free_text_summary": "customer complaint",
        "relationship_manager": "Jamie",
    }], policy) == [{
        "product_name": "Premium plan",
        "customer_id": "[REDACTED]",
        "free_text_summary": "[REDACTED]",
        "relationship_manager": "[REDACTED]",
    }]


def test_router_selects_specialized_bounded_tools():
    route = route_request("Chart total sales by region", {"sales": {}})
    assert route.intent == "visualization"
    assert route.model_tier == "advanced"
    assert "create_chart" in route.allowed_tools
    assert "join_sources" not in route.allowed_tools


def test_router_detects_implicit_cross_table_request():
    route = route_request(
        "Show transactions over 50000 for California customers",
        {"transactions": {}, "customers": {}, "products": {}},
    )

    assert route.intent == "relationship"
    assert route.model_tier == "advanced"
    assert "join_sources" in route.allowed_tools
    assert "filter_rows" in route.allowed_tools
    assert "top_rows" in route.allowed_tools


def test_plan_schema_and_tool_route_are_enforced():
    agent_runtime = runtime(allowed_tools=["record_plan"])
    with pytest.raises(AgentToolError, match="unique"):
        agent_runtime.execute("record_plan", {"steps": ["Do work", "do work"]})
    with pytest.raises(AgentToolError, match="not allowed"):
        agent_runtime.execute("count_rows", {"source": "sales", "save_as": "count"})


def test_self_correction_is_bounded_and_safe():
    agent_runtime = runtime(limits=AgentLimits(max_corrections=1))
    agent_runtime.record_correction()
    with pytest.raises(ResourceLimitExceeded, match="self-correction"):
        agent_runtime.record_correction()


def test_evaluation_suite_reports_requested_metrics():
    outcome = AgentOutcome(
        status="finished",
        message="done",
        result=StoredResult("count", "number", 2),
        plan=["Count rows"],
        trace=[{"tool": "count_rows"}, {"tool": "finish"}],
        turns=2,
        tool_calls=2,
        metrics={"self_corrections": 1, "retries": 2, "latency_ms": 30, "total_tokens": 40, "estimated_cost_usd": .01},
    )
    case = AgentEvaluationCase(
        name="count",
        query="count sales",
        expected_tools=["count_rows", "finish"],
        expected_result_kind="number",
        expected_answer=2,
        required_correction=True,
    )
    report = evaluate_suite([(case, outcome)])
    assert report["passed"] == 1
    assert report["metrics"] == {
        "plan_validity_rate": 1.0,
        "execution_success_rate": 1.0,
        "answer_accuracy_rate": 1.0,
        "self_correction_success_rate": 1.0,
        "total_retries": 2,
        "average_latency_ms": 30,
        "total_tokens": 40,
        "estimated_cost_usd": 0.01,
    }


def test_aggregate_results_honour_the_result_policy():
    """The aggregate render path used to bypass masking entirely, so grouping
    by a sensitive column exposed values that selecting it would have hidden."""
    from services.privacy import PrivacyPolicy, protect_result_rows

    policy = PrivacyPolicy(result_mode="masked")
    rows = [{"email": "a@x.test", "sum_amount": 10}]
    assert protect_result_rows(rows, policy) == [
        {"email": "[REDACTED]", "sum_amount": 10}
    ]


def test_result_policy_defaults_to_showing_the_owner_their_own_data():
    from services.privacy import PrivacyPolicy, protect_result_rows

    rows = [{"customer_name": "Acme Ltd", "amount": 10}]
    assert protect_result_rows(rows, PrivacyPolicy()) == rows


def test_schema_mode_still_defaults_to_withholding_credentials():
    """The LLM boundary is unchanged: this is the guarantee the product makes."""
    from services.privacy import PrivacyPolicy, redact_schema

    safe = redact_schema(
        {"users": {"types": {"id": "int", "api_key": "str", "email": "str"}}},
        PrivacyPolicy(),
    )
    assert "api_key" not in safe["users"]["columns"]
    assert safe["users"]["classifications"]["email"] == "direct_identifier"
