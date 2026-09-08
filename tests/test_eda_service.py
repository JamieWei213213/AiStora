from pathlib import Path

import pytest

from engine.dataframe import DataFrame
from services.eda_service import (
    EDALimits,
    EDAPlanValidationError,
    build_eda_plan,
    generate_eda_report,
    validate_eda_plan,
)


FIXTURE = Path(__file__).parent / "fixtures" / "eda_synthetic.csv"


def fixture_schema(row_count=9):
    dataframe = DataFrame(str(FIXTURE))
    return {
        "customers": {
            "types": dataframe.get_column_types(),
            "row_count": row_count,
        }
    }


def test_eda_report_profiles_synthetic_data_and_redacts_sensitive_values():
    schema = fixture_schema()
    report = generate_eda_report(
        schema,
        lambda table: DataFrame(str(FIXTURE)),
        limits=EDALimits(timeout_seconds=5),
    )

    assert report["status"] == "complete"
    assert report["plan"]["validated"] is True
    assert report["overview"]["scanned_rows"] == 9
    assert report["privacy"]["raw_rows_returned"] is False
    table = report["tables"][0]
    assert table["duplicate_rows"] == 1
    missing = {item["column"]: item for item in table["missingness"]}
    assert missing["age"]["missing_count"] == 1
    assert missing["segment"]["missing_count"] == 1
    spend = next(item for item in table["numeric_summary"] if item["column"] == "spend")
    assert spend["max"] == 10000
    assert spend["outlier_status"] == "not_assessed_small_sample"
    assert spend["iqr_outlier_count"] is None
    assert spend["distribution_shape"] == "insufficient_sample"
    email = next(item for item in table["categorical_summary"] if item["column"] == "email")
    assert email["classification"] == "direct_identifier"
    assert email["values_redacted"] is True
    assert email["top_values"] == []
    assert table["time_summary"][0]["column"] == "signup_date"


def test_eda_assesses_iqr_tails_only_with_enough_values():
    frame = DataFrame([{"metric": 1} for _ in range(39)] + [{"metric": 100}])
    schema = {
        "measurements": {
            "types": frame.get_column_types(),
            "row_count": 40,
        }
    }

    report = generate_eda_report(
        schema,
        lambda table: frame,
        limits=EDALimits(timeout_seconds=5),
    )

    metric = report["tables"][0]["numeric_summary"][0]
    assert metric["outlier_status"] == "assessed"
    assert metric["iqr_outlier_count"] == 1
    assert metric["iqr_outlier_rate"] == 0.025
    assert metric["distribution_shape"] == "strongly_skewed_or_heavy_tailed"


def test_eda_uses_semantic_roles_and_suppresses_identifier_and_text_values():
    frame = DataFrame([
        {
            "customer_id": 101,
            "ssn_last4_fake": 1234,
            "product_name": "Starter",
            "notes": "private note one",
            "year": 2025,
            "amount": 10.0,
        },
        {
            "customer_id": 102,
            "ssn_last4_fake": 5678,
            "product_name": "Growth",
            "notes": "private note two",
            "year": 2026,
            "amount": 20.0,
        },
    ])
    schema = {
        "sales": {
            "types": frame.get_column_types(),
            "row_count": 2,
        }
    }

    report = generate_eda_report(
        schema,
        lambda table: frame,
        limits=EDALimits(timeout_seconds=5),
    )
    table = report["tables"][0]
    numeric_columns = {item["column"] for item in table["numeric_summary"]}
    categories = {item["column"]: item for item in table["categorical_summary"]}

    assert numeric_columns == {"amount"}
    assert categories["customer_id"]["semantic_role"] == "identifier"
    assert categories["customer_id"]["top_values"] == []
    assert categories["ssn_last4_fake"]["classification"] == "direct_identifier"
    assert categories["ssn_last4_fake"]["top_values"] == []
    assert categories["notes"]["semantic_role"] == "free_text"
    assert categories["notes"]["top_values"] == []
    assert categories["product_name"]["classification"] == "ordinary"
    assert categories["product_name"]["top_values"]
    assert categories["year"]["semantic_role"] == "temporal_component"


def test_duplicate_scope_is_reported_and_late_duplicates_are_detected_when_in_scope():
    frame = DataFrame([{"value": index} for index in range(11)] + [{"value": 10}])
    schema = {
        "events": {
            "types": frame.get_column_types(),
            "row_count": 12,
        }
    }

    bounded = generate_eda_report(
        schema,
        lambda table: frame,
        limits=EDALimits(max_duplicate_rows=10, timeout_seconds=5),
    )
    assert bounded["status"] == "partial"
    assert bounded["tables"][0]["duplicate_rows"] == 0
    assert bounded["tables"][0]["duplicate_scope"] == 10
    assert any("Duplicate detection covers only" in item for item in bounded["tables"][0]["warnings"])

    complete = generate_eda_report(
        schema,
        lambda table: frame,
        limits=EDALimits(max_duplicate_rows=12, timeout_seconds=5),
    )
    assert complete["status"] == "complete"
    assert complete["tables"][0]["duplicate_rows"] == 1


def test_missingness_context_identifies_status_dependent_nulls():
    rows = []
    for status in ("Open", "Pending", "Closed", "Resolved"):
        for index in range(10):
            rows.append({
                "status": status,
                "resolution_days": None if status in {"Open", "Pending"} else index + 1,
            })
    frame = DataFrame(rows)
    schema = {
        "tickets": {
            "types": frame.get_column_types(),
            "row_count": len(rows),
        }
    }

    report = generate_eda_report(
        schema,
        lambda table: frame,
        limits=EDALimits(timeout_seconds=5),
    )
    patterns = report["tables"][0]["missingness_patterns"]

    resolution = next(item for item in patterns if item["column"] == "resolution_days")
    assert resolution["group_by"] == "status"
    assert resolution["strength"] == 1
    assert "structurally expected" in resolution["summary"]


def test_eda_report_marks_bounded_scan_as_partial():
    schema = fixture_schema()
    limits = EDALimits(max_rows_per_table=3, timeout_seconds=5)

    report = generate_eda_report(
        schema,
        lambda table: DataFrame(str(FIXTURE)),
        limits=limits,
    )

    assert report["status"] == "partial"
    assert report["tables"][0]["rows_scanned"] == 3
    assert report["tables"][0]["complete_scan"] is False
    assert any("bounded leading sample" in item for item in report["limitations"])


def test_eda_plan_validation_rejects_unknown_columns_and_over_budget_rows():
    schema = fixture_schema()
    limits = EDALimits(max_rows_per_table=20)
    plan = build_eda_plan(schema, limits)
    plan["tables"][0]["columns"].append("not_a_column")

    with pytest.raises(EDAPlanValidationError, match="Unknown column"):
        validate_eda_plan(plan, schema, limits)

    plan = build_eda_plan(schema, limits)
    plan["tables"][0]["row_limit"] = 21
    with pytest.raises(EDAPlanValidationError, match="row limit"):
        validate_eda_plan(plan, schema, limits)


def test_relationship_hints_get_local_join_coverage_measurements():
    schema = {
        "customers": {"types": {"id": "int"}, "row_count": 1},
        "orders": {"types": {"customer_id": "int"}, "row_count": 1},
    }
    frames = {
        "customers": DataFrame([{"id": 1}]),
        "orders": DataFrame([{"customer_id": 1}]),
    }
    report = generate_eda_report(
        schema,
        frames.get,
        relationships=[{
            "from_table": "customers",
            "from_column": "id",
            "to_table": "orders",
            "to_column": "customer_id",
        }],
        limits=EDALimits(timeout_seconds=5),
    )

    relationship = report["relationships"][0]
    assert relationship["status"] == "data_verified"
    assert relationship["overlap_distinct_keys"] == 1
    assert relationship["from_match_rate"] == 1
    assert relationship["to_match_rate"] == 1
    assert relationship["scope_complete"] is True
