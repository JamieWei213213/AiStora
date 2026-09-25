"""Parquet source for the engine: the curated layer written by the pipeline.

Same three-method surface as ``CsvParser`` (``get_header``,
``get_column_types``, ``parse``) so ``DataFrame`` does not care where rows
come from. Two differences matter:

* types come from the file's schema, not from sampling, so they are exact;
* ``parse(columns=...)`` reads only the requested columns. Parquet is
  columnar, so a filter on three columns of a five-million-row file reads
  three columns -- this is what lifts the practical row ceiling.

Bookkeeping columns written by the pipeline (``__load_id``, ``__row_hash``,
``__loaded_at``) are hidden.
"""

from __future__ import annotations

import math
import os

import pyarrow as pa
import pyarrow.parquet as pq

BATCH_ROWS = 65_536
HIDDEN_PREFIX = "__"


def _engine_type(arrow_type: pa.DataType) -> str:
    if pa.types.is_integer(arrow_type):
        return "int"
    if pa.types.is_floating(arrow_type) or pa.types.is_decimal(arrow_type):
        return "float"
    return "str"


class ParquetParser:
    def __init__(self, filepath: str, columns: list[str] | None = None):
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")
        self.filepath = filepath
        self._file = pq.ParquetFile(filepath)
        schema = self._file.schema_arrow
        visible = [name for name in schema.names if not name.startswith(HIDDEN_PREFIX)]
        if columns is not None:
            missing = [c for c in columns if c not in visible]
            if missing:
                raise KeyError(f"Unknown column(s): {', '.join(missing)}")
            visible = [c for c in visible if c in set(columns)]
        self.header = visible
        self.column_types = {name: _engine_type(schema.field(name).type) for name in visible}
        self._arrow_types = {name: schema.field(name).type for name in visible}
        self.encoding = "parquet"
        self.separator = None

    @property
    def row_count(self) -> int:
        return int(self._file.metadata.num_rows)

    def get_header(self):
        return self.header

    def get_column_types(self):
        return self.column_types

    def _convert(self, name: str, value):
        if value is None:
            return None
        arrow_type = self._arrow_types[name]
        if pa.types.is_floating(arrow_type):
            return value if math.isfinite(value) else None
        if pa.types.is_decimal(arrow_type):
            return float(value)
        if pa.types.is_integer(arrow_type):
            return int(value)
        if pa.types.is_boolean(arrow_type):
            return "true" if value else "false"
        if pa.types.is_date(arrow_type) or pa.types.is_timestamp(arrow_type):
            return value.isoformat()
        return str(value)

    def parse(self, cast=True, columns: list[str] | None = None):
        names = [c for c in (columns or self.header) if c in self.column_types]
        if not names:
            return
        converters = {name: self._convert for name in names}
        for batch in self._file.iter_batches(batch_size=BATCH_ROWS, columns=names):
            data = batch.to_pydict()
            for index in range(batch.num_rows):
                row = {}
                for name in names:
                    value = data[name][index]
                    row[name] = converters[name](name, value) if cast else value
                yield row

    def parse_chunks(self, chunk_size=1000, cast=True):
        batch = []
        for row in self.parse(cast=cast):
            batch.append(row)
            if len(batch) >= chunk_size:
                yield batch
                batch = []
        if batch:
            yield batch
