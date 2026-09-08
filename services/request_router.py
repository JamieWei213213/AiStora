"""Classify a request for cost-aware model routing and tool scoping.

Design note (changed 2026-09-02)
--------------------------------
This router used to decide *which tools the agent was allowed to call* from a
short list of English keywords, and fell back to an empty tool set whenever no
keyword matched. Ordinary questions such as "Which client billed the most last
quarter?" matched nothing, so the agent was handed no data tools at all and
could not answer them.

Keyword matching is now advisory. It still picks the model tier (the cheap
model for simple work, the capable model for hard work) and it still gates
``create_chart``, because that tool sends aggregate labels and values to an
external service and should only be reachable when a chart was actually asked
for. Every other analytical tool is available by default. Narrowing the
model's search space is a nice-to-have; refusing to answer the question is not.
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class RouteDecision:
    intent: str
    allowed_tools: tuple
    model_tier: str
    reason: str


# Always available: planning, schema inspection, clarification and completion.
BASE_TOOLS = ("record_plan", "inspect_schema", "ask_clarification", "finish")

# Available for every request. Reading data is the product.
ANALYTICAL_TOOLS = (
    "count_rows",
    "filter_rows",
    "select_columns",
    "aggregate_rows",
    "top_rows",
)

# Only meaningful when the project has more than one table.
MULTI_TABLE_TOOLS = ("join_sources",)

# Gated: sends aggregate labels and values to QuickChart, so it stays behind an
# explicit visualization request *and* the separate user approval prompt.
VISUALIZATION_TOOLS = ("create_chart",)


_VISUALIZATION_WORDS = {"chart", "graph", "plot", "visualise", "visualize", "visualization"}
_RELATIONSHIP_WORDS = {"across", "join", "joined", "relationship", "relationships", "merge"}
_COMPARISON_WORDS = {"compare", "comparison", "versus", "vs", "difference", "trend", "trends"}
_COMPARISON_PHRASES = ("over time", "year over year", "month over month", "week over week")
_AGGREGATION_WORDS = {
    "average", "avg", "count", "group", "grouped", "maximum", "mean", "median",
    "minimum", "most", "least", "per", "rank", "ranked", "sum", "summarise",
    "summarize", "total", "breakdown", "break", "many", "number", "rows",
    "records", "entries",
}
_LOOKUP_WORDS = {
    "bottom", "filter", "find", "list", "lowest", "highest", "show", "top",
    "where", "which", "who", "whose",
}


def _words(query):
    return set(re.findall(r"[a-z]+", str(query or "").lower()))


def _mentioned_tables(words, schema):
    mentioned = []
    for table_name in schema or {}:
        table_words = set(re.findall(r"[a-z]+", str(table_name).lower()))
        if table_words and table_words.issubset(words):
            mentioned.append(table_name)
    return mentioned


def classify_intent(query, schema, mode="interactive"):
    """Return (intent, reason). Never returns a state that removes data tools."""
    words = _words(query)
    mentioned = _mentioned_tables(words, schema)

    if mode == "auto":
        return "aggregation", "autonomous schema analysis"
    if words & _VISUALIZATION_WORDS:
        return "visualization", "visualization requested"
    if len(mentioned) >= 2:
        return "relationship", "cross-table request references: " + ", ".join(mentioned[:3])
    if words & _RELATIONSHIP_WORDS:
        return "relationship", "cross-table relationship requested"
    if words & _COMPARISON_WORDS or any(
        phrase in str(query or "").lower() for phrase in _COMPARISON_PHRASES
    ):
        return "comparison", "comparative analysis requested"
    if words & _AGGREGATION_WORDS:
        return "aggregation", "aggregate analysis requested"
    if words & _LOOKUP_WORDS:
        return "lookup", "row lookup requested"
    return "general", "no specific analytical pattern detected; full tool set offered"


def route_request(query, schema, mode="interactive", enabled=True):
    intent, reason = classify_intent(query, schema, mode=mode)

    allowed = BASE_TOOLS + ANALYTICAL_TOOLS
    if len(schema or {}) > 1:
        allowed += MULTI_TABLE_TOOLS
    if intent == "visualization":
        allowed += VISUALIZATION_TOOLS

    # Tier selection is a cost optimisation and is safe to get wrong.
    advanced = (
        mode == "auto"
        or intent in {"comparison", "relationship", "visualization"}
        # An unclassified request is an unknown quantity. If the project has
        # more than one table it is more likely to need the capable model.
        or (intent == "general" and len(schema or {}) > 1)
        or len(str(query or "")) > 280
    )
    if not enabled:
        advanced = False
        reason += "; model routing disabled"

    return RouteDecision(
        intent=intent,
        allowed_tools=allowed,
        model_tier="advanced" if advanced else "standard",
        reason=reason,
    )
