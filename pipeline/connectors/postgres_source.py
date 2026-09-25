"""Incremental extract from a PostgreSQL table.

Options (``config.options``):

``table``           required, ``schema.table`` or ``table``
``cursor_column``   optional; when set only rows with a value greater than
                    the stored cursor are fetched (high-watermark
                    extraction) and the cursor advances to the maximum seen
``columns``         optional list; default ``*``
``batch_rows``      optional cap per run (default 200,000)

The DSN comes from the connector's secret. Without a cursor column every run
is a full extract, which is the right thing for small reference tables in
``replace`` mode.
"""

from __future__ import annotations

import csv
import os
import re

from pipeline.connectors.base import Connector, ConnectorError, ConnectorState, ExtractResult

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _ident(name: str) -> str:
    parts = str(name).split(".")
    if not 1 <= len(parts) <= 2 or not all(_IDENT.match(p) for p in parts):
        raise ConnectorError(f"Invalid identifier {name!r}.")
    return ".".join(f'"{p}"' for p in parts)


class PostgresSource(Connector):
    type = "postgres"

    def extract(self, state: ConnectorState, workdir: str) -> ExtractResult:
        if not self.secret:
            raise ConnectorError("The connector has no database DSN secret.")
        options = self.config.options or {}
        table = _ident(options.get("table") or "")
        cursor_column = options.get("cursor_column")
        columns = options.get("columns") or []
        batch = int(options.get("batch_rows") or 200_000)
        select = ", ".join(_ident(c) for c in columns) if columns else "*"
        params: list = []
        sql = f"SELECT {select} FROM {table}"
        if cursor_column:
            cursor_ident = _ident(cursor_column)
            if state.cursor is not None:
                sql += f" WHERE {cursor_ident} > %s"
                params.append(state.cursor)
            sql += f" ORDER BY {cursor_ident} ASC"
        sql += f" LIMIT {batch}"

        try:
            import psycopg2
        except ImportError as exc:  # pragma: no cover
            raise ConnectorError("psycopg2 is not installed.") from exc

        os.makedirs(workdir, exist_ok=True)
        path = os.path.join(workdir, "extract.csv")
        rows = 0
        new_cursor = state.cursor
        with psycopg2.connect(self.secret, connect_timeout=15) as conn:
            with conn.cursor(name="aistora_extract") as cur:
                cur.itersize = 5000
                cur.execute(sql, params)
                header = None
                with open(path, "w", encoding="utf-8", newline="") as handle:
                    writer = csv.writer(handle, lineterminator="\n")
                    for record in cur:
                        if header is None:
                            header = [desc[0] for desc in cur.description]
                            writer.writerow(header)
                        writer.writerow(["" if v is None else v for v in record])
                        rows += 1
                        if cursor_column:
                            value = record[header.index(cursor_column)]
                            if value is not None:
                                new_cursor = value
                    if header is None:
                        cur2 = conn.cursor()
                        cur2.execute(f"SELECT {select} FROM {table} LIMIT 0")
                        writer.writerow([desc[0] for desc in cur2.description])
        if rows == 0:
            return ExtractResult(path=None, rows=0, cursor=state.cursor)
        if hasattr(new_cursor, "isoformat"):
            new_cursor = new_cursor.isoformat()
        stem = str(options.get("table")).replace(".", "_")
        return ExtractResult(path=path, rows=rows, cursor=new_cursor, filename=f"{stem}.csv",
                             details={"table": options.get("table"), "cursor_column": cursor_column})
