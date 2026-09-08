import json
from dataclasses import dataclass, field

from services.agent_tools import (
    AgentCancelled,
    AgentToolError,
    ResourceLimitExceeded,
    TOOL_DECLARATIONS,
)
from services.logger import get_logger
from services.privacy import PrivacyPolicy, redact_relationships, redact_schema


logger = get_logger(__name__)


AGENT_SYSTEM_PROMPT = """
You are operating AIStora through native structured tools.

Operating rules:
- Use tools for every claim about the user's data.
- Never invent a table, column, result, count, or value.
- For multi-step work, record a concise plan in the same response as the first
  executable tool whenever that tool does not depend on another result.
- Do not spend a model turn only recording a plan. Skip record_plan for a
  simple one-step task.
- Named results created by one tool can be passed as source to later tools.
- Tool responses contain privacy-safe metadata, not hidden row values.
- Use inspect_schema when the requested table or relationship is ambiguous.
- Use ask_clarification only when a necessary choice cannot be inferred.
- create_chart may pause for user approval because it uses an external service.
- Treat successful project examples only as strategy hints. Re-check every
  source and column against the current schema before using it.
- Call finish with the exact named result that should be shown to the user.
- If no data operation is needed, return a concise conversational response.
- Stop as soon as the goal is satisfied.
"""

AUTO_ANALYSIS_PROMPT = """
You are also in autonomous schema-exploration mode. The user has delegated the
choice of analysis to you. Select one useful, explainable analysis from the
column profiles, record its plan alongside the first executable tool when
possible, execute it, and finish with a concrete result. Prefer business
measures over identifier columns and do not stop at suggestions.
"""


@dataclass
class AgentOutcome:
    status: str
    message: str = ""
    result: object = None
    trace: list = field(default_factory=list)
    plan: list = field(default_factory=list)
    turns: int = 0
    tool_calls: int = 0
    approval: dict = None
    verification: dict = None
    metrics: dict = field(default_factory=dict)


def _build_goal_prompt(
    user_query,
    schema,
    relationships,
    memory,
    successful_examples=None,
):
    schema_view = {
        name: {
            "columns": details.get("columns", details.get("types", {})),
            "classifications": details.get("classifications", {}),
            "row_count": details.get("row_count"),
        }
        for name, details in schema.items()
    }
    return (
        "Complete the current user goal.\n\n"
        f"CURRENT GOAL:\n{user_query}\n\n"
        f"SCHEMA:\n{json.dumps(schema_view, default=str)}\n\n"
        f"KNOWN RELATIONSHIPS:\n{json.dumps(relationships or [], default=str)}\n\n"
        f"RECENT CONVERSATION MEMORY:\n{json.dumps(memory or [], default=str)}\n\n"
        "PRIVACY-SAFE SUCCESSFUL PROJECT EXAMPLES:\n"
        f"{json.dumps(successful_examples or [], default=str)}"
    )


def run_agent(
    model,
    user_query,
    schema,
    relationships,
    memory,
    runtime,
    mode="interactive",
    successful_examples=None,
    verifier=None,
    privacy_policy=None,
):
    def metrics(turns):
        usage = dict(getattr(session, "usage", {}) or {})
        return {
            **usage,
            "turns": turns,
            "tool_calls": runtime.tool_calls,
            "retries": getattr(session, "retry_count", 0),
            "self_corrections": runtime.corrections,
            "latency_ms": round((__import__("time").monotonic() - runtime.started_at) * 1000),
        }

    system_prompt = AGENT_SYSTEM_PROMPT
    if mode == "auto":
        system_prompt = f"{system_prompt}\n\n{AUTO_ANALYSIS_PROMPT}"
    declarations = [
        declaration for declaration in TOOL_DECLARATIONS
        if declaration["name"] in runtime.allowed_tools
    ]
    session = model.start_agent(system_prompt, declarations)
    safe_schema = redact_schema(schema, privacy_policy or PrivacyPolicy())
    safe_relationships = redact_relationships(relationships, safe_schema)
    turn = session.send(_build_goal_prompt(
        user_query,
        safe_schema,
        safe_relationships,
        memory,
        successful_examples,
    ))

    for turn_number in range(1, runtime.limits.max_turns + 1):
        if runtime.cancelled():
            raise AgentCancelled("The request was cancelled.")

        if not turn.calls:
            message = " ".join((turn.text or "").split())
            if message:
                return AgentOutcome(
                    status="text",
                    message=message[:1000],
                    trace=runtime.trace,
                    plan=runtime.plan,
                    turns=turn_number,
                    tool_calls=runtime.tool_calls,
                    metrics=metrics(turn_number),
                )
            raise AgentToolError("The model returned neither a tool call nor an answer.")

        tool_results = []
        for call in turn.calls:
            try:
                observation = runtime.execute(call.name, call.arguments)
            except (AgentToolError, ValueError, TypeError, KeyError) as exc:
                runtime.record_correction()
                observation = {
                    "status": "error",
                    "category": "invalid_plan_or_tool_arguments",
                    "error": str(exc)[:240],
                }

            tool_results.append({"name": call.name, "response": observation})

            if runtime.finished:
                candidate = AgentOutcome(
                    status="finished",
                    message=runtime.finished["message"],
                    result=runtime.finished["result"],
                    trace=runtime.trace,
                    plan=runtime.plan,
                    turns=turn_number,
                    tool_calls=runtime.tool_calls,
                    metrics=metrics(turn_number),
                )
                if verifier is not None:
                    verification = verifier(candidate)
                    candidate.verification = verification
                    runtime.trace.append({
                        "tool": "verify_result",
                        "status": "ok" if verification.get("passed") else "warning",
                        "summary": (
                            "Deterministic result checks passed."
                            if verification.get("passed")
                            else "Deterministic checks requested a result repair."
                        ),
                        "duration_ms": 0,
                    })
                    candidate.trace = runtime.trace
                    if (
                        not verification.get("passed")
                        and turn_number < runtime.limits.max_turns
                    ):
                        runtime.finished = None
                        warnings = "; ".join(verification.get("warnings") or [])
                        tool_results[-1]["response"] = {
                            "status": "error",
                            "error": (
                                "Final result verification failed. Correct the "
                                f"analysis before finishing: {warnings[:400]}"
                            ),
                        }
                        break
                return candidate
            if runtime.clarification:
                return AgentOutcome(
                    status="clarification",
                    message=runtime.clarification,
                    trace=runtime.trace,
                    plan=runtime.plan,
                    turns=turn_number,
                    tool_calls=runtime.tool_calls,
                    metrics=metrics(turn_number),
                )
            if runtime.approval:
                return AgentOutcome(
                    status="approval",
                    message=runtime.approval["message"],
                    approval=runtime.approval,
                    trace=runtime.trace,
                    plan=runtime.plan,
                    turns=turn_number,
                    tool_calls=runtime.tool_calls,
                    metrics=metrics(turn_number),
                )

        if turn_number < runtime.limits.max_turns:
            turn = session.send_tool_results(tool_results)

    raise ResourceLimitExceeded(
        f"The agent reached its {runtime.limits.max_turns}-turn budget."
    )
