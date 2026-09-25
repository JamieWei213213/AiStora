"""Stage 4: CSV -> typed, compressed Parquet (the silver layer).

DuckDB reads the UTF-8 working copy with every column as text, the contract
decides the cast for each column, and the result is written as a single
zstd-compressed Parquet file with three bookkeeping columns:

``__load_id``   the load that produced the row
``__row_hash``  sha256 over the typed values, used to de-duplicate appends
``__loaded_at`` UTC timestamp of the load

Bookkeeping columns start with ``__`` and are hidden by the app's Parquet
reader, so users only ever see their own columns.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import duckdb

from pipeline.contract import Contract, typed_expr
from pipeline.duck import quote_ident, quote_literal

RAW_VIEW = "raw_rows"
TYPED_VIEW = "typed_rows"
META_COLUMNS = ("__load_id", "__row_hash", "__loaded_at")

DUCK_TYPES = {
    "bool": "BOOLEAN",
    "int": "BIGINT",
    "float": "DOUBLE",
    "date": "DATE",
    "timestamp": "TIMESTAMP",
    "str": "VARCHAR",
}


def register_raw_csv(
    con: duckdb.DuckDBPyConnection,
    csv_path: str,
    delimiter: str,
    header: list[str],
) -> None:
    """Create ``raw_rows`` (every column VARCHAR) from the validated working copy.

    The working copy was rewritten by the validate stage with a clean header
    and no ragged rows, so the read is strict: any error here is a bug, not
    user data, and must surface.
    """
    columns_struct = ", ".join(f"{quote_literal(name)}: 'VARCHAR'" for name in header)
    con.execute(
        f"CREATE OR REPLACE TABLE {RAW_VIEW} AS SELECT * FROM read_csv("
        f"{quote_literal(csv_path)}, delim={quote_literal(delimiter)}, header=true, "
        f"columns={{{columns_struct}}}, all_varchar=true, quote='\"', escape='\"', "
        f"null_padding=false, ignore_errors=false, sample_size=-1)"
    )


def typed_select(contract: Contract, load_id: str, loaded_at: datetime | None = None) -> str:
    loaded_at = loaded_at or datetime.now(timezone.utc)
    parts = []
    hash_parts = []
    for column in contract.columns:
        expr = typed_expr(column.name, column.type, column.transform)
        parts.append(f"{expr} AS {quote_ident(column.name)}")
        hash_parts.append(f"coalesce(CAST({expr} AS VARCHAR), '\\N')")
    parts.append(f"{quote_literal(load_id)} AS __load_id")
    parts.append(
        "sha256(concat_ws('\\x1f', " + ", ".join(hash_parts) + ")) AS __row_hash"
    )
    parts.append(f"TIMESTAMP {quote_literal(loaded_at.strftime('%Y-%m-%d %H:%M:%S.%f'))} AS __loaded_at")
    return f"SELECT {', '.join(parts)} FROM {RAW_VIEW}"


def create_typed_view(con: duckdb.DuckDBPyConnection, contract: Contract, load_id: str, loaded_at=None) -> None:
    """``typed_rows`` only projects columns present in the raw file.

    A column the contract knows about but the file lacks (an allowed
    "removed" schema change) is emitted as a typed NULL so the Parquet
    schema always matches the contract.
    """
    raw_columns = {row[0] for row in con.execute(f"DESCRIBE {RAW_VIEW}").fetchall()}
    missing = [column.name for column in contract.columns if column.name not in raw_columns]
    for name in missing:
        con.execute(f"ALTER TABLE {RAW_VIEW} ADD COLUMN {quote_ident(name)} VARCHAR")
    con.execute(f"CREATE OR REPLACE VIEW {TYPED_VIEW} AS {typed_select(contract, load_id, loaded_at)}")


def write_parquet(con: duckdb.DuckDBPyConnection, destination: str, relation: str = TYPED_VIEW) -> int:
    os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
    con.execute(
        f"COPY (SELECT * FROM {relation}) TO {quote_literal(destination)} "
        "(FORMAT PARQUET, COMPRESSION ZSTD, ROW_GROUP_SIZE 122880)"
    )
    return int(con.execute(f"SELECT count(*) FROM {relation}").fetchone()[0])


def parquet_schema_sql(contract: Contract) -> dict:
    """Column -> DuckDB type, for diagnostics and the Glue table."""
    schema = {column.name: DUCK_TYPES[column.type] for column in contract.columns}
    schema.update({"__load_id": "VARCHAR", "__row_hash": "VARCHAR", "__loaded_at": "TIMESTAMP"})
    return schema
