import hashlib
import json
import math
import re
import statistics
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timezone

from services.privacy import classify_column


REPORT_VERSION = 1
REPORT_SECTIONS = [
    "overview",
    "data_quality",
    "numeric_summary",
    "categorical_summary",
    "time_summary",
    "correlations",
    "relationships",
    "findings",
    "privacy_and_limitations",
]
ALLOWED_ANALYSES = set(REPORT_SECTIONS) - {"relationships", "findings", "privacy_and_limitations"}
NUMERIC_TYPES = {"int", "integer", "float", "double", "decimal", "number", "numeric"}
IDENTIFIER_COLUMN_PATTERN = re.compile(r"(^id$|_id$|_key$|^key$)", re.I)
TEMPORAL_COMPONENT_PATTERN = re.compile(r"^(year|month|day|week|quarter|hour)$", re.I)
TIME_COLUMN_PATTERN = re.compile(
    r"(^|_)(date|time|timestamp|created|updated|modified|occurred|completed)(_|$)",
    re.I,
)


class EDAPlanValidationError(ValueError):
    pass


@dataclass(frozen=True)
class EDALimits:
    max_tables: int = 20
    max_columns_per_table: int = 60
    max_rows_per_table: int = 100000
    max_numeric_columns: int = 25
    max_categorical_columns: int = 25
    max_correlation_columns: int = 12
    max_top_categories: int = 5
    max_distinct_values: int = 25000
    max_duplicate_rows: int = 100000
    max_category_tracked_values: int = 1000
    min_outlier_sample_size: int = 30
    timeout_seconds: int = 30

    def __post_init__(self):
        for name, value in self.__dict__.items():
            if int(value) < 1:
                raise ValueError(f"{name} must be at least 1")


def limits_from_config(config):
    return EDALimits(
        max_tables=int(getattr(config, "EDA_MAX_TABLES", 20)),
        max_columns_per_table=int(getattr(config, "EDA_MAX_COLUMNS_PER_TABLE", 60)),
        max_rows_per_table=int(getattr(config, "EDA_MAX_ROWS_PER_TABLE", 100000)),
        max_numeric_columns=int(getattr(config, "EDA_MAX_NUMERIC_COLUMNS", 25)),
        max_categorical_columns=int(getattr(config, "EDA_MAX_CATEGORICAL_COLUMNS", 25)),
        max_correlation_columns=int(getattr(config, "EDA_MAX_CORRELATION_COLUMNS", 12)),
        max_top_categories=int(getattr(config, "EDA_MAX_TOP_CATEGORIES", 5)),
        max_distinct_values=int(getattr(config, "EDA_MAX_DISTINCT_VALUES", 25000)),
        max_duplicate_rows=int(getattr(config, "EDA_MAX_DUPLICATE_ROWS", 100000)),
        max_category_tracked_values=int(
            getattr(config, "EDA_MAX_CATEGORY_TRACKED_VALUES", 1000)
        ),
        min_outlier_sample_size=int(
            getattr(config, "EDA_MIN_OUTLIER_SAMPLE_SIZE", 30)
        ),
        timeout_seconds=int(getattr(config, "EDA_TIMEOUT_SECONDS", 30)),
    )


def infer_column_role(name, declared_type):
    """Classify how a column should be analyzed, independently of CSV casting."""
    column = str(name)
    classification = classify_column(column)
    if classification == "credential":
        return "credential"
    if IDENTIFIER_COLUMN_PATTERN.search(column) or classification in {
        "direct_identifier",
        "identifier",
    }:
        return "identifier"
    if classification == "unstructured_text":
        return "free_text"
    if TIME_COLUMN_PATTERN.search(column):
        return "datetime"
    if TEMPORAL_COMPONENT_PATTERN.search(column):
        return "temporal_component"
    if str(declared_type or "").lower() in NUMERIC_TYPES:
        return "numeric_measure"
    return "categorical"


def build_eda_plan(schema, limits=None):
    """Create a deterministic, schema-constrained JSON plan for an EDA report."""
    limits = limits or EDALimits()
    tables = []
    for table_name, details in list((schema or {}).items())[:limits.max_tables]:
        columns = list((details.get("types") or {}).keys())[:limits.max_columns_per_table]
        tables.append({
            "name": table_name,
            "columns": columns,
            "row_limit": limits.max_rows_per_table,
            "analyses": [
                "overview",
                "data_quality",
                "numeric_summary",
                "categorical_summary",
                "time_summary",
                "correlations",
            ],
        })
    return {
        "version": REPORT_VERSION,
        "operation": "exploratory_data_analysis",
        "sections": list(REPORT_SECTIONS),
        "tables": tables,
    }


def validate_eda_plan(plan, schema, limits=None):
    """Reject malformed, unknown, or over-budget report operations."""
    limits = limits or EDALimits()
    if not isinstance(plan, dict):
        raise EDAPlanValidationError("The EDA plan must be a JSON object")
    if plan.get("version") != REPORT_VERSION:
        raise EDAPlanValidationError("Unsupported EDA plan version")
    if plan.get("operation") != "exploratory_data_analysis":
        raise EDAPlanValidationError("Unsupported EDA operation")
    sections = plan.get("sections")
    if not isinstance(sections, list) or sections != REPORT_SECTIONS:
        raise EDAPlanValidationError("EDA report sections are invalid")
    tables = plan.get("tables")
    if not isinstance(tables, list) or not tables:
        raise EDAPlanValidationError("The EDA plan must contain at least one table")
    if len(tables) > limits.max_tables:
        raise EDAPlanValidationError("The EDA plan exceeds the table limit")

    seen = set()
    for step in tables:
        if not isinstance(step, dict):
            raise EDAPlanValidationError("Each EDA table step must be an object")
        table_name = step.get("name")
        if table_name not in (schema or {}):
            raise EDAPlanValidationError(f"Unknown table in EDA plan: {table_name}")
        if table_name in seen:
            raise EDAPlanValidationError(f"Duplicate table in EDA plan: {table_name}")
        seen.add(table_name)
        columns = step.get("columns")
        known_columns = set(((schema or {}).get(table_name, {}).get("types") or {}).keys())
        if not isinstance(columns, list) or not columns:
            raise EDAPlanValidationError(f"No columns selected for table: {table_name}")
        if len(columns) > limits.max_columns_per_table or len(columns) != len(set(columns)):
            raise EDAPlanValidationError(f"Invalid column selection for table: {table_name}")
        if any(column not in known_columns for column in columns):
            raise EDAPlanValidationError(f"Unknown column in EDA plan for table: {table_name}")
        row_limit = step.get("row_limit")
        if not isinstance(row_limit, int) or not 1 <= row_limit <= limits.max_rows_per_table:
            raise EDAPlanValidationError(f"Invalid row limit for table: {table_name}")
        analyses = step.get("analyses")
        if not isinstance(analyses, list) or not analyses or len(analyses) != len(set(analyses)):
            raise EDAPlanValidationError(f"Invalid analyses for table: {table_name}")
        if any(analysis not in ALLOWED_ANALYSES for analysis in analyses):
            raise EDAPlanValidationError(f"Unsupported analysis for table: {table_name}")
    return True


def _round_number(value, digits=6):
    if value is None:
        return None
    value = float(value)
    if not math.isfinite(value):
        return None
    return round(value, digits)


def _quantile(sorted_values, proportion):
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * proportion
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def _safe_float(value):
    if value is None or value == "":
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return numeric if math.isfinite(numeric) else None


def _parse_datetime(value):
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, date):
        parsed = datetime.combine(value, datetime.min.time())
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        parsed = None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d", "%m/%d/%Y %H:%M:%S"):
                try:
                    parsed = datetime.strptime(text, fmt)
                    break
                except ValueError:
                    continue
        if parsed is None:
            return None
    else:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _stable_value(value):
    try:
        return json.dumps(value, sort_keys=True, default=str, ensure_ascii=False)
    except (TypeError, ValueError):
        return str(value)


def _value_digest(value, digest_size=16):
    return hashlib.blake2b(
        _stable_value(value).encode("utf-8"),
        digest_size=digest_size,
    ).digest()


def _pearson(left, right):
    pairs = [(x, y) for x, y in zip(left, right) if x is not None and y is not None]
    if len(pairs) < 3:
        return None, len(pairs)
    xs = [pair[0] for pair in pairs]
    ys = [pair[1] for pair in pairs]
    mean_x = statistics.fmean(xs)
    mean_y = statistics.fmean(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs) * sum((y - mean_y) ** 2 for y in ys)
    )
    if denominator == 0:
        return None, len(pairs)
    return _round_number(numerator / denominator), len(pairs)


def _numeric_summary(
    column,
    values,
    missing_count,
    invalid_count,
    rows_scanned,
    min_outlier_sample_size,
):
    sorted_values = sorted(values)
    q1 = _quantile(sorted_values, 0.25)
    median = _quantile(sorted_values, 0.5)
    q3 = _quantile(sorted_values, 0.75)
    iqr = None if q1 is None or q3 is None else q3 - q1
    mean = statistics.fmean(sorted_values) if sorted_values else None
    std_dev = statistics.pstdev(sorted_values) if len(sorted_values) > 1 else 0
    skewness = None
    if len(sorted_values) > 2 and std_dev:
        skewness = statistics.fmean(
            ((value - mean) / std_dev) ** 3 for value in sorted_values
        )

    outlier_count = None
    outlier_rate = None
    outlier_status = "not_assessed_small_sample"
    if len(sorted_values) >= min_outlier_sample_size and iqr is not None:
        lower = q1 - 1.5 * iqr
        upper = q3 + 1.5 * iqr
        outlier_count = sum(value < lower or value > upper for value in sorted_values)
        outlier_rate = outlier_count / len(sorted_values) if sorted_values else 0
        outlier_status = "assessed"
    if outlier_status != "assessed":
        distribution_shape = "insufficient_sample"
    elif (abs(skewness or 0) >= 1) or (outlier_rate or 0) >= 0.1:
        distribution_shape = "strongly_skewed_or_heavy_tailed"
    elif outlier_count:
        distribution_shape = "some_tail_values"
    else:
        distribution_shape = "no_iqr_tail_flags"
    return {
        "column": column,
        "classification": classify_column(column),
        "semantic_role": "numeric_measure",
        "count": len(values),
        "missing_count": missing_count,
        "missing_rate": _round_number(missing_count / rows_scanned) if rows_scanned else 0,
        "invalid_numeric_count": invalid_count,
        "min": _round_number(sorted_values[0]) if sorted_values else None,
        "q1": _round_number(q1),
        "median": _round_number(median),
        "mean": _round_number(mean),
        "q3": _round_number(q3),
        "max": _round_number(sorted_values[-1]) if sorted_values else None,
        "std_dev": _round_number(std_dev),
        "skewness": _round_number(skewness),
        "distribution_shape": distribution_shape,
        "zero_count": sum(value == 0 for value in sorted_values),
        "iqr_outlier_count": outlier_count,
        "iqr_outlier_rate": _round_number(outlier_rate),
        "outlier_status": outlier_status,
        "outlier_rule": "values below Q1 - 1.5*IQR or above Q3 + 1.5*IQR",
    }


def _analyze_missingness_patterns(
    dataframe,
    missingness,
    categorical_summary,
    deadline,
):
    """Explain strongly group-dependent missingness without treating it as an error."""
    targets = [
        item["column"]
        for item in missingness
        if item["missing_count"] >= 10 and item["missing_rate"] >= 0.01
    ][:3]
    candidates = [
        item["column"]
        for item in categorical_summary
        if item["classification"] == "ordinary"
        and item["semantic_role"] in {"categorical", "temporal_component"}
        and not item["distinct_count_is_lower_bound"]
        and 2 <= item["distinct_count"] <= 10
    ]
    if not targets or not candidates:
        return [], False

    totals = {candidate: Counter() for candidate in candidates}
    missing_by_group = {
        target: {candidate: Counter() for candidate in candidates}
        for target in targets
    }
    for row_index, row in enumerate(dataframe._get_data()):
        if row_index % 250 == 0 and time.monotonic() >= deadline:
            return [], True
        group_values = {}
        for candidate in candidates:
            value = row.get(candidate)
            if value is None or value == "":
                continue
            display = str(value)
            group_values[candidate] = display
            totals[candidate][display] += 1
        for target in targets:
            if row.get(target) not in (None, ""):
                continue
            for candidate, display in group_values.items():
                missing_by_group[target][candidate][display] += 1

    patterns = []
    for target in targets:
        best = None
        for candidate in candidates:
            groups = []
            for value, total_count in totals[candidate].items():
                if total_count < 5:
                    continue
                missing_count = missing_by_group[target][candidate][value]
                groups.append({
                    "value": value,
                    "total_count": total_count,
                    "missing_count": missing_count,
                    "missing_rate": _round_number(missing_count / total_count),
                })
            if len(groups) < 2:
                continue
            groups.sort(key=lambda item: item["missing_rate"], reverse=True)
            rate_range = groups[0]["missing_rate"] - groups[-1]["missing_rate"]
            if rate_range < 0.5:
                continue
            candidate_result = {
                "column": target,
                "group_by": candidate,
                "strength": _round_number(rate_range),
                "groups": groups,
            }
            if best is None or candidate_result["strength"] > best["strength"]:
                best = candidate_result
        if best:
            highest = best["groups"][0]
            lowest = best["groups"][-1]
            best["summary"] = (
                f"{target} missingness varies strongly by {best['group_by']} "
                f"({highest['missing_rate'] * 100:.1f}% for {highest['value']} versus "
                f"{lowest['missing_rate'] * 100:.1f}% for {lowest['value']}); "
                "this may be structurally expected rather than a data defect."
            )
            patterns.append(best)
    return patterns, False


def _profile_dataframe(
    table_name,
    dataframe,
    step,
    declared_types,
    limits,
    deadline,
    relationship_columns=None,
):
    started = time.monotonic()
    columns = [column for column in step["columns"] if column in dataframe.get_header()]
    missing = {column: 0 for column in columns}
    column_roles = {
        column: infer_column_role(column, declared_types.get(column))
        for column in columns
    }
    numeric_columns = [
        column for column in columns if column_roles[column] == "numeric_measure"
    ][:limits.max_numeric_columns]
    categorical_columns = [column for column in columns if column not in numeric_columns][
        :limits.max_categorical_columns
    ]
    time_columns = [column for column in categorical_columns if column_roles[column] == "datetime"]
    numeric_vectors = {column: [] for column in numeric_columns}
    invalid_numeric = {column: 0 for column in numeric_columns}
    category_counts = {column: Counter() for column in categorical_columns}
    category_count_truncated = {column: False for column in categorical_columns}
    distinct_values = {column: set() for column in categorical_columns}
    distinct_truncated = {column: False for column in categorical_columns}
    time_values = {column: [] for column in time_columns}
    time_invalid = {column: 0 for column in time_columns}
    relationship_value_counts = {
        column: Counter()
        for column in (relationship_columns or set())
        if column in dataframe.get_header()
    }
    row_fingerprints = set()
    duplicate_rows = 0
    duplicate_tracking_truncated = False
    rows_scanned = 0
    row_limit_reached = False
    timeout_reached = False

    for row in dataframe._get_data():
        if rows_scanned >= step["row_limit"]:
            row_limit_reached = True
            break
        if rows_scanned % 250 == 0 and time.monotonic() >= deadline:
            timeout_reached = True
            break
        rows_scanned += 1

        if rows_scanned <= limits.max_duplicate_rows:
            fingerprint = _value_digest([row.get(column) for column in columns])
            if fingerprint in row_fingerprints:
                duplicate_rows += 1
            else:
                row_fingerprints.add(fingerprint)
        else:
            duplicate_tracking_truncated = True

        for column in columns:
            value = row.get(column)
            if value is None or value == "":
                missing[column] += 1

        for column in numeric_columns:
            value = row.get(column)
            numeric = _safe_float(value)
            numeric_vectors[column].append(numeric)
            if value not in (None, "") and numeric is None:
                invalid_numeric[column] += 1

        for column in categorical_columns:
            value = row.get(column)
            if value is None or value == "":
                continue
            display = str(value)
            classification = classify_column(column)
            role = column_roles[column]
            can_show_values = classification == "ordinary" and role not in {
                "identifier",
                "credential",
                "free_text",
                "datetime",
            }
            if can_show_values:
                counts = category_counts[column]
                if display in counts:
                    counts[display] += 1
                elif len(counts) < limits.max_category_tracked_values:
                    counts[display] = 1
                else:
                    category_count_truncated[column] = True
            if len(distinct_values[column]) < limits.max_distinct_values:
                distinct_values[column].add(_value_digest(display, digest_size=8))
            elif _value_digest(display, digest_size=8) not in distinct_values[column]:
                distinct_truncated[column] = True
            if column in time_values:
                parsed = _parse_datetime(value)
                if parsed is None:
                    time_invalid[column] += 1
                else:
                    time_values[column].append(parsed)

        for column, counts in relationship_value_counts.items():
            value = row.get(column)
            if value is not None and value != "":
                # Join coverage needs equality, not the original key. Keeping only
                # a stable digest avoids retaining customer/account identifiers in
                # the in-memory profile while still permitting exact comparisons.
                counts[_value_digest(value)] += 1

    row_scan_complete = not row_limit_reached and not timeout_reached
    complete_scan = row_scan_complete and not duplicate_tracking_truncated
    missingness = [
        {
            "column": column,
            "classification": classify_column(column),
            "semantic_role": column_roles[column],
            "missing_count": missing[column],
            "missing_rate": _round_number(missing[column] / rows_scanned) if rows_scanned else 0,
        }
        for column in columns
    ]
    missingness.sort(key=lambda item: (-item["missing_count"], item["column"]))

    numeric = [
        _numeric_summary(
            column,
            [value for value in numeric_vectors[column] if value is not None],
            missing[column],
            invalid_numeric[column],
            rows_scanned,
            limits.min_outlier_sample_size,
        )
        for column in numeric_columns
    ]

    categorical = []
    for column in categorical_columns:
        classification = classify_column(column)
        role = column_roles[column]
        non_null_count = rows_scanned - missing[column]
        distinct_count = len(distinct_values[column])
        high_cardinality = bool(
            distinct_truncated[column]
            or category_count_truncated[column]
            or (
                non_null_count >= 50
                and distinct_count / non_null_count > 0.5
            )
        )
        values_redacted = classification != "ordinary" or role in {
            "identifier",
            "credential",
            "free_text",
        }
        if classification != "ordinary":
            suppression_reason = "sensitive_column"
        elif role == "identifier":
            suppression_reason = "identifier"
        elif role == "free_text":
            suppression_reason = "free_text"
        elif role == "datetime":
            suppression_reason = "summarized_as_time"
        elif high_cardinality:
            suppression_reason = "high_cardinality"
        else:
            suppression_reason = None
        categorical.append({
            "column": column,
            "classification": classification,
            "semantic_role": role,
            "non_null_count": non_null_count,
            "distinct_count": distinct_count,
            "distinct_count_is_lower_bound": distinct_truncated[column],
            "top_values": [] if suppression_reason else [
                {"value": value, "count": count}
                for value, count in category_counts[column].most_common(limits.max_top_categories)
            ],
            "values_redacted": values_redacted,
            "values_suppressed_reason": suppression_reason,
        })

    time_summary = []
    for column in time_columns:
        parsed = time_values[column]
        non_null = rows_scanned - missing[column]
        if not parsed or (non_null and len(parsed) / non_null < 0.8):
            continue
        time_summary.append({
            "column": column,
            "parsed_count": len(parsed),
            "invalid_date_count": time_invalid[column],
            "min": min(parsed).isoformat(),
            "max": max(parsed).isoformat(),
        })

    correlations = []
    correlation_columns = numeric_columns[:limits.max_correlation_columns]
    for left_index, left in enumerate(correlation_columns):
        for right in correlation_columns[left_index + 1:]:
            coefficient, pair_count = _pearson(numeric_vectors[left], numeric_vectors[right])
            if coefficient is not None:
                correlations.append({
                    "left": left,
                    "right": right,
                    "pearson_r": coefficient,
                    "pair_count": pair_count,
                })
    correlations.sort(key=lambda item: abs(item["pearson_r"]), reverse=True)
    correlations = correlations[:10]

    missingness_patterns = []
    if row_scan_complete:
        missingness_patterns, pattern_timeout = _analyze_missingness_patterns(
            dataframe,
            missingness,
            categorical,
            deadline,
        )
        if pattern_timeout:
            timeout_reached = True
            complete_scan = False

    findings = []
    if rows_scanned == 0:
        findings.append("No data rows were available to profile.")
    if missingness and missingness[0]["missing_count"]:
        top = missingness[0]
        contextual = next(
            (
                pattern
                for pattern in missingness_patterns
                if pattern["column"] == top["column"]
            ),
            None,
        )
        findings.append(
            contextual["summary"]
            if contextual
            else f"{top['column']} has the highest missingness at {top['missing_rate'] * 100:.1f}%."
        )
    if duplicate_rows:
        qualifier = "at least " if duplicate_tracking_truncated else ""
        findings.append(f"The scanned rows contain {qualifier}{duplicate_rows} duplicate rows.")
    outlier_columns = sorted(
        (item for item in numeric if item["iqr_outlier_count"]),
        key=lambda item: item["iqr_outlier_count"],
        reverse=True,
    )
    if outlier_columns:
        top = outlier_columns[0]
        rate = top["iqr_outlier_rate"] * 100
        if top["distribution_shape"] == "strongly_skewed_or_heavy_tailed":
            findings.append(
                f"{top['column']} is strongly skewed or heavy-tailed; {rate:.1f}% of values fall outside IQR fences. Treat this as distribution shape, not confirmed errors."
            )
        else:
            findings.append(
                f"{top['column']} has {top['iqr_outlier_count']} IQR tail values ({rate:.1f}%) for review."
            )
    strong = next((item for item in correlations if abs(item["pearson_r"]) >= 0.8), None)
    if strong:
        findings.append(
            f"{strong['left']} and {strong['right']} have a strong linear association (r={strong['pearson_r']})."
        )

    warnings = []
    if len(columns) < len(step["columns"]):
        warnings.append("Some planned columns were not present in the loaded file.")
    if len([column for column in columns if column_roles[column] == "numeric_measure"]) > limits.max_numeric_columns:
        warnings.append(f"Numeric profiling was limited to {limits.max_numeric_columns} columns.")
    if len([column for column in columns if column not in numeric_columns]) > limits.max_categorical_columns:
        warnings.append(f"Categorical profiling was limited to {limits.max_categorical_columns} columns.")
    if row_limit_reached:
        warnings.append(f"Statistics are based on the first {rows_scanned} rows because the row limit was reached.")
    if timeout_reached:
        warnings.append("This table was only partially profiled because the report time limit was reached.")
    if duplicate_tracking_truncated:
        warnings.append(
            f"Duplicate detection covers only the first {limits.max_duplicate_rows} scanned rows."
        )

    return {
        "name": table_name,
        "declared_row_count": None,
        "rows_scanned": rows_scanned,
        "row_scan_complete": row_scan_complete,
        "complete_scan": complete_scan,
        "column_count": len(columns),
        "column_roles": [
            {
                "column": column,
                "declared_type": declared_types.get(column),
                "semantic_role": column_roles[column],
                "classification": classify_column(column),
            }
            for column in columns
        ],
        "numeric_column_count": len(numeric_columns),
        "categorical_column_count": len(categorical_columns),
        "duplicate_rows": duplicate_rows,
        "duplicate_scope": min(rows_scanned, limits.max_duplicate_rows),
        "missingness": missingness,
        "missingness_patterns": missingness_patterns,
        "numeric_summary": numeric,
        "categorical_summary": categorical,
        "time_summary": time_summary,
        "correlations": correlations,
        "findings": findings,
        "warnings": warnings,
        "duration_ms": int((time.monotonic() - started) * 1000),
        "timeout_reached": timeout_reached,
        "_relationship_value_counts": relationship_value_counts,
    }


def _validated_relationships(relationships, schema):
    safe = []
    for relationship in relationships or []:
        source_table = relationship.get("from_table")
        target_table = relationship.get("to_table")
        source_column = relationship.get("from_column")
        target_column = relationship.get("to_column")
        source_columns = ((schema or {}).get(source_table, {}).get("types") or {})
        target_columns = ((schema or {}).get(target_table, {}).get("types") or {})
        if source_column in source_columns and target_column in target_columns:
            safe.append({
                "from_table": source_table,
                "from_column": source_column,
                "to_table": target_table,
                "to_column": target_column,
            })
    return safe


def _measure_relationships(relationships, table_profiles):
    measured = []
    profile_by_name = {profile["name"]: profile for profile in table_profiles}
    for relationship in relationships:
        source_profile = profile_by_name.get(relationship["from_table"])
        target_profile = profile_by_name.get(relationship["to_table"])
        source_counts = (
            source_profile.get("_relationship_value_counts", {}).get(
                relationship["from_column"]
            )
            if source_profile
            else None
        )
        target_counts = (
            target_profile.get("_relationship_value_counts", {}).get(
                relationship["to_column"]
            )
            if target_profile
            else None
        )
        result = dict(relationship)
        if source_counts is None or target_counts is None:
            result["status"] = "schema_validated_not_data_verified"
            measured.append(result)
            continue
        overlap = set(source_counts) & set(target_counts)
        source_non_null = sum(source_counts.values())
        target_non_null = sum(target_counts.values())
        source_matched = sum(source_counts[key] for key in overlap)
        target_matched = sum(target_counts[key] for key in overlap)
        result.update({
            "status": "data_verified",
            "overlap_distinct_keys": len(overlap),
            "from_distinct_keys": len(source_counts),
            "to_distinct_keys": len(target_counts),
            "from_non_null_rows": source_non_null,
            "to_non_null_rows": target_non_null,
            "from_matched_rows": source_matched,
            "to_matched_rows": target_matched,
            "from_match_rate": _round_number(source_matched / source_non_null)
            if source_non_null
            else 0,
            "to_match_rate": _round_number(target_matched / target_non_null)
            if target_non_null
            else 0,
            "scope_complete": bool(
                source_profile.get("complete_scan") and target_profile.get("complete_scan")
            ),
        })
        measured.append(result)
    return measured


def generate_eda_report(schema, dataframe_loader, relationships=None, limits=None):
    """Run the validated report plan locally and return privacy-limited JSON."""
    limits = limits or EDALimits()
    plan = build_eda_plan(schema, limits)
    validate_eda_plan(plan, schema, limits)
    started = time.monotonic()
    deadline = started + limits.timeout_seconds
    tables = []
    trace = []
    limitations = []
    sensitive_columns = []
    validated_relationships = _validated_relationships(relationships, schema)
    relationship_columns = defaultdict(set)
    for relationship in validated_relationships:
        relationship_columns[relationship["from_table"]].add(
            relationship["from_column"]
        )
        relationship_columns[relationship["to_table"]].add(
            relationship["to_column"]
        )

    if len(schema or {}) > limits.max_tables:
        limitations.append(
            f"Only the first {limits.max_tables} tables were included in this report."
        )
    for table_name, details in (schema or {}).items():
        for column in (details.get("types") or {}):
            classification = classify_column(column)
            if classification != "ordinary":
                sensitive_columns.append({
                    "table": table_name,
                    "column": column,
                    "classification": classification,
                })

    for step in plan["tables"]:
        if time.monotonic() >= deadline:
            limitations.append("The global report time limit was reached before all tables were profiled.")
            break
        table_name = step["name"]
        table_started = time.monotonic()
        try:
            dataframe = dataframe_loader(table_name)
            if dataframe is None:
                raise LookupError("table data is unavailable")
            profile = _profile_dataframe(
                table_name,
                dataframe,
                step,
                (schema.get(table_name, {}).get("types") or {}),
                limits,
                deadline,
                relationship_columns.get(table_name),
            )
            profile["declared_row_count"] = schema.get(table_name, {}).get("row_count")
            tables.append(profile)
            trace.append({
                "step": "profile_table",
                "table": table_name,
                "status": "partial" if not profile["complete_scan"] else "ok",
                "duration_ms": profile["duration_ms"],
                "summary": f"Profiled {profile['rows_scanned']} rows and {profile['column_count']} columns.",
            })
            if profile["timeout_reached"]:
                limitations.append("The global report time limit was reached during table profiling.")
                break
        except Exception:
            trace.append({
                "step": "profile_table",
                "table": table_name,
                "status": "error",
                "duration_ms": int((time.monotonic() - table_started) * 1000),
                "summary": "The table could not be profiled safely.",
            })

    scanned_rows = sum(table["rows_scanned"] for table in tables)
    declared_rows = sum(
        int(details.get("row_count") or 0) for details in (schema or {}).values()
    )
    column_count = sum(len(details.get("types") or {}) for details in (schema or {}).values())
    partial = len(tables) != len(plan["tables"]) or any(not table["complete_scan"] for table in tables)
    if any(item["status"] == "error" for item in trace):
        limitations.append("One or more tables could not be loaded and were skipped.")
        partial = True
    if any(table["rows_scanned"] < int(table["declared_row_count"] or 0) for table in tables):
        limitations.append("Some statistics describe a bounded leading sample rather than every row.")
    if any(table["duplicate_scope"] < table["rows_scanned"] for table in tables):
        limitations.append("Duplicate checks are bounded separately from other statistics.")
    measured_relationships = _measure_relationships(validated_relationships, tables)
    if any(
        relationship["status"] != "data_verified"
        or not relationship.get("scope_complete", False)
        for relationship in measured_relationships
    ):
        limitations.append(
            "Some detected relationships are schema hints or use bounded coverage rather than full-table verification."
        )
    for table in tables:
        table.pop("_relationship_value_counts", None)

    limitations.extend([
        f"IQR tail flags use the 1.5 IQR rule only when at least {limits.min_outlier_sample_size} numeric values are available; they describe distribution tails, not confirmed errors.",
        "Correlations describe linear association and do not establish causation.",
        "Semantic roles and privacy classifications are inferred from column names and declared types; content-level DLP is not performed.",
        "This report is descriptive EDA; it does not make causal or business decisions.",
    ])

    findings = []
    for table in tables:
        findings.extend(f"{table['name']}: {finding}" for finding in table["findings"])
    if not findings and tables:
        findings.append("No high-priority deterministic data-quality flags were found in the scanned scope.")

    return {
        "version": REPORT_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "partial" if partial else "complete",
        "plan": {**plan, "validated": True},
        "overview": {
            "table_count": len(schema or {}),
            "profiled_table_count": len(tables),
            "declared_rows": declared_rows,
            "scanned_rows": scanned_rows,
            "column_count": column_count,
            "relationship_count": len(measured_relationships),
        },
        "tables": tables,
        "relationships": measured_relationships,
        "findings": findings,
        "privacy": {
            "execution": "local_only",
            "raw_rows_returned": False,
            "sensitive_category_values_redacted": True,
            "sensitive_columns": sensitive_columns,
        },
        "limits": {
            "tables": limits.max_tables,
            "columns_per_table": limits.max_columns_per_table,
            "rows_per_table": limits.max_rows_per_table,
            "numeric_columns_per_table": limits.max_numeric_columns,
            "categorical_columns_per_table": limits.max_categorical_columns,
            "correlation_columns_per_table": limits.max_correlation_columns,
            "category_tracked_values_per_column": limits.max_category_tracked_values,
            "duplicate_rows_per_table": limits.max_duplicate_rows,
            "minimum_outlier_sample_size": limits.min_outlier_sample_size,
            "timeout_seconds": limits.timeout_seconds,
        },
        "limitations": list(dict.fromkeys(limitations)),
        "trace": trace,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }
