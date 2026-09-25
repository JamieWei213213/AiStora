"""Pipeline settings, read once from the environment.

The Flask app, the Lambda image and the local runner all build a
``PipelineSettings`` from the same variables so a value is never configured in
two places. ``PIPELINE_BACKEND`` picks the runtime:

* ``local``  -- the lake is a directory (``LAKE_ROOT``), the Iceberg catalog is
  a SQLite file inside it, and loads run in-process. Used by docker-compose,
  development and the test-suite. No AWS credentials are needed.
* ``aws``    -- the lake is an S3 bucket (``LAKE_BUCKET``), the catalog is AWS
  Glue, and loads are executed by the Step Functions state machine.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return int(value)


def _float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return float(value)


class PipelineConfigurationError(RuntimeError):
    """The pipeline is not safe to start with the given settings."""


@dataclass(frozen=True)
class PipelineSettings:
    backend: str = "local"
    lake_root: str = "instance/lake"
    lake_bucket: str | None = None
    aws_region: str = "us-west-2"
    s3_endpoint_url: str | None = None

    # Iceberg catalog. ``sql`` (SQLite file under the lake root) locally,
    # ``glue`` on AWS. ``namespace`` is the Glue database name.
    catalog_type: str = "sql"
    catalog_namespace: str = "aistora"
    warehouse: str | None = None

    # Upload shape limits. These mirror the app's limits so a file rejected
    # by the app is also rejected by a connector that bypasses the app.
    max_raw_bytes: int = 500 * 1024 * 1024
    max_columns: int = 200
    max_column_name_chars: int = 64
    # Share of typed values that may fail to cast before the load is
    # quarantined instead of loaded with nulls.
    cast_failure_threshold: float = 0.05
    min_rows: int = 1

    # Event sink (product telemetry).
    events_enabled: bool = True
    events_flush_rows: int = 500
    events_flush_seconds: float = 30.0

    # Working directory for downloaded raw files and Parquet output. Lambda
    # only offers /tmp; docker-compose mounts nothing else either.
    scratch_dir: str = "/tmp/aistora-pipeline"

    # Nightly maintenance.
    compaction_min_files: int = 8
    snapshot_retention_days: int = 30

    # dbt (telemetry marts).
    dbt_project_dir: str = field(default_factory=lambda: os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "dbt",
    ))

    @property
    def is_aws(self) -> bool:
        return self.backend == "aws"

    @property
    def lake_uri(self) -> str:
        """Root of the lake as DuckDB and PyIceberg address it."""
        if self.is_aws:
            return f"s3://{self.lake_bucket}"
        return os.path.abspath(self.lake_root)

    def validate(self) -> "PipelineSettings":
        if self.backend not in {"local", "aws"}:
            raise PipelineConfigurationError(
                "PIPELINE_BACKEND must be 'local' or 'aws'."
            )
        if self.is_aws and not self.lake_bucket:
            raise PipelineConfigurationError(
                "LAKE_BUCKET is required when PIPELINE_BACKEND=aws."
            )
        if self.is_aws and self.catalog_type != "glue":
            raise PipelineConfigurationError(
                "ICEBERG_CATALOG must be 'glue' when PIPELINE_BACKEND=aws; a "
                "SQLite catalog on Lambda's ephemeral disk would lose every "
                "table on the next cold start."
            )
        if not 0 <= self.cast_failure_threshold <= 1:
            raise PipelineConfigurationError(
                "PIPELINE_CAST_FAILURE_THRESHOLD must be between 0 and 1."
            )
        return self


def settings_from_env(**overrides) -> PipelineSettings:
    backend = (os.environ.get("PIPELINE_BACKEND") or "local").strip().lower()
    lake_root = os.environ.get("LAKE_ROOT") or os.path.join("instance", "lake")
    catalog_default = "glue" if backend == "aws" else "sql"
    values = dict(
        backend=backend,
        lake_root=lake_root,
        lake_bucket=os.environ.get("LAKE_BUCKET") or None,
        aws_region=os.environ.get("AWS_REGION", "us-west-2"),
        s3_endpoint_url=os.environ.get("S3_ENDPOINT_URL") or None,
        catalog_type=(os.environ.get("ICEBERG_CATALOG") or catalog_default).strip().lower(),
        catalog_namespace=os.environ.get("ICEBERG_NAMESPACE", "aistora"),
        warehouse=os.environ.get("ICEBERG_WAREHOUSE") or None,
        max_raw_bytes=_int("PIPELINE_MAX_RAW_BYTES", 500 * 1024 * 1024),
        max_columns=_int("MAX_UPLOAD_COLUMNS", 200),
        max_column_name_chars=_int("MAX_COLUMN_NAME_CHARS", 64),
        cast_failure_threshold=_float("PIPELINE_CAST_FAILURE_THRESHOLD", 0.05),
        min_rows=_int("PIPELINE_MIN_ROWS", 1),
        events_enabled=_bool("PIPELINE_EVENTS", True),
        events_flush_rows=_int("PIPELINE_EVENTS_FLUSH_ROWS", 500),
        events_flush_seconds=_float("PIPELINE_EVENTS_FLUSH_SECONDS", 30.0),
        scratch_dir=os.environ.get("PIPELINE_SCRATCH_DIR", "/tmp/aistora-pipeline"),
        compaction_min_files=_int("PIPELINE_COMPACTION_MIN_FILES", 8),
        snapshot_retention_days=_int("PIPELINE_SNAPSHOT_RETENTION_DAYS", 30),
    )
    if os.environ.get("PIPELINE_DBT_PROJECT_DIR"):
        values["dbt_project_dir"] = os.environ["PIPELINE_DBT_PROJECT_DIR"]
    values.update(overrides)
    return PipelineSettings(**values).validate()
