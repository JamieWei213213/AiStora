"""Thin adapter between the request router and the agent entry point.

Kept as a separate module so callers depend on a stable dict shape rather than
on ``RouteDecision``. The previous keyword-scoring implementation that lived
here was dead code and has been removed.
"""

from services.request_router import route_request


def route_agent_task(query, schema, mode="interactive", enabled=True):
    decision = route_request(query, schema, mode=mode, enabled=enabled)
    return {
        "tier": decision.model_tier,
        "reason": decision.reason,
        "intent": decision.intent,
        "allowed_tools": list(decision.allowed_tools),
    }
