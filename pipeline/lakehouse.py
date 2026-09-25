"""Stage 5: the curated layer, one Apache Iceberg table per dataset.

Why Iceberg instead of "just Parquet": a recurring load needs *merge*
semantics (update the invoice that changed, insert the new ones), the user
needs to see what a dataset looked like before last month's load, and a bad
load must be reversible. Iceberg gives all three -- row-level upsert,
snapshots, rollback -- with plain Parquet files underneath and no server.

Locally the catalog is a SQLite file inside the lake; on AWS it is the Glue
Data Catalog, which also makes every table queryable from Athena.

Honest limits of the PyIceberg implementation used here (0.12):

* writes are copy-on-write, so a merge rewrites the data files it touches;
* there is no concurrent-commit retry, so loads for one dataset are
  serialised by the state machine (see ``stages.curate``);
* tables are unpartitioned; at the volumes AIStora targets (files up to a
  few hundred MB) a partition spec would only add small files.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

import pyarrow as pa
import pyarrow.parquet as pq

from pipeline.config import PipelineSettings
from pipeline.contract import Contract
from pipeline.keys import ICEBERG_PREFIX, iceberg_history_table_name, iceberg_table_name
from pipeline.objectstore import ObjectStore
from pipeline.transform import META_COLUMNS

_ARROW_TYPES = {
    "bool": pa.bool_(),
    "int": pa.int64(),
    "float": pa.float64(),
    "date": pa.date32(),
    "timestamp": pa.timestamp("us"),
    "str": pa.string(),
}


class LakehouseError(RuntimeError):
    pass


class TableNotFound(LakehouseError):
    pass


@dataclass
class LoadResult:
    mode: str
    rows_in: int = 0
    rows_inserted: int = 0
    rows_updated: int = 0
    rows_skipped: int = 0
    rows_deleted: int = 0
    snapshot_id: int | None = None
    parent_snapshot_id: int | None = None
    history_rows: int = 0
    details: dict = field(default_factory=dict)

    def counts(self) -> dict:
        return {
            "rows_inserted": self.rows_inserted,
            "rows_updated": self.rows_updated,
            "rows_skipped": self.rows_skipped,
            "rows_deleted": self.rows_deleted,
            "history_rows": self.history_rows,
        }


def arrow_schema_for(contract: Contract) -> pa.Schema:
    fields = [pa.field(column.name, _ARROW_TYPES[column.type], nullable=True) for column in contract.columns]
    fields.extend([
        pa.field("__load_id", pa.string(), nullable=True),
        pa.field("__row_hash", pa.string(), nullable=True),
        pa.field("__loaded_at", pa.timestamp("us"), nullable=True),
    ])
    return pa.schema(fields)


def read_parquet_as(path: str, contract: Contract) -> pa.Table:
    """Read a silver file and coerce it to the contract's Arrow schema.

    DuckDB and PyArrow agree on almost everything; the cast is there for the
    cases they do not (large_string vs string, timestamp units), so the
    table always matches the Iceberg schema exactly.
    """
    table = pq.read_table(path)
    target = arrow_schema_for(contract)
    columns = []
    for field_ in target:
        if field_.name in table.column_names:
            columns.append(table.column(field_.name).cast(field_.type))
        else:
            columns.append(pa.nulls(table.num_rows, type=field_.type))
    return pa.table(columns, schema=target)


def _match_filter(rows: pa.Table, keys: list[str]):
    """Iceberg expression selecting every row whose key appears in ``rows``."""
    from pyiceberg.expressions import And, EqualTo, In, Or

    if len(keys) == 1:
        return In(keys[0], rows.column(keys[0]).to_pylist())
    clauses = []
    for record in rows.select(keys).to_pylist():
        clauses.append(And(*[EqualTo(key, record[key]) for key in keys]))
    return clauses[0] if len(clauses) == 1 else Or(*clauses)


def _summary(snapshot) -> dict:
    summary = getattr(snapshot, "summary", None)
    if summary is None:
        return {}
    payload = dict(getattr(summary, "additional_properties", {}) or {})
    operation = getattr(summary, "operation", None)
    payload["operation"] = getattr(operation, "value", operation)
    return payload


class Lakehouse:
    def __init__(self, settings: PipelineSettings, store: ObjectStore):
        self.settings = settings
        self.store = store
        self._catalog = None

    # ----- catalog ----------------------------------------------------------
    @property
    def warehouse(self) -> str:
        if self.settings.warehouse:
            return self.settings.warehouse
        if self.settings.is_aws:
            return f"s3://{self.settings.lake_bucket}/{ICEBERG_PREFIX}"
        return "file://" + os.path.join(os.path.abspath(self.settings.lake_root), ICEBERG_PREFIX)

    @property
    def catalog(self):
        if self._catalog is None:
            self._catalog = self._open_catalog()
        return self._catalog

    def _open_catalog(self):
        if self.settings.catalog_type == "glue":
            from pyiceberg.catalog.glue import GlueCatalog

            properties = {
                "warehouse": self.warehouse,
                "glue.region": self.settings.aws_region,
                "s3.region": self.settings.aws_region,
            }
            if self.settings.s3_endpoint_url:
                properties["s3.endpoint"] = self.settings.s3_endpoint_url
            catalog = GlueCatalog("aistora", **properties)
        elif self.settings.catalog_type == "sql":
            from pyiceberg.catalog.sql import SqlCatalog

            root = os.path.join(os.path.abspath(self.settings.lake_root), ICEBERG_PREFIX)
            os.makedirs(root, exist_ok=True)
            catalog = SqlCatalog(
                "aistora",
                uri=f"sqlite:///{os.path.join(root, 'catalog.db')}",
                warehouse=self.warehouse,
            )
        else:
            raise LakehouseError(f"Unknown ICEBERG_CATALOG {self.settings.catalog_type!r}.")
        try:
            catalog.create_namespace_if_not_exists(self.settings.catalog_namespace)
        except Exception as exc:  # pragma: no cover - provider specific
            raise LakehouseError(f"Could not open the Iceberg namespace: {exc}") from exc
        return catalog

    def identifier(self, project_id: int, dataset: str, history: bool = False) -> tuple[str, str]:
        name = (iceberg_history_table_name if history else iceberg_table_name)(project_id, dataset)
        return (self.settings.catalog_namespace, name)

    # ----- tables -----------------------------------------------------------
    def load_table(self, project_id: int, dataset: str, history: bool = False):
        from pyiceberg.exceptions import NoSuchTableError

        try:
            return self.catalog.load_table(self.identifier(project_id, dataset, history))
        except NoSuchTableError:
            return None

    def table_exists(self, project_id: int, dataset: str) -> bool:
        return self.load_table(project_id, dataset) is not None

    def drop_table(self, project_id: int, dataset: str) -> None:
        for history in (False, True):
            table = self.load_table(project_id, dataset, history)
            if table is not None:
                self.catalog.drop_table(self.identifier(project_id, dataset, history))

    def ensure_table(self, project_id: int, dataset: str, contract: Contract, *, replace: bool = False):
        """Create the table for a contract, or evolve an existing one.

        ``replace`` drops and recreates: that is how a user resets a dataset
        whose schema conflict blocked a load. Otherwise only *additive*
        evolution happens here; anything else was blocked by the profile
        stage before this point.
        """
        schema = arrow_schema_for(contract)
        table = self.load_table(project_id, dataset)
        if table is not None and replace:
            self.drop_table(project_id, dataset)
            table = None
        if table is None:
            return self.catalog.create_table(
                self.identifier(project_id, dataset),
                schema=schema,
                properties={
                    "format-version": "2",
                    "write.parquet.compression-codec": "zstd",
                    "aistora.dataset": dataset,
                    "aistora.project_id": str(int(project_id)),
                },
            )
        existing = {f.name for f in table.schema().fields}
        missing = [f for f in schema if f.name not in existing]
        if missing:
            with table.update_schema() as update:
                for f in missing:
                    update.union_by_name(pa.schema([f]))
            table.refresh()
        return table

    # ----- loading ----------------------------------------------------------
    def load(self, project_id: int, dataset: str, contract: Contract, arrow: pa.Table, *,
             mode: str, load_id: str, key_columns: list[str] | None = None,
             keep_history: bool = False, reset_schema: bool = False) -> LoadResult:
        if mode not in {"replace", "append", "merge"}:
            raise LakehouseError(f"Unknown load mode {mode!r}.")
        if reset_schema and mode != "replace":
            raise LakehouseError("Only a replace load may reset a dataset's schema.")
        table = self.ensure_table(project_id, dataset, contract, replace=reset_schema)
        # Iceberg matches Arrow columns by position as well as name, so put
        # the incoming columns in the table's order (new columns land last).
        table_columns = table.schema().column_names
        for name in table_columns:
            if name not in arrow.column_names:
                field_type = arrow_schema_for(contract).field(name).type if name in arrow_schema_for(contract).names else pa.string()
                arrow = arrow.append_column(name, pa.nulls(arrow.num_rows, type=field_type))
        arrow = arrow.select(table_columns)
        result = LoadResult(mode=mode, rows_in=arrow.num_rows)
        result.parent_snapshot_id = table.current_snapshot().snapshot_id if table.current_snapshot() else None
        props = {"aistora.load_id": load_id, "aistora.mode": mode}

        if mode == "replace":
            table.overwrite(arrow, snapshot_properties=props)
            result.rows_inserted = arrow.num_rows
            result.rows_deleted = self._previous_row_count(table, result.parent_snapshot_id)

        elif mode == "append":
            existing = self._existing_hashes(table)
            if existing:
                mask = pa.compute.invert(pa.compute.is_in(arrow.column("__row_hash"), value_set=pa.array(existing)))
                fresh = arrow.filter(mask)
            else:
                fresh = arrow
            result.rows_skipped = arrow.num_rows - fresh.num_rows
            if fresh.num_rows:
                table.append(fresh, snapshot_properties=props)
            result.rows_inserted = fresh.num_rows

        else:  # merge
            keys = list(key_columns or contract.key_columns)
            if not keys:
                raise LakehouseError("Merge loads need at least one key column.")
            inserts, updates, unchanged = self._classify_merge(table, arrow, keys)
            result.rows_skipped = unchanged
            changed = pa.concat_tables([inserts, updates]) if updates.num_rows else inserts
            if changed.num_rows:
                # Delete-then-append inside one transaction rather than
                # ``Table.upsert``: PyIceberg 0.12's upsert reads matched rows
                # with each data file's own schema, so it breaks on the first
                # merge after a column was added. This path projects through
                # the table schema and commits atomically.
                with table.transaction() as tx:
                    if updates.num_rows:
                        tx.delete(_match_filter(updates, keys), snapshot_properties=props)
                    tx.append(changed, snapshot_properties=props)
                result.rows_inserted = int(inserts.num_rows)
                result.rows_updated = int(updates.num_rows)
            if keep_history:
                result.history_rows = self._append_history(
                    project_id, dataset, contract, keys, inserts, updates, load_id,
                )

        table.refresh()
        current = table.current_snapshot()
        result.snapshot_id = current.snapshot_id if current else None
        return result

    def _previous_row_count(self, table, snapshot_id) -> int:
        if snapshot_id is None:
            return 0
        snapshot = table.snapshot_by_id(snapshot_id)
        if snapshot is None:
            return 0
        try:
            return int(_summary(snapshot).get("total-records", 0) or 0)
        except (TypeError, ValueError):
            return 0

    def _existing_hashes(self, table) -> list[str]:
        if table.current_snapshot() is None:
            return []
        scanned = table.scan(selected_fields=("__row_hash",)).to_arrow()
        return scanned.column("__row_hash").to_pylist() if scanned.num_rows else []

    def _classify_merge(self, table, arrow: pa.Table, keys: list[str]):
        """Split incoming rows into inserts, changed rows and unchanged rows."""
        import duckdb

        if table.current_snapshot() is None:
            return arrow, arrow.slice(0, 0), 0
        existing = table.scan(selected_fields=tuple(keys) + ("__row_hash",)).to_arrow()
        con = duckdb.connect()
        con.register("incoming", arrow)
        con.register("existing", existing)
        join = " AND ".join(f'i."{k}" IS NOT DISTINCT FROM e."{k}"' for k in keys)
        inserts = con.execute(
            f"SELECT i.* FROM incoming i LEFT JOIN existing e ON {join} WHERE e.__row_hash IS NULL"
        ).fetch_arrow_table()
        updates = con.execute(
            f"SELECT i.* FROM incoming i JOIN existing e ON {join} WHERE e.__row_hash <> i.__row_hash"
        ).fetch_arrow_table()
        unchanged = arrow.num_rows - inserts.num_rows - updates.num_rows
        con.close()
        return inserts.cast(arrow.schema), updates.cast(arrow.schema), unchanged

    def _append_history(self, project_id, dataset, contract, keys, inserts, updates, load_id) -> int:
        """Type-2 history as append-only row versions.

        Each changed or new row is appended with ``__valid_from`` and a
        ``__change`` marker; ``valid_to`` is the next version's
        ``__valid_from`` and is computed at read time with a window
        function, which keeps the write path a plain append.
        """
        rows = inserts.num_rows + updates.num_rows
        if rows == 0:
            return 0
        schema = arrow_schema_for(contract)
        history_schema = pa.schema(
            list(schema) + [pa.field("__valid_from", pa.timestamp("us")), pa.field("__change", pa.string())]
        )
        table = self.load_table(project_id, dataset, history=True)
        if table is None:
            table = self.catalog.create_table(
                self.identifier(project_id, dataset, history=True),
                schema=history_schema,
                properties={"format-version": "2", "aistora.dataset": dataset, "aistora.kind": "history"},
            )
        else:
            existing = {f.name for f in table.schema().fields}
            missing = [f for f in history_schema if f.name not in existing]
            if missing:
                with table.update_schema() as update:
                    for f in missing:
                        update.union_by_name(pa.schema([f]))
                table.refresh()
        import datetime as _dt

        now = _dt.datetime.utcnow().replace(tzinfo=None)

        def stamp(batch: pa.Table, change: str) -> pa.Table:
            if batch.num_rows == 0:
                return batch
            batch = batch.append_column("__valid_from", pa.array([now] * batch.num_rows, pa.timestamp("us")))
            batch = batch.append_column("__change", pa.array([change] * batch.num_rows, pa.string()))
            return batch.select(history_schema.names).cast(history_schema)

        parts = [stamp(inserts, "insert"), stamp(updates, "update")]
        payload = pa.concat_tables([p for p in parts if p.num_rows])
        table.append(payload, snapshot_properties={"aistora.load_id": load_id})
        return payload.num_rows

    # ----- reading ----------------------------------------------------------
    def materialize_current(self, project_id: int, dataset: str, destination: str) -> int:
        """Write the table's current snapshot as one Parquet file for the app."""
        table = self.load_table(project_id, dataset)
        if table is None:
            raise TableNotFound(f"{dataset} has no curated table")
        if table.current_snapshot():
            arrow = table.scan().to_arrow()
        else:
            arrow = table.schema().as_arrow().empty_table()
        os.makedirs(os.path.dirname(destination) or ".", exist_ok=True)
        pq.write_table(arrow, destination, compression="zstd")
        return arrow.num_rows

    def snapshots(self, project_id: int, dataset: str) -> list[dict]:
        table = self.load_table(project_id, dataset)
        if table is None:
            return []
        current = table.current_snapshot().snapshot_id if table.current_snapshot() else None
        out = []
        for snapshot in table.snapshots():
            summary = _summary(snapshot)
            out.append({
                "snapshot_id": snapshot.snapshot_id,
                "parent_snapshot_id": snapshot.parent_snapshot_id,
                "timestamp_ms": snapshot.timestamp_ms,
                "operation": summary.get("operation"),
                "load_id": summary.get("aistora.load_id"),
                "mode": summary.get("aistora.mode"),
                "total_records": int(summary.get("total-records", 0) or 0),
                "is_current": snapshot.snapshot_id == current,
            })
        return out

    def rollback_to(self, project_id: int, dataset: str, snapshot_id: int) -> int:
        table = self.load_table(project_id, dataset)
        if table is None:
            raise TableNotFound(dataset)
        table.manage_snapshots().rollback_to_snapshot(int(snapshot_id)).commit()
        table.refresh()
        return table.current_snapshot().snapshot_id

    def row_count(self, project_id: int, dataset: str) -> int:
        table = self.load_table(project_id, dataset)
        if table is None or table.current_snapshot() is None:
            return 0
        return self._previous_row_count(table, table.current_snapshot().snapshot_id)
