import re


_ADVANCED_TERMS = {
    "across", "chart", "compare", "correlation", "difference", "join",
    "relationship", "relationships", "trend", "trends", "versus",
}

_MULTI_STEP_TERMS = {
    "after", "before", "filter", "group", "having", "then", "where",
}


def route_agent_task(query, schema, mode="interactive", enabled=True):
    if not enabled:
        return {
            "tier": "standard",
            "reason": "Model routing is disabled.",
        }

    words = set(re.findall(r"[a-z]+", str(query or "").lower().replace("_", " ")))
    reasons = []
    if mode == "auto":
        reasons.append("autonomous schema exploration")
    if len(schema or {}) > 1 and words & _ADVANCED_TERMS:
        reasons.append("cross-table or comparative request")
    if words & _ADVANCED_TERMS:
        reasons.append("advanced analytical intent")
    if len(words & _MULTI_STEP_TERMS) >= 2:
        reasons.append("multi-step analytical intent")
    if len(str(query or "")) > 280:
        reasons.append("long task description")

    return {
        "tier": "advanced" if reasons else "standard",
        "reason": ", ".join(dict.fromkeys(reasons)) if reasons else "simple analytical request",
    }
