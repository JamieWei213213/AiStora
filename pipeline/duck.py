"""DuckDB connections configured for the lake.

DuckDB does the heavy lifting in every stage: full-file type probing,
quality checks, CSV -> Parquet conversion and the gold-mart queries. On AWS
the ``httpfs`` extension is loaded and credentials come from the default
chain (the Lambda role), so the same ``s3://`` paths work in every stage.
"""

from __future__ import annotations

import os

import duckdb

from pipeline.config import PipelineSettings


def connect(settings: PipelineSettings, memory_limit: str | None = None) -> duckdb.DuckDBPyConnection:
    con = duckdb.connect(database=":memory:")
    threads = max(1, min(os.cpu_count() or 1, 4))
    con.execute(f"SET threads TO {threads}")
    if memory_limit:
        con.execute(f"SET memory_limit = '{memory_limit}'")
    # Spill to the scratch directory rather than failing on large sorts.
    os.makedirs(settings.scratch_dir, exist_ok=True)
    con.execute(f"SET temp_directory = '{settings.scratch_dir}/duckdb-tmp'")
    con.execute("SET preserve_insertion_order = false")
    # Lambda's /tmp is a fresh mount at runtime, so extensions baked into the
    # image live in a read-only directory named by this variable.
    extension_dir = os.environ.get("DUCKDB_EXTENSION_DIRECTORY")
    if extension_dir:
        con.execute(f"SET extension_directory = '{extension_dir}'")
    if settings.is_aws:
        con.execute("INSTALL httpfs; LOAD httpfs;")
        con.execute("CREATE OR REPLACE SECRET lake (TYPE s3, PROVIDER credential_chain, "
                    f"REGION '{settings.aws_region}')")
        if settings.s3_endpoint_url:
            endpoint = settings.s3_endpoint_url.replace("https://", "").replace("http://", "")
            use_ssl = "true" if settings.s3_endpoint_url.startswith("https") else "false"
            con.execute(
                "CREATE OR REPLACE SECRET lake (TYPE s3, PROVIDER credential_chain, "
                f"REGION '{settings.aws_region}', ENDPOINT '{endpoint}', USE_SSL {use_ssl}, URL_STYLE 'path')"
            )
    return con


def quote_ident(name: str) -> str:
    return '"' + str(name).replace('"', '""') + '"'


def quote_literal(value: str) -> str:
    return "'" + str(value).replace("'", "''") + "'"
