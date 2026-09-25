"""Dataset contracts: the inferred schema a dataset promises to keep.

The first load of a dataset infers a contract from the *whole* file (not a
1,000-row sample) and stores it. Every later load is diffed against it and
the diff is judged by explicit rules, because "the schema changed" is the
single most common way a recurring load silently corrupts a table:

* a new column          -> allowed; added as nullable, contract updated
* a missing column      -> allowed with a warning; loaded as null
* incoming type narrower than stored (int -> float column, anything -> str)
                        -> allowed; incoming values are cast to the stored type
* incoming type wider than stored (str values in an int column, ...)
                        -> blocked; the load is quarantined and the user must
                           either fix the file or reload the dataset in
                           ``replace`` mode. Iceberg only promotes int->long
                           and float->double, so widening in place is not an
                           option we can offer honestly.

Types are deliberately few: ``bool``, ``int``, ``float``, ``date``,
``timestamp``, ``str``. Money columns exported as ``$1,234.50`` or
``(120.00)`` are recognised and recorded as ``float`` with a ``currency``
transform so the value is numeric downstream.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Iterable

import duckdb

from pipeline.duck import quote_ident, quote_literal
from pipeline.manifest import utcnow_iso

# Values that mean "missing" in exports from accounting tools; kept in step
# with engine/parser.py.
NULL_MARKERS = (
    "", "-", "--", "n/a", "n.a.", "na", "nan", "null", "none", "nil",
    "#n/a", "#null!", "(blank)", "not available", "unknown",
)

BOOL_TOKENS = ("true", "false", "t", "f", "yes", "no", "y", "n")

TYPES = ("bool", "int", "float", "date", "timestamp", "str")

# ``a -> b`` is allowed when b is at least as wide as a. Incoming values of
# type a are cast to b without loss.
_WIDENS_TO = {
    "bool": {"bool", "str"},
    "int": {"int", "float", "str"},
    "float": {"float", "str"},
    "date": {"date", "timestamp", "str"},
    "timestamp": {"timestamp", "str"},
    "str": {"str"},
}

US_DATE_FORMAT = "%m/%d/%Y"
US_DATE_FORMAT_SHORT = "%m/%d/%y"


@dataclass
class ColumnContract:
    name: str
    type: str
    nullable: bool = True
    transform: str | None = None       # None | currency | date_us | date_us_short
    non_null: int = 0
    distinct: int | None = None
    added_by: str | None = None        # load_id that introduced the column

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Contract:
    dataset: str
    project_id: int
    columns: list[ColumnContract] = field(default_factory=list)
    key_columns: list[str] = field(default_factory=list)
    candidate_keys: list[str] = field(default_factory=list)
    version: int = 1
    inferred_at: str = field(default_factory=utcnow_iso)
    load_id: str | None = None
    row_count: int = 0

    def column(self, name: str) -> ColumnContract | None:
        for column in self.columns:
            if column.name == name:
                return column
        return None

    @property
    def names(self) -> list[str]:
        return [column.name for column in self.columns]

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["contract_version"] = 1
        return payload

    @classmethod
    def from_dict(cls, payload: dict) -> "Contract":
        columns = [ColumnContract(**column) for column in payload.get("columns", [])]
        return cls(
            dataset=payload["dataset"],
            project_id=int(payload["project_id"]),
            columns=columns,
            key_columns=list(payload.get("key_columns", [])),
            candidate_keys=list(payload.get("candidate_keys", [])),
            version=int(payload.get("version", 1)),
            inferred_at=payload.get("inferred_at") or utcnow_iso(),
            load_id=payload.get("load_id"),
            row_count=int(payload.get("row_count", 0) or 0),
        )

    def json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, sort_keys=True)


# --------------------------------------------------------------------------
# SQL fragments
# --------------------------------------------------------------------------

def null_markers_sql() -> str:
    return ", ".join(quote_literal(marker) for marker in NULL_MARKERS)


def cleaned_expr(column: str) -> str:
    """The raw text of a cell with null markers mapped to NULL."""
    col = quote_ident(column)
    return f"(CASE WHEN lower(trim({col})) IN ({null_markers_sql()}) THEN NULL ELSE trim({col}) END)"


def currency_expr(column: str) -> str:
    """``$1,234.50`` -> ``1234.50``; ``(120.00)`` -> ``-120.00``."""
    return (
        f"regexp_replace(regexp_replace({cleaned_expr(column)}, "
        r"'^\((.*)\)$', '-\1'), '[$,\s]', '', 'g')"
    )


def typed_expr(column: str, type_name: str, transform: str | None) -> str:
    """Expression casting a raw column to its contract type (NULL on failure)."""
    raw = cleaned_expr(column)
    if type_name == "str":
        return raw
    if type_name == "bool":
        return (
            f"(CASE WHEN lower({raw}) IN ('true','t','yes','y') THEN TRUE "
            f"WHEN lower({raw}) IN ('false','f','no','n') THEN FALSE ELSE NULL END)"
        )
    if type_name == "int":
        return f"(CASE WHEN regexp_matches({raw}, '^[+-]?[0-9]+$') THEN TRY_CAST({raw} AS BIGINT) ELSE NULL END)"
    if type_name == "float":
        if transform == "currency":
            return f"TRY_CAST({currency_expr(column)} AS DOUBLE)"
        return f"TRY_CAST({raw} AS DOUBLE)"
    if type_name == "date":
        if transform == "date_us":
            return f"CAST(try_strptime({raw}, '{US_DATE_FORMAT}') AS DATE)"
        if transform == "date_us_short":
            return f"CAST(try_strptime({raw}, '{US_DATE_FORMAT_SHORT}') AS DATE)"
        return f"(CASE WHEN regexp_matches({raw}, '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$') THEN TRY_CAST({raw} AS DATE) ELSE NULL END)"
    if type_name == "timestamp":
        return f"TRY_CAST({raw} AS TIMESTAMP)"
    raise ValueError(f"unknown contract type {type_name!r}")


# --------------------------------------------------------------------------
# Inference
# --------------------------------------------------------------------------

def _probe_columns(con: duckdb.DuckDBPyConnection, relation: str, columns: Iterable[str]) -> dict:
    """One full pass computing, per column, how many values fit each type."""
    pieces = ["count(*) AS __rows"]
    for column in columns:
        raw = cleaned_expr(column)
        c = quote_ident(column)
        pieces.extend([
            f"count({raw}) AS {quote_ident(column + '::non_null')}",
            f"count(DISTINCT {raw}) AS {quote_ident(column + '::distinct')}",
            f"count(CASE WHEN lower({raw}) IN ({', '.join(quote_literal(t) for t in BOOL_TOKENS)}) THEN 1 END) AS {quote_ident(column + '::bool')}",
            f"count(CASE WHEN regexp_matches({raw}, '^[+-]?[0-9]+$') THEN 1 END) AS {quote_ident(column + '::int')}",
            f"count(TRY_CAST({raw} AS DOUBLE)) AS {quote_ident(column + '::float')}",
            f"count(TRY_CAST({currency_expr(column)} AS DOUBLE)) AS {quote_ident(column + '::currency')}",
            f"count(CASE WHEN regexp_matches({raw}, '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}$') AND TRY_CAST({raw} AS DATE) IS NOT NULL THEN 1 END) AS {quote_ident(column + '::date')}",
            f"count(try_strptime({raw}, '{US_DATE_FORMAT}')) AS {quote_ident(column + '::date_us')}",
            f"count(try_strptime({raw}, '{US_DATE_FORMAT_SHORT}')) AS {quote_ident(column + '::date_us_short')}",
            f"count(TRY_CAST({raw} AS TIMESTAMP)) AS {quote_ident(column + '::timestamp')}",
        ])
        del c
    query = f"SELECT {', '.join(pieces)} FROM {relation}"
    cursor = con.execute(query)
    names = [desc[0] for desc in cursor.description]
    row = cursor.fetchone()
    return dict(zip(names, row))


# Order matters twice. Exact matches are tried narrowest-first so an all-digit
# column is ``int`` rather than ``float``. When nothing matches exactly, a
# type is accepted if at least ``1 - tolerance`` of the non-null values fit
# it, but ``int`` is left out of that second pass: a column that is 97%
# integers and 3% decimals must become ``float`` (lossless), never ``int``
# with 3% of its values nulled.
_EXACT_ORDER = (
    ("bool", None), ("int", None), ("float", None), ("date", None), ("timestamp", None),
    ("date_us", "date_us"), ("date_us_short", "date_us_short"), ("currency", "currency"),
)
_TOLERANT_ORDER = (
    ("bool", None), ("float", None), ("currency", "currency"), ("date", None),
    ("timestamp", None), ("date_us", "date_us"), ("date_us_short", "date_us_short"),
)
_STAT_TO_TYPE = {
    "bool": "bool", "int": "int", "float": "float", "currency": "float",
    "date": "date", "date_us": "date", "date_us_short": "date", "timestamp": "timestamp",
}


def _choose_type(stats: dict, column: str, tolerance: float = 0.0) -> tuple[str, str | None]:
    non_null = int(stats[f"{column}::non_null"])
    if non_null == 0:
        return "str", None
    for kind, transform in _EXACT_ORDER:
        if int(stats[f"{column}::{kind}"]) == non_null:
            return _STAT_TO_TYPE[kind], transform
    if tolerance > 0:
        floor = non_null * (1 - tolerance)
        for kind, transform in _TOLERANT_ORDER:
            count = int(stats[f"{column}::{kind}"])
            if count > 0 and count >= floor:
                return _STAT_TO_TYPE[kind], transform
    return "str", None


def infer_contract(
    con: duckdb.DuckDBPyConnection,
    relation: str,
    header: list[str],
    *,
    dataset: str,
    project_id: int,
    load_id: str,
    key_columns: list[str] | None = None,
    tolerance: float = 0.0,
) -> Contract:
    stats = _probe_columns(con, relation, header)
    rows = int(stats["__rows"])
    columns = []
    candidate_keys = []
    for name in header:
        type_name, transform = _choose_type(stats, name, tolerance)
        non_null = int(stats[f"{name}::non_null"])
        distinct = int(stats[f"{name}::distinct"])
        columns.append(ColumnContract(
            name=name,
            type=type_name,
            nullable=non_null < rows,
            transform=transform,
            non_null=non_null,
            distinct=distinct,
            added_by=load_id,
        ))
        # A column that is unique and never null identifies rows; the app
        # offers these as merge keys.
        if rows > 0 and non_null == rows and distinct == rows and type_name in {"int", "str"}:
            candidate_keys.append(name)
    return Contract(
        dataset=dataset,
        project_id=int(project_id),
        columns=columns,
        key_columns=list(key_columns or []),
        candidate_keys=candidate_keys,
        load_id=load_id,
        row_count=rows,
    )


# --------------------------------------------------------------------------
# Evolution
# --------------------------------------------------------------------------

@dataclass
class SchemaChanges:
    added: list[dict] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    narrowed: list[dict] = field(default_factory=list)   # incoming narrower -> cast to stored
    blocked: list[dict] = field(default_factory=list)    # incoming wider -> quarantine
    key_changes: list[str] = field(default_factory=list)

    @property
    def is_blocked(self) -> bool:
        return bool(self.blocked or self.key_changes)

    @property
    def is_empty(self) -> bool:
        return not (self.added or self.removed or self.narrowed or self.blocked or self.key_changes)

    def to_dict(self) -> dict:
        payload = asdict(self)
        payload["blocked_reason"] = self.reason()
        return payload

    def reason(self) -> str | None:
        if not self.is_blocked:
            return None
        parts = []
        for change in self.blocked:
            parts.append(
                f"column '{change['column']}' holds {change['incoming']} values but the "
                f"dataset stores it as {change['stored']}"
            )
        for key in self.key_changes:
            parts.append(f"merge key '{key}' is missing from the file")
        return (
            "Schema conflict: " + "; ".join(parts) +
            ". Fix the file, or reload the dataset in 'replace' mode to reset its schema."
        )


def diff_contract(stored: Contract, incoming: Contract) -> SchemaChanges:
    changes = SchemaChanges()
    stored_names = set(stored.names)
    incoming_names = set(incoming.names)
    for column in incoming.columns:
        if column.name not in stored_names:
            changes.added.append({"column": column.name, "type": column.type})
            continue
        current = stored.column(column.name)
        assert current is not None
        if column.type == current.type:
            continue
        if current.type in _WIDENS_TO.get(column.type, set()):
            changes.narrowed.append({
                "column": column.name, "incoming": column.type, "stored": current.type,
            })
        else:
            changes.blocked.append({
                "column": column.name, "incoming": column.type, "stored": current.type,
            })
    for name in stored.names:
        if name not in incoming_names:
            changes.removed.append(name)
    for key in stored.key_columns:
        if key not in incoming_names:
            changes.key_changes.append(key)
    return changes


def evolve_contract(stored: Contract, incoming: Contract, changes: SchemaChanges, load_id: str) -> Contract:
    """Apply an allowed diff: keep stored order and types, append new columns."""
    if changes.is_blocked:
        raise ValueError(changes.reason())
    columns = []
    for column in stored.columns:
        fresh = incoming.column(column.name)
        merged = ColumnContract(**column.to_dict())
        if fresh is not None:
            merged.non_null = fresh.non_null
            merged.distinct = fresh.distinct
            # A currency transform is only meaningful when the incoming
            # values need it; keep it if either side used it.
            if fresh.transform and not merged.transform and merged.type == fresh.type:
                merged.transform = fresh.transform
        merged.nullable = True if column.name in changes.removed else (column.nullable or (fresh.nullable if fresh else True))
        columns.append(merged)
    for added in changes.added:
        fresh = incoming.column(added["column"])
        assert fresh is not None
        column = ColumnContract(**fresh.to_dict())
        column.nullable = True
        column.added_by = load_id
        columns.append(column)
    return Contract(
        dataset=stored.dataset,
        project_id=stored.project_id,
        columns=columns,
        key_columns=list(stored.key_columns),
        candidate_keys=sorted(set(stored.candidate_keys) | set(incoming.candidate_keys)),
        version=stored.version + (0 if changes.is_empty else 1),
        inferred_at=stored.inferred_at,
        load_id=load_id,
        row_count=incoming.row_count,
    )


def engine_type(type_name: str) -> str:
    """Map a contract type onto the app engine's three types."""
    return {"int": "int", "float": "float"}.get(type_name, "str")
