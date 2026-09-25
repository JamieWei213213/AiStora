"""Snapshot extract of a Google Sheets range.

Options:

``spreadsheet_id``  required
``range``           A1 range or sheet name (default: first sheet)

The secret is a service-account JSON key with read access to the sheet;
``google-auth`` (already a dependency of the Gemini SDK) signs the request.
Sheets have no change cursor, so every run is a snapshot: use ``replace``
mode, or ``merge`` with key columns when rows are edited in place.
"""

from __future__ import annotations

import csv
import json
import os
from urllib.parse import quote

from pipeline.connectors.base import Connector, ConnectorError, ConnectorState, ExtractResult

_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


class GoogleSheetsSource(Connector):
    type = "google_sheets"

    def _token(self) -> str:
        try:
            from google.auth.transport.requests import Request
            from google.oauth2 import service_account
        except ImportError as exc:  # pragma: no cover
            raise ConnectorError("google-auth is not installed.") from exc
        try:
            info = json.loads(self.secret or "")
        except json.JSONDecodeError as exc:
            raise ConnectorError("The service-account secret is not valid JSON.") from exc
        credentials = service_account.Credentials.from_service_account_info(info, scopes=[_SCOPE])
        credentials.refresh(Request())
        return credentials.token

    def extract(self, state: ConnectorState, workdir: str) -> ExtractResult:
        import requests

        options = self.config.options or {}
        spreadsheet_id = str(options.get("spreadsheet_id") or "").strip()
        if not spreadsheet_id:
            raise ConnectorError("spreadsheet_id is required.")
        rng = str(options.get("range") or "").strip()
        token = self._token()
        base = f"https://sheets.googleapis.com/v4/spreadsheets/{quote(spreadsheet_id)}"
        headers = {"Authorization": f"Bearer {token}"}
        if not rng:
            meta = requests.get(base, headers=headers, params={"fields": "sheets.properties.title"}, timeout=30)
            if meta.status_code != 200:
                raise ConnectorError(f"Sheets API error {meta.status_code}: {meta.text[:200]}")
            sheets = meta.json().get("sheets") or []
            if not sheets:
                raise ConnectorError("The spreadsheet has no sheets.")
            rng = sheets[0]["properties"]["title"]
        response = requests.get(
            f"{base}/values/{quote(rng, safe='')}",
            headers=headers,
            params={"valueRenderOption": "UNFORMATTED_VALUE", "dateTimeRenderOption": "FORMATTED_STRING"},
            timeout=60,
        )
        if response.status_code != 200:
            raise ConnectorError(f"Sheets API error {response.status_code}: {response.text[:200]}")
        values = response.json().get("values") or []
        if len(values) < 2:
            return ExtractResult(path=None, rows=0, cursor=None)
        os.makedirs(workdir, exist_ok=True)
        path = os.path.join(workdir, "extract.csv")
        width = len(values[0])
        with open(path, "w", encoding="utf-8", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\n")
            for row in values:
                row = list(row) + [""] * (width - len(row))
                writer.writerow(row[:width])
        return ExtractResult(path=path, rows=len(values) - 1, cursor=None,
                             filename=f"{self.config.dataset}.csv",
                             details={"spreadsheet_id": spreadsheet_id, "range": rng})
