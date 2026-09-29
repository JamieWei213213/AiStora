"""Auto analyze (insights): deterministic findings with chart specs."""
import csv
from pathlib import Path

import pytest
from flask import Flask

from engine.dataframe import DataFrame
from services.insights_service import InsightLimits, generate_insights

REPO_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DIR = REPO_ROOT / "static" / "samples" / "coffee_shop"


def _sample_schema():
    schema = {}
    for name in ("customers", "orders", "products"):
        frame = DataFrame(str(SAMPLE_DIR / f"{name}.csv"))
        schema[name] = {"types": frame.get_column_types(), "row_count": len(frame)}
    return schema


def _loader(name):
    return DataFrame(str(SAMPLE_DIR / f"{name}.csv"))


def test_sample_database_yields_business_findings_not_price_sums():
    report = generate_insights(_sample_schema(), _loader)
    titles = [f["title"] for f in report["findings"]]

    assert report["status"] == "complete"
    # The fact table leads and its trend comes first.
    assert titles[0].startswith("Total order total per month")
    assert "Total order total by channel" in titles
    assert "Total order total by status" in titles
    # Cross-table breakdowns through customer_id / product_id.
    assert "Total order total by category (via products)" in titles
    assert "Total order total by segment (via customers)" in titles
    # Per-unit prices are averaged, never summed; age is averaged.
    assert "Average unit price by category" in titles
    assert not any(t.startswith("Total unit price") for t in titles)
    assert not any(t.startswith("Total age") for t in titles)
    # Name parts are not entities to rank; identical breakdowns are deduplicated.
    assert not any("first name" in t for t in titles)
    assert "Average age by state" not in titles  # same numbers as "by city"
    # Every finding is renderable and actionable.
    for finding in report["findings"]:
        assert finding["kind"] in {"bar", "line", "histogram"}
        assert finding["chart"]["labels"] and len(finding["chart"]["labels"]) == len(finding["chart"]["values"])
        assert finding["takeaway"].endswith(".")
        assert finding["follow_up"]
    assert len(report["headline"]) == 3


def test_breakdown_takeaways_describe_shares():
    report = generate_insights(_sample_schema(), _loader)
    by_status = next(f for f in report["findings"] if f["title"] == "Total order total by status")
    assert by_status["chart"]["labels"][0] == "delivered"
    assert "% of total order total" in by_status["takeaway"]
    assert by_status["rows"][0]["share_pct"] > 50
    trend = report["findings"][0]
    assert trend["kind"] == "line"
    assert "Best month" in trend["takeaway"]
    assert "second half vs first" in trend["takeaway"]


def test_tables_without_recognisable_columns_produce_no_findings(tmp_path):
    path = tmp_path / "codes.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["code_id", "notes"])
        for i in range(30):
            writer.writerow([i, f"free text {i}"])
    frame = DataFrame(str(path))
    report = generate_insights({"codes": {"types": frame.get_column_types()}}, lambda name: frame)
    assert report["findings"] == []
    assert report["tables"][0]["kpis"][0] == {"label": "Rows", "value": 30, "format": "int"}


def test_group_overflow_and_row_limits_are_respected(tmp_path):
    path = tmp_path / "wide.csv"
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["customer_name", "amount", "status"])
        for i in range(500):
            writer.writerow([f"cust{i}", i % 17, "open" if i % 3 else "closed"])
    frame = DataFrame(str(path))
    limits = InsightLimits(max_rows_per_table=200, max_distinct_groups=50)
    report = generate_insights({"sales": {"types": frame.get_column_types()}}, lambda name: frame, limits=limits)
    assert report["tables"][0]["rows_scanned"] == 200
    assert report["tables"][0]["truncated"] is True
    titles = [f["title"] for f in report["findings"]]
    assert "Total amount by status" in titles
    assert not any("customer name" in t for t in titles)  # overflowed, so skipped


def test_insights_route_requires_login_and_tables():
    from routes.insights import insights_bp

    app = Flask(__name__)
    app.config.update(TESTING=True, SECRET_KEY="test")
    app.register_blueprint(insights_bp)
    client = app.test_client()
    assert client.post("/api/insights").status_code == 401
    with client.session_transaction() as flask_session:
        flask_session["user_id"] = 1
    assert client.post("/api/insights").status_code == 400
