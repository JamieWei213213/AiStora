import csv
import hashlib
import os
import re


NULL_TOKENS = {"", "na", "n/a", "null", "none"}


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


def _row_digest(values):
    encoded = "\x1f".join(values).encode("utf-8", errors="replace")
    return hashlib.blake2b(encoded, digest_size=16).digest()


def build_cleaning_preview(filepath, max_rows=250_000):
    with open(filepath, "r", encoding="utf-8-sig", newline="") as source:
        reader = csv.reader(source)
        try:
            headers = next(reader)
        except StopIteration:
            raise ValueError("The CSV file is empty.")

        cleaned_headers, header_changes = normalize_headers(headers)
        missing_by_column = {column: 0 for column in cleaned_headers}
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
                if cleaned == "":
                    missing_by_column[cleaned_headers[index]] += 1

            if all(value == "" for value in normalized):
                empty_rows += 1
                continue
            digest = _row_digest(normalized)
            if digest in seen:
                duplicate_rows += 1
            else:
                seen.add(digest)

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
        "actions": actions,
        "fingerprint": file_fingerprint(filepath),
    }


def write_cleaned_copy(source_path, destination_path, action_ids, max_rows=250_000):
    actions = set(action_ids)
    with (
        open(source_path, "r", encoding="utf-8-sig", newline="") as source,
        open(destination_path, "w", encoding="utf-8", newline="") as destination,
    ):
        reader = csv.reader(source)
        try:
            original_headers = next(reader)
        except StopIteration:
            raise ValueError("The CSV file is empty.")
        clean_headers, _ = normalize_headers(original_headers)
        output_headers = (
            clean_headers if "normalize_headers" in actions else original_headers
        )
        writer = csv.writer(destination)
        writer.writerow(output_headers)

        seen = set()
        input_rows = 0
        output_rows = 0
        for raw_row in reader:
            input_rows += 1
            if input_rows > max_rows:
                raise CleaningLimitExceeded(
                    f"Cleaning exceeded the {max_rows:,}-row safety limit."
                )
            adjusted = (raw_row + [""] * len(original_headers))[:len(original_headers)]
            normalized = []
            for value in adjusted:
                clean_value = str(value)
                if "trim_whitespace" in actions:
                    clean_value = clean_value.strip()
                if (
                    "standardize_nulls" in actions
                    and clean_value.strip().casefold() in NULL_TOKENS
                ):
                    clean_value = ""
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

    return {"input_rows": input_rows, "output_rows": output_rows}
