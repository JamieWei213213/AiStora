"""Stage 1: is this file something we are willing to load at all?

Everything here is cheap and runs before a single row is typed: size,
extension, encoding, delimiter, and the header. A file that fails goes to
quarantine with a reason a person can act on; a file that passes comes out
as a UTF-8 working copy with a sanitised header so every later stage can
assume clean input.
"""

from __future__ import annotations

import codecs
import csv
import io
import os
import re
import unicodedata
from dataclasses import dataclass, field

from pipeline.config import PipelineSettings

# Encodings tried in order; the same list as engine/parser.py so the app and
# the pipeline agree on what a "supported file" is.
ENCODING_CANDIDATES = ("utf-8-sig", "cp1252", "latin-1")
DETECTION_BYTES = 4 * 1024 * 1024
DELIMITER_CANDIDATES = ",;\t|"

csv.field_size_limit(1_000_000)

_CONTROL = re.compile(r"[\x00-\x1f\x7f]")
_NOT_ALLOWED = re.compile(r"[^A-Za-z0-9_ ]+")


class ValidationFailure(ValueError):
    """The file is rejected. Message is safe to show to the uploader."""

    def __init__(self, message: str, error_type: str = "validation"):
        super().__init__(message)
        self.error_type = error_type


@dataclass
class ValidationResult:
    encoding: str
    delimiter: str
    header: list[str]
    renames: dict = field(default_factory=dict)   # original -> sanitised, only where changed
    raw_bytes: int = 0
    working_path: str = ""                         # UTF-8 copy for DuckDB
    warnings: list[str] = field(default_factory=list)
    rows_rejected: int = 0
    rejected_samples: list[dict] = field(default_factory=list)


def detect_encoding(path: str) -> str:
    with open(path, "rb") as handle:
        sample = handle.read(DETECTION_BYTES)
    if sample.startswith(codecs.BOM_UTF16_LE) or sample.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    for encoding in ENCODING_CANDIDATES:
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "latin-1"


def sniff_delimiter(sample_text: str) -> str:
    first_line = sample_text.splitlines()[0] if sample_text else ""
    try:
        dialect = csv.Sniffer().sniff(sample_text[:64 * 1024], delimiters=DELIMITER_CANDIDATES)
        candidate = dialect.delimiter
    except csv.Error:
        candidate = None
    if candidate and candidate in DELIMITER_CANDIDATES and first_line.count(candidate) > 0:
        return candidate
    # Fall back to the most frequent candidate in the header line.
    best = max(DELIMITER_CANDIDATES, key=lambda char: first_line.count(char))
    return best if first_line.count(best) > 0 else ","


def sanitize_column_name(raw: str, max_chars: int) -> str:
    """Reduce a header cell to ``[A-Za-z0-9_ ]{1,max}``.

    Column names reach the model in every prompt and are rendered in the UI,
    so they are the one place where a CSV can carry an instruction or a
    script. Sanitising on the way in, and recording the rename in the
    manifest, means the original text is never lost but never executed.
    """
    text = unicodedata.normalize("NFKC", str(raw or ""))
    text = _CONTROL.sub(" ", text)
    text = _NOT_ALLOWED.sub(" ", text)
    text = re.sub(r"\s+", " ", text).strip(" _")
    text = re.sub(r"_+", "_", text)
    if max_chars:
        text = text[:max_chars].strip(" _")
    return text


def sanitize_header(raw_header: list[str], max_chars: int) -> tuple[list[str], dict]:
    names: list[str] = []
    renames: dict = {}
    taken: set[str] = set()
    for index, raw in enumerate(raw_header, start=1):
        clean = sanitize_column_name(raw, max_chars) or f"column_{index}"
        candidate, suffix = clean, 1
        while candidate.casefold() in taken:
            suffix += 1
            candidate = f"{clean}_{suffix}"
            if max_chars and len(candidate) > max_chars:
                candidate = f"{clean[: max_chars - len(str(suffix)) - 1]}_{suffix}"
        taken.add(candidate.casefold())
        names.append(candidate)
        if candidate != str(raw):
            renames[str(raw)] = candidate
    return names, renames


def validate_raw_file(
    path: str,
    original_filename: str,
    settings: PipelineSettings,
    working_dir: str,
) -> ValidationResult:
    if not os.path.isfile(path):
        raise ValidationFailure("The uploaded object could not be read.", "storage")
    size = os.path.getsize(path)
    if size == 0:
        raise ValidationFailure("The file is empty.")
    if settings.max_raw_bytes and size > settings.max_raw_bytes:
        limit_mb = settings.max_raw_bytes / (1024 * 1024)
        raise ValidationFailure(
            f"The file is {size / (1024 * 1024):.1f} MB; the pipeline accepts at most "
            f"{limit_mb:.0f} MB per file.",
            "input_limit",
        )
    extension = os.path.splitext(str(original_filename or ""))[1].lower()
    if extension not in {".csv", ".tsv", ".txt"}:
        raise ValidationFailure("Only CSV files (.csv, .tsv, .txt) are accepted.")

    encoding = detect_encoding(path)
    with open(path, "r", encoding=encoding, newline="") as handle:
        sample = handle.read(256 * 1024)
    if not sample.strip():
        raise ValidationFailure("The file contains no data.")
    delimiter = "\t" if extension == ".tsv" else sniff_delimiter(sample)

    reader = csv.reader(io.StringIO(sample), delimiter=delimiter)
    try:
        raw_header = next(reader)
    except (StopIteration, csv.Error) as exc:
        raise ValidationFailure(f"The header row could not be read: {exc}") from exc
    if not any(str(cell).strip() for cell in raw_header):
        raise ValidationFailure("The first row is empty; a header row is required.")
    if settings.max_columns and len(raw_header) > settings.max_columns:
        raise ValidationFailure(
            f"Files may have at most {settings.max_columns} columns "
            f"(this file has {len(raw_header)}).",
            "input_limit",
        )
    header, renames = sanitize_header(raw_header, settings.max_column_name_chars)

    warnings = []
    if renames:
        warnings.append(f"{len(renames)} column name(s) were normalised.")

    # DuckDB reads UTF-8 (and UTF-16/Latin-1 with flags); Windows-1252 is
    # the common case it does not, so a UTF-8 working copy is made once and
    # every later stage reads that. It costs one streaming pass.
    os.makedirs(working_dir, exist_ok=True)
    working_path = os.path.join(working_dir, "working.csv")
    rows_rejected = 0
    rejected_samples: list[dict] = []
    with open(path, "r", encoding=encoding, newline="") as source, open(
        working_path, "w", encoding="utf-8", newline=""
    ) as target:
        # Rewrite the header line with the sanitised names so DuckDB sees the
        # final column names directly.
        writer = csv.writer(target, delimiter=delimiter, lineterminator="\n")
        writer.writerow(header)
        source_reader = csv.reader(source, delimiter=delimiter)
        next(source_reader, None)
        width = len(header)
        for line_number, row in enumerate(source_reader, start=2):
            if not row or all(not str(cell).strip() for cell in row):
                continue
            if len(row) != width:
                # A ragged row is never padded or truncated silently: it is
                # counted, a sample is kept for the quarantine report, and the
                # gate decides whether the share is acceptable.
                rows_rejected += 1
                if len(rejected_samples) < 20:
                    rejected_samples.append({"line": line_number, "fields": len(row)})
                continue
            writer.writerow(row)

    return ValidationResult(
        encoding=encoding,
        delimiter=delimiter,
        header=header,
        renames=renames,
        raw_bytes=size,
        working_path=working_path,
        warnings=warnings,
        rows_rejected=rows_rejected,
        rejected_samples=rejected_samples,
    )
