import re
from collections import Counter
from datetime import datetime, timezone

from sqlalchemy import or_
from sqlalchemy.exc import SQLAlchemyError

from extensions import db
from models import AgentRun
from services.logger import get_logger


logger = get_logger(__name__)

_STOP_WORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "calculate", "can",
    "data", "database", "do", "each", "for", "from", "give", "how", "i",
    "in", "is", "it", "me", "of", "on", "please", "show", "table", "that",
    "the", "this", "to", "with",
}

_INTENT_WORDS = {
    "aggregate", "aggregated", "aggregation", "after", "average", "avg",
    "before", "between", "bottom", "chart", "compare", "comparison",
    "correlate", "correlation", "count", "distinct", "filter", "filtered",
    "group", "grouped", "highest", "join", "joined", "list", "lowest",
    "max", "maximum", "mean", "min", "minimum", "per", "rank", "ranked",
    "rows", "select", "sort", "sorted", "sum", "summarize", "summary",
    "top", "total", "trend", "unique", "where",
}

_LEARNING_EXCLUDED_TOOLS = {"verify_result"}


def _rollback():
    try:
        db.session.rollback()
    except RuntimeError:
        pass


def _schema_tokens(schema):
    tokens = set()
    for table_name, details in (schema or {}).items():
        names = [table_name]
        names.extend((details or {}).get("types", {}).keys())
        for name in names:
            tokens.update(re.findall(r"[a-z_][a-z0-9_]{1,63}", str(name).lower()))
            tokens.update(re.findall(r"[a-z][a-z0-9]{1,63}", str(name).lower()))
    return tokens


def goal_signature(goal, schema=None):
    """Retain analytical intent and schema terms, not user-entered values."""
    text = " ".join(str(goal or "").split()).lower()
    text = re.sub(r"\b[\w.+-]+@[\w.-]+\.[a-z]{2,}\b", " [value] ", text)
    text = re.sub(r"https?://\S+", " [value] ", text)
    text = re.sub(r"""(["']).*?\1""", " [value] ", text)
    text = re.sub(r"\b[-+]?\d+(?:\.\d+)?\b", " [number] ", text)
    allowed = _INTENT_WORDS | _schema_tokens(schema)
    tokens = [
        token
        for token in re.findall(r"[a-z_][a-z0-9_]{1,63}|\[(?:value|number)\]", text)
        if token not in _STOP_WORDS
        and (token in allowed or token in {"[value]", "[number]"})
    ]
    return " ".join(tokens[:80])[:800] or "unspecified goal"


def _tokens(text):
    return {
        token
        for token in re.findall(r"[a-z_][a-z0-9_]{1,63}", str(text or "").lower())
        if token not in _STOP_WORDS
    }


def _similarity(left, right):
    left_tokens = _tokens(left)
    right_tokens = _tokens(right)
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def start_run(
    request_id,
    user_id,
    project_id,
    goal,
    mode,
    model_name,
    routing_tier,
    schema=None,
):
    record = AgentRun(
        request_id=request_id,
        user_id=user_id,
        project_id=project_id,
        goal_signature=goal_signature(goal, schema),
        mode=mode,
        model_name=model_name,
        routing_tier=routing_tier,
        status="running",
        plan=[],
        trace=[],
        verification={},
    )
    try:
        db.session.add(record)
        db.session.commit()
        return record
    except (SQLAlchemyError, RuntimeError):
        _rollback()
        logger.exception("Could not persist agent run start")
        return None


def finish_run(
    record,
    *,
    status,
    plan=None,
    trace=None,
    result_kind=None,
    result_name=None,
    turns=0,
    tool_calls=0,
    duration_ms=0,
    verification=None,
    error_type=None,
):
    if record is None:
        return
    try:
        record.status = str(status)[:30]
        record.plan = [
            goal_signature(step)
            for step in list(plan or [])[:6]
            if goal_signature(step) != "unspecified goal"
        ]
        record.trace = list(trace or [])[:30]
        record.result_kind = str(result_kind)[:30] if result_kind else None
        record.result_name = str(result_name)[:100] if result_name else None
        record.turns = max(int(turns or 0), 0)
        record.tool_calls = max(int(tool_calls or 0), 0)
        record.duration_ms = max(int(duration_ms or 0), 0)
        record.verification = dict(verification or {})
        record.error_type = str(error_type)[:50] if error_type else None
        record.updated_at = datetime.now(timezone.utc)
        db.session.commit()
    except (SQLAlchemyError, RuntimeError, TypeError, ValueError):
        _rollback()
        logger.exception("Could not persist agent run completion")


def record_feedback(request_id, user_id, rating, comment=""):
    record = AgentRun.query.filter_by(
        request_id=request_id,
        user_id=user_id,
    ).first()
    if record is None:
        return None
    record.feedback_rating = rating
    record.feedback_comment = " ".join(str(comment or "").split())[:500] or None
    record.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return record


def successful_examples(user_id, project_id, goal, limit=3, schema=None):
    try:
        candidates = (
            AgentRun.query.filter_by(user_id=user_id, project_id=project_id)
            .filter(AgentRun.status.in_(["finished", "text"]))
            .filter(or_(
                AgentRun.feedback_rating.is_(None),
                AgentRun.feedback_rating != "down",
            ))
            .order_by(AgentRun.created_at.desc())
            .limit(100)
            .all()
        )
    except (SQLAlchemyError, RuntimeError):
        _rollback()
        logger.warning("Agent history is unavailable; continuing without examples")
        return []
    target = goal_signature(goal, schema)
    ranked = []
    for record in candidates:
        verification = record.verification or {}
        if verification and not verification.get("passed", False):
            continue
        if not record.plan and not record.trace:
            continue
        score = _similarity(target, record.goal_signature)
        if record.feedback_rating == "up":
            score += 0.15
        ranked.append((score, record.created_at, record))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)

    examples = []
    for score, _, record in ranked[: max(int(limit), 0)]:
        if score <= 0 and examples:
            continue
        examples.append({
            "similar_goal": record.goal_signature,
            "plan": list(record.plan or []),
            "tools": [
                item.get("tool")
                for item in (record.trace or [])
                if item.get("status") == "ok"
                and item.get("tool")
                and item.get("tool") not in _LEARNING_EXCLUDED_TOOLS
            ],
            "result_kind": record.result_kind,
        })
    return examples


def project_metrics(user_id, project_id):
    runs = AgentRun.query.filter_by(user_id=user_id, project_id=project_id).all()
    total = len(runs)
    completed = [run for run in runs if run.status in {"finished", "text"}]
    verified = [
        run
        for run in completed
        if (run.verification or {}).get("passed") is True
    ]
    rated = [run for run in runs if run.feedback_rating in {"up", "down"}]
    positive = [run for run in rated if run.feedback_rating == "up"]
    models = Counter(run.model_name or "unknown" for run in runs)
    tools = Counter(
        item.get("tool")
        for run in runs
        for item in (run.trace or [])
        if item.get("tool")
    )
    return {
        "total_runs": total,
        "completed_runs": len(completed),
        "success_rate": round(len(completed) / total, 3) if total else None,
        "verified_rate": round(len(verified) / len(completed), 3) if completed else None,
        "positive_feedback_rate": (
            round(len(positive) / len(rated), 3) if rated else None
        ),
        "average_turns": (
            round(sum(run.turns for run in runs) / total, 2) if total else None
        ),
        "average_tool_calls": (
            round(sum(run.tool_calls for run in runs) / total, 2) if total else None
        ),
        "average_duration_ms": (
            round(sum(run.duration_ms for run in runs) / total) if total else None
        ),
        "models": dict(models),
        "top_tools": [
            {"tool": tool, "uses": count}
            for tool, count in tools.most_common(5)
        ],
    }


def clear_project_history(user_id, project_id):
    deleted = AgentRun.query.filter_by(
        user_id=user_id,
        project_id=project_id,
    ).delete(synchronize_session=False)
    db.session.commit()
    return deleted
