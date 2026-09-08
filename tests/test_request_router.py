"""Regression tests for the request router.

The router previously used keyword matching to decide which tools the agent was
allowed to call, and fell through to an empty tool set. Ordinary questions
matched no keyword, so the agent was handed no data tools and could not answer
them. These tests pin the fix: every request gets analytical tools, and only
the externally-visible chart tool stays gated.
"""

import pytest

from services.request_router import (
    ANALYTICAL_TOOLS,
    BASE_TOOLS,
    route_request,
)


SINGLE_TABLE = {"transactions": {"types": {"amount": "float", "client": "str"}}}
TWO_TABLES = {
    "transactions": {"types": {"amount": "float", "client_id": "int"}},
    "clients": {"types": {"id": "int", "name": "str"}},
}


REALISTIC_QUESTIONS = [
    "Which client billed the most last quarter?",
    "How much revenue did we bring in during Q3?",
    "Who are my biggest customers?",
    "Break down expenses by category",
    "Are any invoices overdue?",
    "What is the average transaction amount?",
    "Show me the top 10 clients by revenue",
    "anything at all that matches no keyword whatsoever",
    "",
]


@pytest.mark.parametrize("question", REALISTIC_QUESTIONS)
def test_every_request_receives_analytical_tools(question):
    decision = route_request(question, SINGLE_TABLE)
    for tool in ANALYTICAL_TOOLS:
        assert tool in decision.allowed_tools, (
            f"{tool!r} withheld from {question!r}; the agent cannot answer "
            "questions it has no tools for"
        )


@pytest.mark.parametrize("question", REALISTIC_QUESTIONS)
def test_base_tools_are_always_available(question):
    decision = route_request(question, SINGLE_TABLE)
    assert set(BASE_TOOLS).issubset(set(decision.allowed_tools))


def test_chart_tool_stays_gated_behind_an_explicit_request():
    assert "create_chart" not in route_request("total revenue", SINGLE_TABLE).allowed_tools
    assert "create_chart" in route_request("chart revenue by month", SINGLE_TABLE).allowed_tools
    assert "create_chart" in route_request("plot this", SINGLE_TABLE).allowed_tools


def test_join_is_offered_only_when_more_than_one_table_exists():
    assert "join_sources" not in route_request("compare", SINGLE_TABLE).allowed_tools
    assert "join_sources" in route_request("compare", TWO_TABLES).allowed_tools


def test_unclassified_multi_table_requests_use_the_capable_model():
    assert route_request("are any invoices overdue?", TWO_TABLES).model_tier == "advanced"
    assert route_request("are any invoices overdue?", SINGLE_TABLE).model_tier == "standard"


def test_routing_can_be_disabled_without_removing_tools():
    decision = route_request("compare revenue across regions", TWO_TABLES, enabled=False)
    assert decision.model_tier == "standard"
    for tool in ANALYTICAL_TOOLS:
        assert tool in decision.allowed_tools


def test_auto_mode_is_treated_as_aggregation():
    decision = route_request("anything", TWO_TABLES, mode="auto")
    assert decision.intent == "aggregation"
    assert decision.model_tier == "advanced"
