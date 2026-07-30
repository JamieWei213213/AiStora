import json
import math
import time
import uuid

from flask import Blueprint, jsonify, request, session

from config import Config
from services.agent_control import cancellation_registry
from services.agent_history import (
    clear_project_history,
    finish_run,
    project_metrics,
    record_feedback,
    start_run,
    successful_examples,
)
from services.agent_memory import add_memory, clear_memory, get_memory
from services.agent_service import run_agent
from services.agent_tools import (
    AgentCancelled,
    AgentLimits,
    AgentToolRuntime,
    ResourceLimitExceeded,
)
from services.llm_service import (
    GEMINI_QUOTA_MESSAGE,
    get_model,
    is_gemini_quota_error,
)
from services.logger import get_logger
from services.model_router import route_agent_task
from services.schema_agent import build_auto_analysis_goal, build_query_suggestions
from services.state_manager import get_dataframe
from services.agent_verifier import verify_outcome


chat_bp = Blueprint("chat", __name__)
logger = get_logger(__name__)


def _display_value(value):
    if isinstance(value, float) and math.isfinite(value):
        return round(value, 4)
    return value


def _clean_json(text):
    cleaned = text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return json.loads(cleaned.strip())


def _base_payload(
    outcome,
    request_id,
    agent_meta=None,
    verification=None,
):
    payload = {
        "request_id": request_id,
        "message": outcome.message,
        "trace": outcome.trace,
        "plan": outcome.plan,
        "budget": {
            "turns_used": outcome.turns,
            "tool_calls_used": outcome.tool_calls,
        },
    }
    if agent_meta:
        payload["agent"] = agent_meta
    if verification:
        payload["verification"] = verification
    return payload


def _format_finished(
    outcome,
    request_id,
    max_rows,
    agent_meta=None,
    verification=None,
):
    payload = _base_payload(
        outcome,
        request_id,
        agent_meta=agent_meta,
        verification=verification,
    )
    result = outcome.result
    if result is None:
        payload.update({"type": "text", "data": outcome.message})
        return payload

    payload["result_name"] = result.name
    if result.kind == "number":
        payload.update({"type": "count", "data": result.value})
    elif result.kind == "chart":
        payload.update({"type": "chart", "data": result.value})
    elif result.kind == "table":
        payload.update({
            "type": "table",
            "data": [
                {key: _display_value(value) for key, value in row.items()}
                for row in result.value[:max_rows]
            ],
        })
    elif result.kind == "dataframe":
        rows = []
        for row in result.value._get_data():
            rows.append({
                column: _display_value(row.get(column))
                for column in result.columns
            })
            if len(rows) >= max_rows:
                break
        payload.update({"type": "table", "data": rows})
    elif result.kind == "aggregate":
        group_by = result.metadata.get("group_by", "group")
        rows = []
        for group, metrics in result.value.items():
            row = {group_by: _display_value(group)}
            row.update({
                key: _display_value(value)
                for key, value in metrics.items()
            })
            rows.append(row)
            if len(rows) >= max_rows:
                break
        payload.update({"type": "table", "data": rows})
    else:
        payload.update({"type": "text", "data": outcome.message})
    return payload


@chat_bp.route("/api/detect-relationships", methods=["POST"])
def detect_relationships():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401

    model = get_model("advanced")
    if not model:
        return jsonify({"success": False, "error": "AI model not configured"}), 500

    schema = session.get("db_schema", {})
    if len(schema) < 2:
        return jsonify({"success": False, "error": "At least two tables are required."}), 400

    prompt_schema = {name: details["types"] for name, details in schema.items()}
    prompt = f"""
    Given this database schema:
    {json.dumps(prompt_schema)}
    Infer likely foreign-key relationships. Return only JSON:
    {{"success":true,"relationships":[{{"from_table":"t1","from_column":"c1","to_table":"t2","to_column":"c2"}}]}}
    """
    try:
        result = _clean_json(model.generate_content(prompt).text)
        session["db_relationships"] = result.get("relationships", [])
        logger.info("Relationships detected: %s", len(result.get("relationships", [])))
        return jsonify(result)
    except Exception as exc:
        if is_gemini_quota_error(exc):
            logger.warning("Gemini quota exhausted during relationship detection")
            return jsonify({
                "success": False,
                "error": GEMINI_QUOTA_MESSAGE,
                "error_type": "quota",
            }), 429
        logger.error("Gemini relationship detection failed: %s", exc)
        return jsonify({"success": False, "error": f"AI API error: {exc}"}), 500


@chat_bp.route("/api/chat", methods=["POST"])
def chat():
    if "user_id" not in session:
        return jsonify({"type": "error", "data": "Unauthorized."}), 401

    body = request.get_json(silent=True) or {}
    auto_analyze = body.get("auto_analyze") is True
    user_query = body.get("query")
    if auto_analyze and not isinstance(user_query, str):
        user_query = "Auto-analyze this database"
    if not isinstance(user_query, str) or not user_query.strip():
        return jsonify({"type": "error", "data": "Please enter a question."}), 400

    schema = session.get("db_schema", {})
    project_id = session.get("active_project_id")
    if not schema or not project_id:
        return jsonify({"type": "error", "data": "No active database schema found."}), 400

    try:
        request_id = str(uuid.UUID(str(body.get("request_id") or uuid.uuid4())))
    except (ValueError, TypeError, AttributeError):
        return jsonify({"type": "error", "data": "request_id must be a UUID."}), 400
    approvals = body.get("approvals") or []
    pending = session.pop("pending_agent", None)
    memory_user_text = user_query.strip()
    agent_mode = "auto" if auto_analyze else "interactive"
    effective_query = (
        build_auto_analysis_goal(schema, session.get("db_relationships", []))
        if auto_analyze
        else user_query.strip()
    )
    if pending and pending.get("type") == "clarification":
        agent_mode = pending.get("mode", agent_mode)
        effective_query = (
            f"Original goal: {pending['goal']}\n"
            f"Clarification asked: {pending['message']}\n"
            f"User answer: {effective_query}"
        )
    elif pending and pending.get("type") == "approval" and approvals:
        effective_query = pending["goal"]
        memory_user_text = pending.get("display_query", memory_user_text)
        agent_mode = pending.get("mode", agent_mode)

    routing = route_agent_task(
        effective_query,
        schema,
        mode=agent_mode,
        enabled=bool(getattr(Config, "AGENT_MODEL_ROUTING", True)),
    )
    model = get_model(routing["tier"])
    if not model:
        return jsonify({"type": "error", "data": "AI model not configured."}), 500

    examples = successful_examples(
        session["user_id"],
        project_id,
        effective_query,
        limit=int(getattr(Config, "AGENT_HISTORY_EXAMPLES", 3)),
        schema=schema,
    )
    agent_meta = {
        "model": getattr(model, "model_name", "configured-model"),
        "routing_tier": routing["tier"],
        "routing_reason": routing["reason"],
        "examples_used": len(examples),
    }

    configured_turns = int(getattr(Config, "AGENT_MAX_TURNS", 8))
    requested_turns = body.get("max_turns", configured_turns)
    try:
        max_turns = min(max(int(requested_turns), 2), configured_turns)
    except (ValueError, TypeError):
        max_turns = configured_turns

    limits = AgentLimits(
        max_turns=max_turns,
        max_tool_calls=int(getattr(Config, "AGENT_MAX_TOOL_CALLS", 12)),
        max_output_rows=int(getattr(Config, "AGENT_MAX_OUTPUT_ROWS", 25)),
        max_materialized_rows=int(getattr(Config, "AGENT_MAX_MATERIALIZED_ROWS", 25_000)),
        max_groups=int(getattr(Config, "AGENT_MAX_GROUPS", 500)),
        timeout_seconds=int(getattr(Config, "AGENT_TIMEOUT_SECONDS", 45)),
    )
    cancel_event = cancellation_registry.register(request_id)
    runtime = AgentToolRuntime(
        schema=schema,
        relationships=session.get("db_relationships", []),
        table_loader=get_dataframe,
        request_id=request_id,
        user_id=session["user_id"],
        approvals=approvals,
        limits=limits,
        cancelled=cancel_event.is_set,
    )
    run_started = time.monotonic()
    run_record = start_run(
        request_id=request_id,
        user_id=session["user_id"],
        project_id=project_id,
        goal=effective_query,
        mode=agent_mode,
        model_name=agent_meta["model"],
        routing_tier=routing["tier"],
        schema=schema,
    )

    try:
        outcome = run_agent(
            model=model,
            user_query=effective_query,
            schema=schema,
            relationships=session.get("db_relationships", []),
            memory=get_memory(session, project_id),
            runtime=runtime,
            mode=agent_mode,
            successful_examples=examples,
            verifier=lambda candidate: verify_outcome(
                memory_user_text,
                candidate,
                limits.max_output_rows,
            ),
        )

        if outcome.status == "clarification":
            session["pending_agent"] = {
                "type": "clarification",
                "goal": effective_query,
                "display_query": memory_user_text,
                "mode": agent_mode,
                "message": outcome.message,
            }
            finish_run(
                run_record,
                status="clarification",
                plan=outcome.plan,
                trace=outcome.trace,
                turns=outcome.turns,
                tool_calls=outcome.tool_calls,
                duration_ms=round((time.monotonic() - run_started) * 1000),
            )
            payload = _base_payload(
                outcome,
                request_id,
                agent_meta=agent_meta,
            )
            payload.update({"type": "clarification", "data": outcome.message})
            return jsonify(payload)

        if outcome.status == "approval":
            session["pending_agent"] = {
                "type": "approval",
                "goal": effective_query,
                "display_query": memory_user_text,
                "mode": agent_mode,
                "message": outcome.message,
            }
            finish_run(
                run_record,
                status="approval",
                plan=outcome.plan,
                trace=outcome.trace,
                turns=outcome.turns,
                tool_calls=outcome.tool_calls,
                duration_ms=round((time.monotonic() - run_started) * 1000),
            )
            payload = _base_payload(
                outcome,
                request_id,
                agent_meta=agent_meta,
            )
            payload.update({
                "type": "approval",
                "data": outcome.message,
                "approval": outcome.approval,
                "original_query": memory_user_text,
                "auto_analyze": agent_mode == "auto",
            })
            return jsonify(payload)

        if outcome.status == "text":
            verification = verify_outcome(
                memory_user_text,
                outcome,
                limits.max_output_rows,
            )
            finish_run(
                run_record,
                status="text",
                plan=outcome.plan,
                trace=outcome.trace,
                result_kind="text",
                turns=outcome.turns,
                tool_calls=outcome.tool_calls,
                duration_ms=round((time.monotonic() - run_started) * 1000),
                verification=verification,
            )
            payload = _base_payload(
                outcome,
                request_id,
                agent_meta=agent_meta,
                verification=verification,
            )
            payload.update({"type": "text", "data": outcome.message})
            add_memory(session, project_id, memory_user_text, outcome.message)
            return jsonify(payload)

        verification = outcome.verification or verify_outcome(
            memory_user_text,
            outcome,
            limits.max_output_rows,
        )
        payload = _format_finished(
            outcome,
            request_id,
            limits.max_output_rows,
            agent_meta=agent_meta,
            verification=verification,
        )
        result_kind = outcome.result.kind if outcome.result else "text"
        result_name = outcome.result.name if outcome.result else "none"
        finish_run(
            run_record,
            status="finished",
            plan=outcome.plan,
            trace=outcome.trace,
            result_kind=result_kind,
            result_name=result_name,
            turns=outcome.turns,
            tool_calls=outcome.tool_calls,
            duration_ms=round((time.monotonic() - run_started) * 1000),
            verification=verification,
        )
        add_memory(
            session,
            project_id,
            memory_user_text,
            f"Completed with named {result_kind} result '{result_name}'. {outcome.message}",
        )
        return jsonify(payload)
    except AgentCancelled as exc:
        finish_run(
            run_record,
            status="cancelled",
            plan=runtime.plan,
            trace=runtime.trace,
            tool_calls=runtime.tool_calls,
            duration_ms=round((time.monotonic() - run_started) * 1000),
            error_type="cancelled",
        )
        return jsonify({
            "type": "cancelled",
            "data": str(exc),
            "request_id": request_id,
            "trace": runtime.trace,
            "plan": runtime.plan,
            "agent": agent_meta,
        }), 409
    except ResourceLimitExceeded as exc:
        logger.warning("Agent resource limit: %s", exc)
        finish_run(
            run_record,
            status="resource_limit",
            plan=runtime.plan,
            trace=runtime.trace,
            tool_calls=runtime.tool_calls,
            duration_ms=round((time.monotonic() - run_started) * 1000),
            error_type="resource_limit",
        )
        return jsonify({
            "type": "error",
            "data": str(exc),
            "request_id": request_id,
            "trace": runtime.trace,
            "plan": runtime.plan,
            "agent": agent_meta,
        }), 429
    except Exception as exc:
        if is_gemini_quota_error(exc):
            logger.warning(
                "Gemini quota exhausted for agent request %s after %s tool calls",
                request_id,
                runtime.tool_calls,
            )
            finish_run(
                run_record,
                status="quota",
                plan=runtime.plan,
                trace=runtime.trace,
                tool_calls=runtime.tool_calls,
                duration_ms=round((time.monotonic() - run_started) * 1000),
                error_type="quota",
            )
            return jsonify({
                "type": "quota",
                "data": GEMINI_QUOTA_MESSAGE,
                "request_id": request_id,
                "trace": runtime.trace,
                "plan": runtime.plan,
                "agent": agent_meta,
            }), 429
        logger.exception("Agent chat failed")
        finish_run(
            run_record,
            status="error",
            plan=runtime.plan,
            trace=runtime.trace,
            tool_calls=runtime.tool_calls,
            duration_ms=round((time.monotonic() - run_started) * 1000),
            error_type=type(exc).__name__,
        )
        return jsonify({
            "type": "error",
            "data": f"Agent error: {str(exc)[:300]}",
            "request_id": request_id,
            "trace": runtime.trace,
            "plan": runtime.plan,
            "agent": agent_meta,
        }), 500
    finally:
        cancellation_registry.clear(request_id)


@chat_bp.route("/api/chat/suggestions", methods=["GET"])
def chat_suggestions():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    schema = session.get("db_schema", {})
    if not schema:
        return jsonify({"success": True, "suggestions": []})
    suggestions = build_query_suggestions(
        schema,
        session.get("db_relationships", []),
    )
    return jsonify({"success": True, "suggestions": suggestions})


@chat_bp.route("/api/chat/cancel", methods=["POST"])
def cancel_chat():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    request_id = (request.get_json(silent=True) or {}).get("request_id")
    try:
        request_id = str(uuid.UUID(str(request_id)))
    except (ValueError, TypeError, AttributeError):
        return jsonify({"success": False, "error": "request_id must be a UUID"}), 400
    return jsonify({"success": cancellation_registry.cancel(request_id)})


@chat_bp.route("/api/chat/memory", methods=["DELETE"])
def delete_chat_memory():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    project_id = session.get("active_project_id")
    if project_id:
        clear_memory(session, project_id)
    return jsonify({"success": True})


@chat_bp.route("/api/chat/feedback", methods=["POST"])
def chat_feedback():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    try:
        request_id = str(uuid.UUID(str(body.get("request_id"))))
    except (ValueError, TypeError, AttributeError):
        return jsonify({"success": False, "error": "request_id must be a UUID"}), 400
    rating = body.get("rating")
    if rating not in {"up", "down"}:
        return jsonify({"success": False, "error": "rating must be up or down"}), 400
    try:
        record = record_feedback(
            request_id,
            session["user_id"],
            rating,
            body.get("comment", ""),
        )
    except Exception:
        logger.exception("Could not save agent feedback")
        return jsonify({"success": False, "error": "Feedback could not be saved"}), 500
    if record is None:
        return jsonify({"success": False, "error": "Agent run not found"}), 404
    return jsonify({"success": True, "rating": rating})


@chat_bp.route("/api/chat/metrics", methods=["GET"])
def chat_metrics():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    project_id = session.get("active_project_id")
    if not project_id:
        return jsonify({"success": False, "error": "No database selected"}), 400
    return jsonify({
        "success": True,
        "metrics": project_metrics(session["user_id"], project_id),
    })


@chat_bp.route("/api/chat/history", methods=["DELETE"])
def delete_chat_history():
    if "user_id" not in session:
        return jsonify({"success": False, "error": "Unauthorized"}), 401
    project_id = session.get("active_project_id")
    if not project_id:
        return jsonify({"success": False, "error": "No database selected"}), 400
    deleted = clear_project_history(session["user_id"], project_id)
    return jsonify({"success": True, "deleted": deleted})
