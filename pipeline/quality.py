"""Stage 3: the quality gate.

Every check is a DuckDB query against the typed view built by
``transform.typed_select`` so the gate judges exactly what would be loaded.
A check has a severity: ``fail`` quarantines the load, ``warn`` loads it
with the flag recorded in the manifest and shown in the UI, ``info`` is
just a number worth keeping.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

import duckdb

from pipeline.config import PipelineSettings
from pipeline.contract import Contract, null_markers_sql
from pipeline.duck import quote_ident


@dataclass
class Check:
    name: str
    severity: str          # fail | warn | info
    passed: bool
    message: str
    details: dict = field(default_factory=dict)


@dataclass
class QualityReport:
    status: str = "pass"   # pass | warn | fail
    row_count: int = 0
    rows_rejected: int = 0
    checks: list[Check] = field(default_factory=list)
    cast_failures: dict = field(default_factory=dict)   # column -> count
    null_rates: dict = field(default_factory=dict)      # column -> share
    duplicate_rows: int = 0

    def add(self, check: Check) -> None:
        self.checks.append(check)
        if not check.passed:
            if check.severity == "fail":
                self.status = "fail"
            elif check.severity == "warn" and self.status != "fail":
                self.status = "warn"

    @property
    def failed(self) -> bool:
        return self.status == "fail"

    def failure_reason(self) -> str:
        reasons = [check.message for check in self.checks if not check.passed and check.severity == "fail"]
        return "; ".join(reasons) if reasons else "quality checks failed"

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["failed_checks"] = [c.name for c in self.checks if not c.passed]
        return payload


def run_quality_gate(
    con: duckdb.DuckDBPyConnection,
    raw_relation: str,
    typed_relation: str,
    contract: Contract,
    settings: PipelineSettings,
    *,
    rows_rejected: int = 0,
    key_columns: list[str] | None = None,
) -> QualityReport:
    report = QualityReport(rows_rejected=int(rows_rejected))
    keys = [key for key in (key_columns or contract.key_columns) if contract.column(key)]

    # --- volume -----------------------------------------------------------
    row_count = int(con.execute(f"SELECT count(*) FROM {typed_relation}").fetchone()[0])
    report.row_count = row_count
    report.add(Check(
        "row_count", "fail", row_count >= settings.min_rows,
        f"{row_count} data rows" if row_count >= settings.min_rows
        else f"The file has {row_count} data rows; at least {settings.min_rows} required.",
        {"rows": row_count},
    ))
    if rows_rejected:
        share = rows_rejected / max(row_count + rows_rejected, 1)
        report.add(Check(
            "malformed_rows", "fail" if share > settings.cast_failure_threshold else "warn", False,
            f"{rows_rejected} row(s) had the wrong number of fields and were skipped.",
            {"rows": rows_rejected, "share": round(share, 4)},
        ))

    if row_count == 0:
        return report

    # --- one pass: cast failures and null rates per column -----------------
    pieces = []
    for column in contract.columns:
        name = column.name
        raw = f"r.{quote_ident(name)}"
        typed = f"t.{quote_ident(name)}"
        present = f"({raw} IS NOT NULL AND lower(trim({raw})) NOT IN ({null_markers_sql()}))"
        pieces.append(
            f"count(CASE WHEN {present} AND {typed} IS NULL THEN 1 END) AS {quote_ident(name + '::cast_fail')}"
        )
        pieces.append(f"count({typed}) AS {quote_ident(name + '::non_null')}")
    stats_sql = (
        f"SELECT {', '.join(pieces)} FROM {raw_relation} r POSITIONAL JOIN {typed_relation} t"
    )
    cursor = con.execute(stats_sql)
    names = [desc[0] for desc in cursor.description]
    stats = dict(zip(names, cursor.fetchone()))

    worst_share = 0.0
    for column in contract.columns:
        if column.type == "str":
            continue
        failures = int(stats.get(f"{column.name}::cast_fail", 0) or 0)
        if failures:
            report.cast_failures[column.name] = failures
            share = failures / row_count
            worst_share = max(worst_share, share)
    if report.cast_failures:
        over = {
            name: count for name, count in report.cast_failures.items()
            if count / row_count > settings.cast_failure_threshold
        }
        report.add(Check(
            "cast_failures", "fail" if over else "warn", False,
            (f"{sum(over.values())} value(s) in {', '.join(sorted(over))} do not match the "
             f"column type (more than {settings.cast_failure_threshold:.0%}).")
            if over else
            f"{sum(report.cast_failures.values())} value(s) could not be cast and were loaded as null.",
            {"columns": dict(report.cast_failures), "threshold": settings.cast_failure_threshold},
        ))
    else:
        report.add(Check("cast_failures", "warn", True, "Every typed value cast cleanly.", {}))

    for column in contract.columns:
        non_null = int(stats.get(f"{column.name}::non_null", 0) or 0)
        report.null_rates[column.name] = round(1 - non_null / row_count, 4)
    empty = [name for name, rate in report.null_rates.items() if rate >= 1.0]
    report.add(Check(
        "empty_columns", "warn", not empty,
        f"Column(s) with no values: {', '.join(empty)}." if empty else "No empty columns.",
        {"columns": empty},
    ))

    # --- keys -------------------------------------------------------------
    if keys:
        key_list = ", ".join(quote_ident(key) for key in keys)
        null_keys = int(con.execute(
            f"SELECT count(*) FROM {typed_relation} WHERE " +
            " OR ".join(f"{quote_ident(key)} IS NULL" for key in keys)
        ).fetchone()[0])
        report.add(Check(
            "key_not_null", "fail", null_keys == 0,
            f"{null_keys} row(s) have a null merge key." if null_keys else "Merge keys are never null.",
            {"rows": null_keys, "keys": keys},
        ))
        dupes = int(con.execute(
            f"SELECT coalesce(sum(n - 1), 0) FROM (SELECT count(*) AS n FROM {typed_relation} "
            f"GROUP BY {key_list} HAVING count(*) > 1)"
        ).fetchone()[0])
        report.add(Check(
            "key_unique", "fail", dupes == 0,
            f"{dupes} row(s) repeat a merge key within the file." if dupes else "Merge keys are unique in the file.",
            {"rows": dupes, "keys": keys},
        ))

    # --- exact duplicates -------------------------------------------------
    duplicates = int(con.execute(
        f"SELECT (SELECT count(*) FROM {typed_relation}) - "
        f"(SELECT count(*) FROM (SELECT DISTINCT * FROM {typed_relation}))"
    ).fetchone()[0])
    report.duplicate_rows = duplicates
    report.add(Check(
        "duplicate_rows", "info", duplicates == 0,
        f"{duplicates} exact duplicate row(s)." if duplicates else "No exact duplicate rows.",
        {"rows": duplicates},
    ))
    return report
