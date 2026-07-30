import json


NUMERIC_TYPES = {"int", "float", "integer", "number", "decimal"}


def _is_identifier(column):
    normalized = column.casefold()
    return (
        normalized == "id"
        or normalized.endswith("_id")
        or normalized.startswith("id_")
        or "zip" in normalized
        or "postal" in normalized
    )


def _column_groups(details):
    types = details.get("types", {})
    numeric = [
        column
        for column, column_type in types.items()
        if str(column_type).casefold() in NUMERIC_TYPES and not _is_identifier(column)
    ]
    categorical = [
        column
        for column, column_type in types.items()
        if str(column_type).casefold() not in NUMERIC_TYPES
        and not any(token in column.casefold() for token in ("date", "time", "timestamp"))
    ]
    temporal = [
        column
        for column in types
        if any(token in column.casefold() for token in ("date", "time", "timestamp"))
    ]
    return numeric, categorical, temporal


def build_query_suggestions(schema, relationships=None, limit=6):
    """Create useful, deterministic questions from column names and types."""
    suggestions = []
    for table, details in schema.items():
        numeric, categorical, temporal = _column_groups(details)

        suggestions.append({
            "label": f"Count {table}",
            "question": f"How many rows are in {table}?",
            "reason": "Basic table size",
        })

        if numeric:
            suggestions.append({
                "label": f"Top {numeric[0]}",
                "question": (
                    f"Show the 10 rows in {table} with the highest "
                    f"{numeric[0]}."
                ),
                "reason": f"Numeric column: {numeric[0]}",
            })

        if numeric and categorical:
            suggestions.append({
                "label": f"{numeric[0]} by {categorical[0]}",
                "question": (
                    f"Calculate the total {numeric[0]} in {table}, grouped by "
                    f"{categorical[0]}."
                ),
                "reason": "Categorical and numeric columns",
            })
            suggestions.append({
                "label": f"Average {numeric[0]}",
                "question": (
                    f"Calculate the average {numeric[0]} in {table}, grouped by "
                    f"{categorical[0]}."
                ),
                "reason": "Group comparison",
            })
        elif categorical:
            suggestions.append({
                "label": f"Break down {categorical[0]}",
                "question": (
                    f"Count the rows in {table}, grouped by {categorical[0]}."
                ),
                "reason": f"Categorical column: {categorical[0]}",
            })

        if temporal and numeric:
            suggestions.append({
                "label": f"Recent high {numeric[0]}",
                "question": (
                    f"Show the highest {numeric[0]} rows in {table}, including "
                    f"{temporal[0]}."
                ),
                "reason": "Temporal and numeric columns",
            })

    for relationship in relationships or []:
        left = relationship.get("from_table")
        right = relationship.get("to_table")
        left_key = relationship.get("from_column")
        right_key = relationship.get("to_column")
        if all((left, right, left_key, right_key)):
            suggestions.append({
                "label": f"Join {left} + {right}",
                "question": (
                    f"Join {left} and {right} using {left}.{left_key} and "
                    f"{right}.{right_key}, then show 10 combined rows."
                ),
                "reason": "Detected table relationship",
            })

    unique = []
    seen = set()
    for suggestion in suggestions:
        if suggestion["question"] in seen:
            continue
        seen.add(suggestion["question"])
        unique.append(suggestion)
        if len(unique) >= limit:
            break
    return unique


def build_auto_analysis_goal(schema, relationships=None):
    suggestions = build_query_suggestions(schema, relationships, limit=8)
    profiles = {}
    for table, details in schema.items():
        numeric, categorical, temporal = _column_groups(details)
        profiles[table] = {
            "numeric": numeric,
            "categorical": categorical,
            "temporal": temporal,
        }

    return (
        "AUTONOMOUS SCHEMA EXPLORATION GOAL:\n"
        "Study the column profiles and independently choose one useful analysis. "
        "Prefer a grouped numeric aggregate when numeric and categorical columns "
        "coexist; otherwise use ranking, grouped counts, or a table count. Avoid "
        "summing identifiers. Record a plan, execute the analysis with structured "
        "tools, and finish with the most useful named result. Do not ask for "
        "clarification unless every candidate is invalid.\n\n"
        f"COLUMN PROFILES:\n{json.dumps(profiles)}\n\n"
        f"CANDIDATE QUESTIONS:\n{json.dumps(suggestions)}"
    )
