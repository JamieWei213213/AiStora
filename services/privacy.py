import hashlib
import re
from dataclasses import dataclass


SENSITIVE_NAME_PATTERNS = (
    (
        "credential",
        re.compile(r"(^|_)(password|secret|token|api_key|credential)(_|$)", re.I),
    ),
    (
        "direct_identifier",
        re.compile(
            r"(^|_)(email|phone|address|ssn|sin|passport)(_|$)"
            r"|^(name|customer_name|contact_first_name|contact_last_name|first_name|last_name|full_name|person_name|relationship_manager|sales_rep)$",
            re.I,
        ),
    ),
    (
        "identifier",
        re.compile(
            r"^(customer_id|client_id|user_id|employee_id|transaction_id|ticket_id|order_id|invoice_id)$",
            re.I,
        ),
    ),
    (
        "financial",
        re.compile(r"(^|_)(account|routing|iban|card|credit|salary|wage)(_|$)", re.I),
    ),
    (
        "unstructured_text",
        re.compile(
            r"^(free_text|free_text_summary|notes?|comments?|narrative|text_summary)$",
            re.I,
        ),
    ),
)


@dataclass(frozen=True)
class PrivacyPolicy:
    """Two independent boundaries.

    ``schema_mode`` governs what may enter an LLM prompt and is the boundary the
    product is built on: credentials are dropped, and nothing but column names,
    types and classifications is ever sent. Do not relax this.

    ``result_mode`` governs what is rendered in the *data owner's own browser*.
    It defaults to ``full`` because masking an accountant's own client names in
    their own session protects data from the person it belongs to. ``masked``
    remains available for shared screens and demos, and ``aggregate_only`` for
    the strictest deployments.
    """

    schema_mode: str = "classified"
    result_mode: str = "full"
    max_result_rows: int = 25

    def __post_init__(self):
        if self.schema_mode not in {"full", "classified", "aliases"}:
            raise ValueError("schema_mode must be full, classified, or aliases")
        if self.result_mode not in {"full", "masked", "aggregate_only"}:
            raise ValueError("result_mode must be full, masked, or aggregate_only")


def classify_column(name):
    for classification, pattern in SENSITIVE_NAME_PATTERNS:
        if pattern.search(str(name)):
            return classification
    return "ordinary"


def _alias(value, prefix):
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{digest}"


def redact_schema(schema, policy):
    """Build the only schema representation allowed into an LLM prompt."""
    output = {}
    for table_name, details in (schema or {}).items():
        safe_table = _alias(table_name, "table") if policy.schema_mode == "aliases" else table_name
        columns, classifications = {}, {}
        for column, data_type in (details.get("types") or {}).items():
            classification = classify_column(column)
            safe_column = _alias(column, "column") if policy.schema_mode == "aliases" else column
            if policy.schema_mode == "classified" and classification == "credential":
                continue
            columns[safe_column] = data_type
            classifications[safe_column] = classification
        output[safe_table] = {
            "columns": columns,
            "classifications": classifications,
            "row_count": details.get("row_count"),
        }
    return output


def redact_relationships(relationships, safe_schema):
    """Drop relationship metadata whose table/column was removed or aliased."""
    safe = []
    for relationship in relationships or []:
        source_table = relationship.get("from_table")
        target_table = relationship.get("to_table")
        source_column = relationship.get("from_column")
        target_column = relationship.get("to_column")
        if (
            source_table in safe_schema
            and target_table in safe_schema
            and source_column in safe_schema[source_table]["columns"]
            and target_column in safe_schema[target_table]["columns"]
        ):
            safe.append({
                "from_table": source_table,
                "from_column": source_column,
                "to_table": target_table,
                "to_column": target_column,
            })
    return safe


def protect_result_rows(rows, policy):
    if policy.result_mode == "aggregate_only":
        return []
    protected = []
    for row in list(rows or [])[:policy.max_result_rows]:
        clean = {}
        for column, value in row.items():
            sensitive = classify_column(column) != "ordinary"
            clean[column] = "[REDACTED]" if policy.result_mode == "masked" and sensitive else value
        protected.append(clean)
    return protected
