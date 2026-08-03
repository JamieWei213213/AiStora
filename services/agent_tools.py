import heapq
import math
import re
import time
from dataclasses import dataclass, field

from engine.dataframe import DataFrame
from services.agent_audit import AgentAuditLogger
from services.chart_builder import build_chart_url


class AgentToolError(Exception):
    pass


class ResourceLimitExceeded(AgentToolError):
    pass


class AgentCancelled(AgentToolError):
    pass


@dataclass
class AgentLimits:
    max_turns: int = 8
    max_tool_calls: int = 12
    max_output_rows: int = 25
    max_materialized_rows: int = 25_000
    max_groups: int = 500
    timeout_seconds: int = 45


@dataclass
class StoredResult:
    name: str
    kind: str
    value: object
    columns: list = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


TOOL_DECLARATIONS = [
    {
        "name": "record_plan",
        "description": "Record a short plan before a multi-step analysis.",
        "parameters": {
            "type": "object",
            "properties": {
                "steps": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["steps"],
        },
    },
    {
        "name": "inspect_schema",
        "description": "Inspect available tables, columns, types, relationships, and named results.",
        "parameters": {
            "type": "object",
            "properties": {
                "table": {"type": "string", "description": "Optional table name."},
            },
        },
    },
    {
        "name": "count_rows",
        "description": "Count rows in a table or named intermediate result.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "save_as": {"type": "string"},
            },
            "required": ["source", "save_as"],
        },
    },
    {
        "name": "filter_rows",
        "description": "Filter rows and save a reusable named DataFrame result.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "column": {"type": "string"},
                "operator": {
                    "type": "string",
                    "enum": [
                        "eq", "neq", "gt", "gte", "lt", "lte", "contains",
                        "starts_with", "ends_with", "is_null", "not_null",
                    ],
                },
                "value": {
                    "type": "string",
                    "description": "Comparison value as text; omit for null operators.",
                },
                "save_as": {"type": "string"},
            },
            "required": ["source", "column", "operator", "save_as"],
        },
    },
    {
        "name": "select_columns",
        "description": "Select columns from a source, limit output rows, and save the table.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25},
                "save_as": {"type": "string"},
            },
            "required": ["source", "columns", "save_as"],
        },
    },
    {
        "name": "join_sources",
        "description": "Inner-join two DataFrame sources and save a reusable named result.",
        "parameters": {
            "type": "object",
            "properties": {
                "left": {"type": "string"},
                "right": {"type": "string"},
                "left_column": {"type": "string"},
                "right_column": {"type": "string"},
                "save_as": {"type": "string"},
            },
            "required": ["left", "right", "left_column", "right_column", "save_as"],
        },
    },
    {
        "name": "aggregate_rows",
        "description": "Group a DataFrame and calculate count, sum, average, minimum, or maximum.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "group_by": {"type": "string"},
                "value_column": {"type": "string"},
                "operation": {
                    "type": "string",
                    "enum": ["count", "sum", "avg", "min", "max"],
                },
                "save_as": {"type": "string"},
            },
            "required": ["source", "group_by", "value_column", "operation", "save_as"],
        },
    },
    {
        "name": "top_rows",
        "description": "Select the highest or lowest rows by a numeric column and save them.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "sort_column": {"type": "string"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 25},
                "descending": {"type": "boolean"},
                "save_as": {"type": "string"},
            },
            "required": ["source", "sort_column", "limit", "descending", "save_as"],
        },
    },
    {
        "name": "create_chart",
        "description": "Create an external QuickChart image from an aggregate result. Requires approval.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "title": {"type": "string"},
                "chart_type": {"type": "string", "enum": ["bar", "line", "pie"]},
                "save_as": {"type": "string"},
            },
            "required": ["source", "title", "chart_type", "save_as"],
        },
    },
    {
        "name": "ask_clarification",
        "description": "Pause and ask the user one necessary clarification question.",
        "parameters": {
            "type": "object",
            "properties": {"question": {"type": "string"}},
            "required": ["question"],
        },
    },
    {
        "name": "finish",
        "description": "Finish the task using a named result, or omit source for a text-only answer.",
        "parameters": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "message": {"type": "string"},
            },
            "required": ["message"],
        },
    },
]


def _as_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def _privacy_metadata(result):
    metadata = {"status": "ok", "name": result.name, "kind": result.kind}
    if result.columns:
        metadata["columns"] = result.columns
    if result.kind in {"dataframe", "table"}:
        metadata["rows"] = len(result.value) if result.kind == "table" else result.metadata.get("rows")
    elif result.kind == "aggregate":
        metadata["groups"] = len(result.value)
        metadata["group_by"] = result.metadata.get("group_by")
        metadata["metric"] = result.metadata.get("metric")
    return {key: value for key, value in metadata.items() if value is not None}


class AgentToolRuntime:
    def __init__(
        self,
        schema,
        relationships,
        table_loader,
        request_id,
        user_id,
        approvals=None,
        limits=None,
        cancelled=None,
        audit=None,
    ):
        self.schema = schema
        self.relationships = relationships or []
        self.table_loader = table_loader
        self.request_id = request_id
        self.user_id = user_id
        self.approvals = set(approvals or [])
        self.limits = limits or AgentLimits()
        self.cancelled = cancelled or (lambda: False)
        self.audit = audit or AgentAuditLogger()
        self.started_at = time.monotonic()
        self.results = {}
        self.trace = []
        self.plan = []
        self.tool_calls = 0
        self.finished = None
        self.clarification = None
        self.approval = None

    def _check_budget(self):
        self._check_running()
        if self.tool_calls >= self.limits.max_tool_calls:
            raise ResourceLimitExceeded("The agent exceeded its tool-call budget.")

    def _check_running(self):
        if self.cancelled():
            raise AgentCancelled("The request was cancelled.")
        if time.monotonic() - self.started_at > self.limits.timeout_seconds:
            raise ResourceLimitExceeded("The agent exceeded its time budget.")

    def _check_row_progress(self, row_number):
        if row_number % 1000 == 0:
            self._check_running()

    def _validate_name(self, name):
        if not name or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", name):
            raise AgentToolError("Result names must start with a letter and contain only letters, numbers, and underscores.")
        if name in self.schema:
            raise AgentToolError("A result cannot overwrite a source table.")

    def _load(self, name):
        if name in self.results:
            return self.results[name]
        if name not in self.schema:
            raise AgentToolError(f"Unknown source: {name}")
        dataframe = self.table_loader(name)
        if dataframe is None:
            raise AgentToolError(f"Table could not be loaded: {name}")
        return StoredResult(
            name=name,
            kind="dataframe",
            value=dataframe,
            columns=list(dataframe.columns),
            metadata={"base_table": True},
        )

    def _dataframe(self, name):
        result = self._load(name)
        if result.kind == "dataframe":
            return result
        if result.kind == "table":
            return StoredResult(
                name=name,
                kind="dataframe",
                value=DataFrame(result.value),
                columns=result.columns,
                metadata={"rows": len(result.value)},
            )
        raise AgentToolError(f"{name} is not a row-based result.")

    def _column(self, result, column):
        if column not in result.columns:
            raise AgentToolError(f"Column '{column}' is not available in {result.name}.")

    def _store(self, result):
        self._validate_name(result.name)
        self.results[result.name] = result
        return _privacy_metadata(result)

    def _iter_rows(self, result):
        dataframe = self._dataframe(result.name)
        return dataframe.value._get_data()

    def execute(self, tool_name, arguments):
        self._check_budget()
        self.tool_calls += 1
        started = time.monotonic()
        status = "ok"
        summary = ""
        try:
            handler = getattr(self, f"_tool_{tool_name}", None)
            if handler is None:
                raise AgentToolError(f"Unknown tool: {tool_name}")
            output = handler(**dict(arguments or {}))
            summary = self._summary(tool_name, output)
            return output
        except Exception as exc:
            status = "error"
            summary = str(exc)[:180]
            raise
        finally:
            duration_ms = round((time.monotonic() - started) * 1000)
            self.trace.append({
                "tool": tool_name,
                "status": status,
                "summary": summary,
                "duration_ms": duration_ms,
            })
            self.audit.record(
                self.request_id,
                self.user_id,
                tool_name,
                dict(arguments or {}),
                status,
                duration_ms,
            )

    def _summary(self, tool_name, output):
        if tool_name == "record_plan":
            return f"Recorded {len(self.plan)} plan steps."
        if tool_name == "ask_clarification":
            return "Paused for clarification."
        if tool_name == "finish":
            return "Selected the final result."
        if isinstance(output, dict):
            if output.get("approval_required"):
                return "Waiting for external chart approval."
            name = output.get("name")
            kind = output.get("kind")
            count = output.get("rows", output.get("groups"))
            detail = f" ({count})" if count is not None else ""
            return f"Created {name or kind or 'result'}{detail}."
        return "Completed."

    def _tool_record_plan(self, steps):
        cleaned = [" ".join(str(step).split())[:120] for step in list(steps)[:6]]
        if not cleaned:
            raise AgentToolError("A plan needs at least one step.")
        self.plan = cleaned
        return {"status": "ok", "steps": cleaned}

    def _tool_inspect_schema(self, table=None):
        if table:
            if table not in self.schema:
                raise AgentToolError(f"Unknown table: {table}")
            tables = {table: self.schema[table].get("types", {})}
        else:
            tables = {name: details.get("types", {}) for name, details in self.schema.items()}
        return {
            "status": "ok",
            "tables": tables,
            "relationships": self.relationships,
            "named_results": {
                name: _privacy_metadata(result) for name, result in self.results.items()
            },
        }

    def _tool_count_rows(self, source, save_as):
        result = self._dataframe(source)
        count = 0
        for count, _ in enumerate(result.value._get_data(), start=1):
            self._check_row_progress(count)
        stored = StoredResult(save_as, "number", count, metadata={"operation": "count"})
        return self._store(stored)

    def _matches(self, actual, operator, expected):
        if operator == "is_null":
            return actual is None or actual == ""
        if operator == "not_null":
            return actual is not None and actual != ""
        if operator in {"contains", "starts_with", "ends_with"}:
            actual_text = str(actual or "").casefold()
            expected_text = str(expected or "").casefold()
            if operator == "contains":
                return expected_text in actual_text
            if operator == "starts_with":
                return actual_text.startswith(expected_text)
            return actual_text.endswith(expected_text)
        actual_number = _as_number(actual)
        expected_number = _as_number(expected)
        left, right = (
            (actual_number, expected_number)
            if actual_number is not None and expected_number is not None
            else (str(actual), str(expected))
        )
        return {
            "eq": left == right,
            "neq": left != right,
            "gt": left > right,
            "gte": left >= right,
            "lt": left < right,
            "lte": left <= right,
        }[operator]

    def _tool_filter_rows(self, source, column, operator, save_as, value=None):
        result = self._dataframe(source)
        self._column(result, column)
        rows = []
        for row_number, row in enumerate(result.value._get_data(), start=1):
            self._check_row_progress(row_number)
            if self._matches(row.get(column), operator, value):
                rows.append(row)
                if len(rows) > self.limits.max_materialized_rows:
                    raise ResourceLimitExceeded(
                        f"Filter exceeded {self.limits.max_materialized_rows} materialized rows."
                    )
        stored = StoredResult(
            save_as, "dataframe", DataFrame(rows), list(result.columns),
            {"rows": len(rows), "source": source},
        )
        return self._store(stored)

    def _tool_select_columns(self, source, columns, save_as, limit=25):
        result = self._dataframe(source)
        selected = list(columns)
        if not selected:
            raise AgentToolError("Select at least one column.")
        for column in selected:
            self._column(result, column)
        limit = min(max(int(limit), 1), self.limits.max_output_rows)
        rows = []
        for row_number, row in enumerate(result.value._get_data(), start=1):
            self._check_row_progress(row_number)
            rows.append({column: row.get(column) for column in selected})
            if len(rows) >= limit:
                break
        return self._store(StoredResult(save_as, "table", rows, selected))

    def _tool_join_sources(self, left, right, left_column, right_column, save_as):
        left_result = self._dataframe(left)
        right_result = self._dataframe(right)
        self._column(left_result, left_column)
        self._column(right_result, right_column)
        right_index = {}
        indexed = 0
        for row_number, row in enumerate(right_result.value._get_data(), start=1):
            self._check_row_progress(row_number)
            right_index.setdefault(row.get(right_column), []).append(row)
            indexed += 1
            if indexed > self.limits.max_materialized_rows:
                raise ResourceLimitExceeded("The right side of the join is too large.")
        rows = []
        for row_number, left_row in enumerate(left_result.value._get_data(), start=1):
            self._check_row_progress(row_number)
            for right_row in right_index.get(left_row.get(left_column), []):
                combined = dict(left_row)
                for column, value in right_row.items():
                    if column == right_column:
                        continue
                    output_column = column if column not in combined else f"{right}_{column}"
                    combined[output_column] = value
                rows.append(combined)
                if len(rows) > self.limits.max_materialized_rows:
                    raise ResourceLimitExceeded("The joined result is too large.")
        columns = list(rows[0].keys()) if rows else list(left_result.columns)
        return self._store(StoredResult(
            save_as, "dataframe", DataFrame(rows), columns,
            {"rows": len(rows), "left": left, "right": right},
        ))

    def _tool_aggregate_rows(self, source, group_by, value_column, operation, save_as):
        result = self._dataframe(source)
        self._column(result, group_by)
        self._column(result, value_column)
        states = {}
        for row_number, row in enumerate(result.value._get_data(), start=1):
            self._check_row_progress(row_number)
            group = row.get(group_by)
            if group is None:
                continue
            if group not in states:
                if len(states) >= self.limits.max_groups:
                    raise ResourceLimitExceeded(
                        f"Aggregation exceeded {self.limits.max_groups} groups."
                    )
                states[group] = {"count": 0, "sum": 0.0, "min": None, "max": None}
            state = states[group]
            if operation == "count":
                state["count"] += 1
                continue
            value = _as_number(row.get(value_column))
            if value is None or not math.isfinite(value):
                continue
            state["count"] += 1
            state["sum"] += value
            state["min"] = value if state["min"] is None else min(state["min"], value)
            state["max"] = value if state["max"] is None else max(state["max"], value)
        values = {}
        metric = f"{operation}_{value_column}"
        for group, state in states.items():
            if operation == "count":
                aggregate_value = state["count"]
            elif operation == "sum":
                aggregate_value = state["sum"]
            elif operation == "avg":
                aggregate_value = state["sum"] / state["count"] if state["count"] else 0
            else:
                aggregate_value = state[operation]
            values[group] = {metric: aggregate_value}
        return self._store(StoredResult(
            save_as, "aggregate", values, [group_by, metric],
            {"group_by": group_by, "metric": metric, "operation": operation},
        ))

    def _tool_top_rows(self, source, sort_column, limit, descending, save_as):
        result = self._dataframe(source)
        self._column(result, sort_column)
        limit = min(max(int(limit), 1), self.limits.max_output_rows)
        ranked = []
        counter = 0
        for row_number, row in enumerate(result.value._get_data(), start=1):
            self._check_row_progress(row_number)
            number = _as_number(row.get(sort_column))
            if number is None:
                continue
            score = number if descending else -number
            item = (score, counter, row)
            counter += 1
            if len(ranked) < limit:
                heapq.heappush(ranked, item)
            elif item[0] > ranked[0][0]:
                heapq.heapreplace(ranked, item)
        rows = [item[2] for item in sorted(ranked, reverse=True)]
        return self._store(StoredResult(save_as, "table", rows, list(result.columns)))

    def _tool_create_chart(self, source, title, chart_type, save_as):
        result = self._load(source)
        if result.kind != "aggregate":
            raise AgentToolError("Charts require a named aggregate result.")
        if "external_chart" not in self.approvals:
            self.approval = {
                "scope": "external_chart",
                "title": "Share aggregate chart data?",
                "message": (
                    "Creating this chart sends aggregate labels and values to QuickChart. "
                    "Raw source rows are not sent."
                ),
            }
            return {"status": "paused", "approval_required": True, **self.approval}
        chart_url = build_chart_url(title, chart_type, result.value)
        if not chart_url:
            raise AgentToolError("The chart could not be created.")
        return self._store(StoredResult(
            save_as, "chart", chart_url,
            metadata={"source": source, "chart_type": chart_type},
        ))

    def _tool_ask_clarification(self, question):
        cleaned = " ".join(str(question).split())[:300]
        self.clarification = cleaned
        return {"status": "paused", "question": cleaned}

    def _tool_finish(self, message, source=None):
        if source:
            result = self._load(source)
        else:
            result = None
        self.finished = {"result": result, "message": " ".join(str(message).split())[:500]}
        return {"status": "finished", "source": source}
