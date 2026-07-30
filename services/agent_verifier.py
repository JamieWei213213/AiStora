import math
import re


_DATA_INTENT_WORDS = {
    "average", "avg", "count", "data", "filter", "group", "highest", "join",
    "lowest", "maximum", "minimum", "rows", "show", "sum", "table", "top",
    "total",
}


def _check(name, passed, detail, severity="required"):
    return {
        "name": name,
        "passed": bool(passed),
        "detail": detail,
        "severity": severity,
    }


def _query_words(query):
    return set(re.findall(r"[a-z]+", str(query or "").lower().replace("_", " ")))


def _result_size(result):
    if result is None:
        return 0
    if result.kind == "table":
        return len(result.value)
    if result.kind == "dataframe":
        return result.metadata.get("rows")
    if result.kind == "aggregate":
        return len(result.value)
    return 1


def verify_outcome(query, outcome, max_output_rows=25):
    """Deterministic checks only; never asks another model to grade itself."""
    words = _query_words(query)
    tools = [item.get("tool") for item in outcome.trace or []]
    checks = [
        _check(
            "tool_execution",
            not any(item.get("status") == "error" for item in outcome.trace or []),
            "No local tool ended in an error.",
        ),
    ]

    if outcome.status == "finished":
        checks.extend([
            _check(
                "finish_selected",
                "finish" in tools,
                "The agent explicitly selected its final result.",
            ),
            _check(
                "named_result",
                outcome.result is not None,
                "A named local result was selected.",
            ),
        ])
        if outcome.result is not None:
            size = _result_size(outcome.result)
            checks.append(_check(
                "bounded_output",
                size is None or size <= max_output_rows
                or outcome.result.kind in {"dataframe", "aggregate"},
                "The rendered result respects the configured output bound.",
            ))
            if outcome.result.kind in {"table", "dataframe", "aggregate"}:
                checks.append(_check(
                    "columns_present",
                    bool(outcome.result.columns),
                    "The selected result has defined columns.",
                ))

            expected_operation = None
            if {"average", "avg", "mean"} & words:
                expected_operation = "avg"
            elif {"total", "sum"} & words:
                expected_operation = "sum"
            elif {"minimum", "min"} & words:
                expected_operation = "min"
            elif {"maximum", "max"} & words:
                expected_operation = "max"
            if expected_operation and outcome.result.kind == "aggregate":
                actual = outcome.result.metadata.get("operation")
                checks.append(_check(
                    "aggregate_operation",
                    actual == expected_operation,
                    f"Requested {expected_operation}; selected {actual or 'unknown'}.",
                ))

            if outcome.result.kind == "number":
                value = outcome.result.value
                numeric = isinstance(value, (int, float)) and not isinstance(value, bool)
                checks.append(_check(
                    "finite_number",
                    numeric and math.isfinite(float(value)),
                    "The scalar result is a finite number.",
                ))
    elif outcome.status == "text":
        data_intent = bool(words & _DATA_INTENT_WORDS)
        checks.append(_check(
            "text_is_appropriate",
            not data_intent,
            (
                "A conversational answer is appropriate."
                if not data_intent
                else "The request appears data-related but no local result was selected."
            ),
        ))

    required = [item for item in checks if item["severity"] == "required"]
    passed_count = sum(item["passed"] for item in required)
    passed = bool(required) and passed_count == len(required)
    warnings = [
        item["detail"]
        for item in checks
        if not item["passed"]
    ]
    return {
        "passed": passed,
        "score": round(passed_count / len(required), 3) if required else 0.0,
        "checks": checks,
        "warnings": warnings,
        "method": "deterministic",
    }
