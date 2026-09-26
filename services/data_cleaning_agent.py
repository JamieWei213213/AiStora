import csv
import hashlib
import os
import re

from engine.parser import detect_delimiter, detect_encoding


NULL_TOKENS = {"", "na", "n/a", "null", "none"}

# Share of non-empty cells that must parse for a text column to be converted.
NUMERIC_COLUMN_THRESHOLD = 0.9
MIN_NUMERIC_SAMPLES = 3

BOOLEAN_TRUE = {"true", "t", "yes", "y"}
BOOLEAN_FALSE = {"false", "f", "no", "n"}
BOOLEAN_TOKENS = BOOLEAN_TRUE | BOOLEAN_FALSE

_CURRENCY_CHARS = "$€£¥₹"
_CURRENCY_CODES = ("usd", "eur", "gbp", "cad", "aud", "chf", "jpy", "inr")
_PLAIN_NUMBER = re.compile(r"^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$")
_THOUSANDS_NUMBER = re.compile(r"^[+-]?\d{1,3}(,\d{3})+(\.\d+)?$")
_DECIMAL_COMMA_NUMBER = re.compile(r"^[+-]?\d+,\d{1,2}$")
_PERCENT_SUFFIX = re.compile(r"\s*%$")


class CleaningLimitExceeded(Exception):
    pass


def file_fingerprint(filepath):
    stat = os.stat(filepath)
    return {"size": stat.st_size, "modified_ns": stat.st_mtime_ns}


def normalize_headers(headers):
    normalized = []
    changes = []
    used = set()
    for index, original in enumerate(headers, start=1):
        cleaned = re.sub(r"\s+", "_", str(original or "").strip())
        cleaned = re.sub(r"[^A-Za-z0-9_]", "_", cleaned)
        cleaned = re.sub(r"_+", "_", cleaned).strip("_") or f"column_{index}"
        candidate = cleaned
        suffix = 2
        while candidate.casefold() in used:
            candidate = f"{cleaned}_{suffix}"
            suffix += 1
        used.add(candidate.casefold())
        normalized.append(candidate)
        if candidate != original:
            changes.append({"from": original, "to": candidate})
    return normalized, changes


def normalize_cell(value):
    original = "" if value is None else str(value)
    trimmed = original.strip()
    was_trimmed = trimmed != original
    is_null_token = trimmed.casefold() in NULL_TOKENS
    normalized = "" if is_null_token else trimmed
    standardized_null = is_null_token and trimmed != ""
    return normalized, was_trimmed, standardized_null


def is_plain_number(value):
    return bool(_PLAIN_NUMBER.match(value))


def parse_formatted_number(value):
    """Turn spreadsheet-formatted text into a plain number string, or None.

    Handles ``$1,234.50``, ``USD 1,234``, ``(123.45)`` accounting negatives,
    ``12%`` / ``7.5 %`` (the percent sign is dropped, the scale is kept) and
    ``1234,50`` decimal commas. A value that is already a plain number is
    returned unchanged so callers can tell "converted" from "untouched".
    """
    text = str(value).strip()
    if not text:
        return None
    if is_plain_number(text):
        return text
    negative = False
    if text.startswith("(") and text.endswith(")"):
        negative, text = True, text[1:-1].strip()
    if text.startswith("-"):
        negative, text = True, text[1:].strip()
    elif text.startswith("+"):
        text = text[1:].strip()
    text = _PERCENT_SUFFIX.sub("", text)
    lowered = text.casefold()
    for code in _CURRENCY_CODES:
        if lowered.startswith(code):
            text = text[len(code):].strip()
            break
        if lowered.endswith(code):
            text = text[: -len(code)].strip()
            break
    text = text.strip(_CURRENCY_CHARS).strip()
    if text.startswith("-"):
        negative, text = True, text[1:].strip()
    if _THOUSANDS_NUMBER.match(text):
        text = text.replace(",", "")
    elif _DECIMAL_COMMA_NUMBER.match(text):
        text = text.replace(",", ".")
    if not is_plain_number(text):
        return None
    return f"-{text}" if negative and not text.startswith("-") else text


def normalize_boolean(value):
    lowered = str(value).strip().casefold()
    if lowered in BOOLEAN_TRUE:
        return "true"
    if lowered in BOOLEAN_FALSE:
        return "false"
    return None


def _row_digest(values):
    encoded = "\x1f".join(values).encode("utf-8", errors="replace")
    return hashlib.blake2b(encoded, digest_size=16).digest()


def _open_reader(filepath):
    encoding = detect_encoding(filepath)
    delimiter = detect_delimiter(filepath, encoding)
    handle = open(filepath, "r", encoding=encoding, newline="")
    return handle, csv.reader(handle, delimiter=delimiter), delimiter


class _ColumnTypeStats:
    """Per-column tallies that decide the numeric and boolean conversions."""

    def __init__(self, headers):
        self.non_empty = {column: 0 for column in headers}
        self.plain_numeric = {column: 0 for column in headers}
        self.formatted_numeric = {column: 0 for column in headers}
        self.numeric_examples = {column: [] for column in headers}
        self.boolean_like = {column: 0 for column in headers}
        self.boolean_canonical = {column: 0 for column in headers}

    def observe(self, column, value):
        if value == "":
            return
        self.non_empty[column] += 1
        if is_plain_number(value):
            self.plain_numeric[column] += 1
        elif parse_formatted_number(value) is not None:
            self.formatted_numeric[column] += 1
            if len(self.numeric_examples[column]) < 3:
                self.numeric_examples[column].append(value)
        lowered = value.casefold()
        if lowered in BOOLEAN_TOKENS:
            self.boolean_like[column] += 1
            if value in ("true", "false"):
                self.boolean_canonical[column] += 1

    def numeric_columns(self):
        result = []
        for column, total in self.non_empty.items():
            if total < MIN_NUMERIC_SAMPLES or not self.formatted_numeric[column]:
                continue
            parseable = self.plain_numeric[column] + self.formatted_numeric[column]
            if parseable / total >= NUMERIC_COLUMN_THRESHOLD:
                result.append({
                    "column": column,
                    "converted_cells": self.formatted_numeric[column],
                    "unparsed_cells": total - parseable,
                    "examples": list(self.numeric_examples[column]),
                })
        return result

    def boolean_columns(self):
        result = []
        for column, total in self.non_empty.items():
            if total < MIN_NUMERIC_SAMPLES or self.boolean_like[column] != total:
                continue
            if self.boolean_canonical[column] == total:
                continue  # already true/false
            result.append({
                "column": column,
                "converted_cells": total - self.boolean_canonical[column],
            })
        return result


def build_cleaning_preview(filepath, max_rows=250_000):
    handle, reader, delimiter = _open_reader(filepath)
    with handle:
        try:
            headers = next(reader)
        except StopIteration:
            raise ValueError("The CSV file is empty.")

        cleaned_headers, header_changes = normalize_headers(headers)
        missing_by_column = {column: 0 for column in cleaned_headers}
        stats = _ColumnTypeStats(cleaned_headers)
        seen = set()
        total_rows = 0
        duplicate_rows = 0
        empty_rows = 0
        malformed_rows = 0
        trimmed_cells = 0
        standardized_nulls = 0

        for raw_row in reader:
            total_rows += 1
            if total_rows > max_rows:
                raise CleaningLimitExceeded(
                    f"Cleaning preview exceeded the {max_rows:,}-row safety limit."
                )
            if len(raw_row) != len(headers):
                malformed_rows += 1
            adjusted = (raw_row + [""] * len(headers))[:len(headers)]
            normalized = []
            for index, value in enumerate(adjusted):
                cleaned, was_trimmed, standardized_null = normalize_cell(value)
                normalized.append(cleaned)
                trimmed_cells += int(was_trimmed)
                standardized_nulls += int(standardized_null)
                column = cleaned_headers[index]
                if cleaned == "":
                    missing_by_column[column] += 1
                stats.observe(column, cleaned)

            if all(value == "" for value in normalized):
                empty_rows += 1
                continue
            digest = _row_digest(normalized)
            if digest in seen:
                duplicate_rows += 1
            else:
                seen.add(digest)

    numeric_columns = stats.numeric_columns()
    boolean_columns = stats.boolean_columns()

    actions = []
    if header_changes:
        actions.append({
            "id": "normalize_headers",
            "description": "Normalize blank, spaced, duplicated, or punctuated headers.",
            "affected": len(header_changes),
        })
    if trimmed_cells:
        actions.append({
            "id": "trim_whitespace",
            "description": "Trim leading and trailing whitespace from cells.",
            "affected": trimmed_cells,
        })
    if standardized_nulls:
        actions.append({
            "id": "standardize_nulls",
            "description": "Convert NA, N/A, null, and none markers to empty values.",
            "affected": standardized_nulls,
        })
    if numeric_columns:
        names = ", ".join(item["column"] for item in numeric_columns)
        actions.append({
            "id": "parse_numbers",
            "description": (
                "Convert currency, percent and thousands-separated text to plain "
                f"numbers so the columns can be summed and charted ({names})."
            ),
            "affected": sum(item["converted_cells"] for item in numeric_columns),
        })
    if boolean_columns:
        names = ", ".join(item["column"] for item in boolean_columns)
        actions.append({
            "id": "normalize_booleans",
            "description": f"Standardize yes/no and Y/N values to true/false ({names}).",
            "affected": sum(item["converted_cells"] for item in boolean_columns),
        })
    if duplicate_rows:
        actions.append({
            "id": "remove_duplicates",
            "description": "Remove exact duplicate rows after normalization.",
            "affected": duplicate_rows,
        })
    if empty_rows:
        actions.append({
            "id": "remove_empty_rows",
            "description": "Remove fully empty rows.",
            "affected": empty_rows,
        })
    if malformed_rows:
        actions.append({
            "id": "repair_row_width",
            "description": "Pad missing cells and discard extra trailing cells.",
            "affected": malformed_rows,
        })
    if delimiter != ",":
        actions.append({
            "id": "standardize_delimiter",
            "description": (
                f"Rewrite the file with comma separators (it currently uses "
                f"{'tabs' if delimiter == chr(9) else repr(delimiter)})."
            ),
            "affected": total_rows,
        })

    return {
        "total_rows": total_rows,
        "estimated_output_rows": total_rows - duplicate_rows - empty_rows,
        "duplicate_rows": duplicate_rows,
        "empty_rows": empty_rows,
        "malformed_rows": malformed_rows,
        "trimmed_cells": trimmed_cells,
        "standardized_nulls": standardized_nulls,
        "header_changes": header_changes,
        "missing_by_column": missing_by_column,
        "numeric_columns": numeric_columns,
        "boolean_columns": boolean_columns,
        "delimiter": delimiter,
        "actions": actions,
        "fingerprint": file_fingerprint(filepath),
    }


def _conversion_targets(source_path, max_rows):
    """Columns to convert, decided from the whole file so apply matches preview."""
    report = build_cleaning_preview(source_path, max_rows=max_rows)
    numeric = {item["column"] for item in report["numeric_columns"]}
    boolean = {item["column"] for item in report["boolean_columns"]}
    return numeric, boolean


def write_cleaned_copy(source_path, destination_path, action_ids, max_rows=250_000):
    actions = set(action_ids)
    numeric_targets, boolean_targets = set(), set()
    if actions & {"parse_numbers", "normalize_booleans"}:
        numeric_targets, boolean_targets = _conversion_targets(source_path, max_rows)
        if "parse_numbers" not in actions:
            numeric_targets = set()
        if "normalize_booleans" not in actions:
            boolean_targets = set()

    handle, reader, _delimiter = _open_reader(source_path)
    with handle, open(destination_path, "w", encoding="utf-8", newline="") as destination:
        try:
            original_headers = next(reader)
        except StopIteration:
            raise ValueError("The CSV file is empty.")
        clean_headers, _ = normalize_headers(original_headers)
        output_headers = (
            clean_headers if "normalize_headers" in actions else original_headers
        )
        # Conversion targets are keyed by normalized header names.
        numeric_index = {i for i, name in enumerate(clean_headers) if name in numeric_targets}
        boolean_index = {i for i, name in enumerate(clean_headers) if name in boolean_targets}
        writer = csv.writer(destination)
        writer.writerow(output_headers)

        seen = set()
        input_rows = 0
        output_rows = 0
        converted_numbers = 0
        blanked_unparsed = 0
        converted_booleans = 0
        for raw_row in reader:
            input_rows += 1
            if input_rows > max_rows:
                raise CleaningLimitExceeded(
                    f"Cleaning exceeded the {max_rows:,}-row safety limit."
                )
            adjusted = (raw_row + [""] * len(original_headers))[:len(original_headers)]
            normalized = []
            for index, value in enumerate(adjusted):
                clean_value = str(value)
                if "trim_whitespace" in actions:
                    clean_value = clean_value.strip()
                if (
                    "standardize_nulls" in actions
                    and clean_value.strip().casefold() in NULL_TOKENS
                ):
                    clean_value = ""
                if index in numeric_index and clean_value.strip() != "":
                    parsed = parse_formatted_number(clean_value)
                    if parsed is None:
                        # Below-threshold junk in a numeric column: blank it so
                        # the copy types as a number. Reported to the caller.
                        blanked_unparsed += 1
                        clean_value = ""
                    else:
                        if parsed != clean_value.strip():
                            converted_numbers += 1
                        clean_value = parsed
                if index in boolean_index and clean_value.strip() != "":
                    normalized_bool = normalize_boolean(clean_value)
                    if normalized_bool is not None:
                        if normalized_bool != clean_value.strip():
                            converted_booleans += 1
                        clean_value = normalized_bool
                normalized.append(clean_value)

            if "remove_empty_rows" in actions and all(
                value.strip() == "" for value in normalized
            ):
                continue
            if "remove_duplicates" in actions:
                digest = _row_digest(normalized)
                if digest in seen:
                    continue
                seen.add(digest)
            writer.writerow(normalized)
            output_rows += 1

    return {
        "input_rows": input_rows,
        "output_rows": output_rows,
        "converted_numbers": converted_numbers,
        "blanked_unparsed": blanked_unparsed,
        "converted_booleans": converted_booleans,
    }
