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
    expected_answer: object = None
    required_correction: bool = False


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
    if case.expected_answer is not None:
        actual = outcome.result.value if outcome.result is not None else outcome.message
        passed = case.expected_answer(actual) if callable(case.expected_answer) else actual == case.expected_answer
        checks.append({
            "name": "answer_accuracy",
            "passed": bool(passed),
            "detail": "Final answer matched the case oracle." if passed else "Final answer did not match the case oracle.",
            "severity": "required",
        })
    if case.required_correction:
        corrections = int((outcome.metrics or {}).get("self_corrections", 0))
        checks.append({
            "name": "self_correction_success",
            "passed": outcome.status == "finished" and corrections > 0,
            "detail": f"Observed {corrections} bounded correction attempts.",
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
    outcomes = [outcome for _, outcome in cases_and_outcomes]
    return {
        "cases": results,
        "passed": sum(result["passed"] for result in results),
        "failed": sum(not result["passed"] for result in results),
        "average_score": (
            round(sum(result["score"] for result in results) / len(results), 3)
            if results
            else None
        ),
        "metrics": {
            "plan_validity_rate": round(sum(bool(outcome.plan) for outcome in outcomes) / len(outcomes), 3) if outcomes else None,
            "execution_success_rate": round(sum(outcome.status == "finished" for outcome in outcomes) / len(outcomes), 3) if outcomes else None,
            "answer_accuracy_rate": round(sum(result["passed"] for result in results) / len(results), 3) if results else None,
            "self_correction_success_rate": round(sum(bool((outcome.metrics or {}).get("self_corrections")) and outcome.status == "finished" for outcome in outcomes) / sum(bool((outcome.metrics or {}).get("self_corrections")) for outcome in outcomes), 3) if any(bool((outcome.metrics or {}).get("self_corrections")) for outcome in outcomes) else None,
            "total_retries": sum(int((outcome.metrics or {}).get("retries", 0)) for outcome in outcomes),
            "average_latency_ms": round(sum(int((outcome.metrics or {}).get("latency_ms", 0)) for outcome in outcomes) / len(outcomes)) if outcomes else None,
            "total_tokens": sum(int((outcome.metrics or {}).get("total_tokens", 0)) for outcome in outcomes),
            "estimated_cost_usd": round(sum(float((outcome.metrics or {}).get("estimated_cost_usd", 0)) for outcome in outcomes), 6),
        },
    }
