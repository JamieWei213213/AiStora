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


# Column-name hints. Types alone cannot tell a revenue column from an age
# column or a status column from a free-text note, and the old picker took
# the first column of each type, which produced "total age grouped by
# first_name". Names are ranked instead; unknown names keep a neutral score.
_STRONG_MEASURE_HINTS = (
    "total", "amount", "revenue", "sales", "price", "spend", "profit",
    "income", "salary", "value", "balance", "gmv", "turnover",
)
_MEASURE_HINTS = (
    "cost", "quantity", "qty", "fee", "units", "volume", "score", "rating",
    "duration", "hours", "minutes", "count", "weight", "distance", "discount",
    "tax", "margin", "clicks", "views", "visits",
)
# Averages make sense for these; totals do not ("total age").
_WEAK_MEASURE_HINTS = ("age", "year", "month", "day", "week", "pct", "percent", "rate", "ratio", "temperature", "height", "bmi")
_NOT_MEASURE_HINTS = ("code", "number", "phone", "zip", "postal", "lat", "lon", "longitude", "latitude", "ssn")
_DIMENSION_HINTS = (
    "status", "category", "type", "segment", "channel", "region", "state",
    "country", "city", "gender", "department", "class", "tier", "plan",
    "source", "method", "kind", "group", "brand", "product", "team", "stage",
    "priority", "level", "industry", "role", "market", "store", "branch",
)
_NOT_DIMENSION_HINTS = (
    "name", "email", "address", "description", "comment", "note", "url",
    "title", "phone", "id", "uuid", "hash", "token", "password",
)
_TEMPORAL_HINTS = ("date", "time", "timestamp", "_at", "_on", "created", "updated", "year", "month")


def _measure_score(column):
    name = column.casefold()
    if _is_identifier(column) or any(token in name for token in _NOT_MEASURE_HINTS):
        return None
    if any(token in name for token in _STRONG_MEASURE_HINTS):
        return 4
    if any(token in name for token in _MEASURE_HINTS):
        return 3
    if any(token in name for token in _WEAK_MEASURE_HINTS):
        return 1
    return 2


def _dimension_score(column):
    name = column.casefold()
    if _is_identifier(column) or any(token in name for token in _NOT_DIMENSION_HINTS):
        return None
    if any(token in name for token in _DIMENSION_HINTS):
        return 3
    return 2


def _is_temporal(column):
    name = column.casefold()
    return any(token in name for token in _TEMPORAL_HINTS)


def _column_groups(details):
    """Return (numeric, categorical, temporal) column lists, best first."""
    types = details.get("types", {})
    numeric, categorical, temporal = [], [], []
    for column, column_type in types.items():
        kind = str(column_type).casefold()
        if _is_temporal(column) and kind not in NUMERIC_TYPES:
            temporal.append(column)
            continue
        if kind in NUMERIC_TYPES:
            score = _measure_score(column)
            if score is not None:
                numeric.append((score, column))
        else:
            score = _dimension_score(column)
            if score is not None:
                categorical.append((score, column))
    numeric = [column for _, column in sorted(numeric, key=lambda item: -item[0])]
    categorical = [column for _, column in sorted(categorical, key=lambda item: -item[0])]
    return numeric, categorical, temporal


def _table_suggestions(table, details):
    numeric, categorical, temporal = _column_groups(details)
    items = []
    # "total age" is meaningless; averages suit rates, ages and percentages.
    aggregate = "average" if numeric and _measure_score(numeric[0]) == 1 else "total"
    if numeric and categorical:
        items.append({
            "label": f"{numeric[0]} by {categorical[0]}",
            "question": f"Calculate the {aggregate} {numeric[0]} in {table}, grouped by {categorical[0]}.",
            "reason": "Categorical and numeric columns",
        })
    if numeric:
        items.append({
            "label": f"Top {numeric[0]}",
            "question": f"Show the 10 rows in {table} with the highest {numeric[0]}.",
            "reason": f"Numeric column: {numeric[0]}",
        })
    if categorical:
        items.append({
            "label": f"Break down {categorical[0]}",
            "question": f"Count the rows in {table}, grouped by {categorical[0]}.",
            "reason": f"Categorical column: {categorical[0]}",
        })
    if numeric and categorical:
        second = categorical[1] if len(categorical) > 1 else categorical[0]
        items.append({
            "label": f"Average {numeric[0]}",
            "question": f"Calculate the average {numeric[0]} in {table}, grouped by {second}.",
            "reason": "Group comparison",
        })
    if temporal and numeric:
        items.append({
            "label": f"{numeric[0]} over time",
            "question": f"Show the {aggregate} {numeric[0]} in {table} by month of {temporal[0]}.",
            "reason": "Temporal and numeric columns",
        })
    elif temporal:
        items.append({
            "label": f"{table} over time",
            "question": f"Count the rows in {table} by month of {temporal[0]}.",
            "reason": f"Temporal column: {temporal[0]}",
        })
    items.append({
        "label": f"Count {table}",
        "question": f"How many rows are in {table}?",
        "reason": "Basic table size",
    })
    return items


def build_query_suggestions(schema, relationships=None, limit=6):
    """Create useful, deterministic questions from column names and types.

    Tables take turns (round robin) so a database with several tables does
    not fill every slot with questions about the first one, and the most
    analytical question for each table comes first.
    """
    per_table = [_table_suggestions(table, details) for table, details in schema.items()]
    suggestions = []
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
    round_index = 0
    while any(len(items) > round_index for items in per_table):
        for items in per_table:
            if len(items) > round_index:
                suggestions.append(items[round_index])
        round_index += 1

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
