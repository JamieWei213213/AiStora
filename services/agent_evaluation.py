from dataclasses import dataclass, field

from services.agent_verifier import verify_outcome


@dataclass
class AgentEvaluationCase:
    name: str
    query: str
    expected_tools: list = field(default_factory=list)
    forbidden_tools: list = field(default_factory=list)
    expected_result_kind: str = None
    max_turns: int = None
    max_tool_calls: int = None


def _is_subsequence(expected, actual):
    iterator = iter(actual)
    return all(any(item == expected_item for item in iterator) for expected_item in expected)


def evaluate_case(case, outcome, max_output_rows=25):
    verification = verify_outcome(case.query, outcome, max_output_rows)
    actual_tools = [item.get("tool") for item in outcome.trace or []]
    checks = list(verification["checks"])

    if case.expected_tools:
        checks.append({
            "name": "expected_tool_sequence",
            "passed": _is_subsequence(case.expected_tools, actual_tools),
            "detail": (
                f"Expected tool sequence {case.expected_tools}; observed {actual_tools}."
            ),
            "severity": "required",
        })
    if case.forbidden_tools:
        used_forbidden = sorted(set(case.forbidden_tools) & set(actual_tools))
        checks.append({
            "name": "forbidden_tools",
            "passed": not used_forbidden,
            "detail": f"Forbidden tools used: {used_forbidden}.",
            "severity": "required",
        })
    if case.expected_result_kind:
        actual_kind = outcome.result.kind if outcome.result is not None else None
        checks.append({
            "name": "result_kind",
            "passed": actual_kind == case.expected_result_kind,
            "detail": (
                f"Expected result kind {case.expected_result_kind}; "
                f"observed {actual_kind}."
            ),
            "severity": "required",
        })
    if case.max_turns is not None:
        checks.append({
            "name": "turn_efficiency",
            "passed": outcome.turns <= case.max_turns,
            "detail": f"Used {outcome.turns} of {case.max_turns} allowed turns.",
            "severity": "required",
        })
    if case.max_tool_calls is not None:
        checks.append({
            "name": "tool_efficiency",
            "passed": outcome.tool_calls <= case.max_tool_calls,
            "detail": (
                f"Used {outcome.tool_calls} of {case.max_tool_calls} allowed calls."
            ),
            "severity": "required",
        })

    required = [check for check in checks if check.get("severity") == "required"]
    passed_count = sum(bool(check.get("passed")) for check in required)
    return {
        "name": case.name,
        "passed": passed_count == len(required),
        "score": round(passed_count / len(required), 3) if required else 0.0,
        "checks": checks,
    }


def evaluate_suite(cases_and_outcomes, max_output_rows=25):
    results = [
        evaluate_case(case, outcome, max_output_rows)
        for case, outcome in cases_and_outcomes
    ]
    return {
        "cases": results,
        "passed": sum(result["passed"] for result in results),
        "failed": sum(not result["passed"] for result in results),
        "average_score": (
            round(sum(result["score"] for result in results) / len(results), 3)
            if results
            else None
        ),
    }
