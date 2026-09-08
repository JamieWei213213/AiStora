from services.agent_evaluation import (
    AgentEvaluationCase,
    evaluate_case,
    evaluate_suite,
)
from services.agent_service import AgentOutcome
from services.agent_tools import StoredResult
from services.agent_verifier import verify_outcome
from services.model_router import route_agent_task


def finished_average_outcome():
    result = StoredResult(
        "average_sales",
        "aggregate",
        {"east": {"avg_sales": 10.0}},
        ["region", "avg_sales"],
        {"group_by": "region", "operation": "avg", "metric": "avg_sales"},
    )
    return AgentOutcome(
        status="finished",
        message="Ready.",
        result=result,
        trace=[
            {"tool": "aggregate_rows", "status": "ok"},
            {"tool": "finish", "status": "ok"},
        ],
        turns=2,
        tool_calls=2,
    )


def test_verifier_checks_operation_and_agent_evaluation_constraints():
    outcome = finished_average_outcome()
    verification = verify_outcome("Average sales by region", outcome)
    assert verification["passed"] is True

    case = AgentEvaluationCase(
        name="average-by-region",
        query="Average sales by region",
        expected_tools=["aggregate_rows", "finish"],
        forbidden_tools=["create_chart"],
        expected_result_kind="aggregate",
        max_turns=3,
        max_tool_calls=3,
    )
    result = evaluate_case(case, outcome)
    assert result["passed"] is True
    assert evaluate_suite([(case, outcome)])["average_score"] == 1.0


def test_verifier_accepts_valid_result_after_recoverable_tool_error():
    outcome = finished_average_outcome()
    outcome.trace.insert(1, {
        "tool": "top_rows",
        "status": "error",
        "summary": "An exploratory ranking attempt failed.",
    })

    verification = verify_outcome("Auto-analyze this database", outcome)

    assert verification["passed"] is True
    tool_check = next(
        check for check in verification["checks"]
        if check["name"] == "tool_execution"
    )
    assert tool_check["severity"] == "advisory"
    assert tool_check["passed"] is False


def test_model_router_uses_advanced_model_only_for_complex_work():
    schema = {"sales": {}, "customers": {}}
    assert route_agent_task("Count sales", schema)["tier"] == "standard"
    advanced = route_agent_task("Join sales and customers", schema)
    assert advanced["tier"] == "advanced"
    assert "cross-table" in advanced["reason"]
