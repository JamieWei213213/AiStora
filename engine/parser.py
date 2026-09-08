import codecs
import csv
import math
import os

from services.logger import get_logger


logger = get_logger(__name__)


# Values that mean "missing" in exports from accounting tools. Treating these
# as text used to demote an otherwise numeric column to str, because type
# inference saw a non-numeric token and gave up on the column.
NULL_MARKERS = {
    "", "-", "--", "n/a", "n.a.", "na", "nan", "null", "none", "nil",
    "#n/a", "#null!", "(blank)", "not available", "unknown",
}

DEFAULT_SAMPLE_SIZE = 1000

# Python's csv module refuses fields over 131,072 characters. That limit used
# to surface as a csv.Error that was printed and swallowed, so parsing stopped
# silently and the upload "succeeded" with a truncated row count. The limit is
# raised to a generous ceiling and anything past it is a real error.
MAX_FIELD_CHARS = 1_000_000
csv.field_size_limit(MAX_FIELD_CHARS)

# Encodings tried in order. utf-8-sig handles plain UTF-8 and Excel's BOM;
# cp1252 is what Windows Excel writes when you pick "CSV (Comma delimited)";
# latin-1 cannot fail to decode and is the last resort.
_ENCODING_CANDIDATES = ("utf-8-sig", "cp1252", "latin-1")
_DETECTION_BYTES = 4 * 1024 * 1024


class CsvParseError(ValueError):
    """The file could not be read as CSV. Safe to show to the uploader."""


def is_null_marker(value):
    return str(value).strip().casefold() in NULL_MARKERS


def detect_encoding(filepath):
    """Pick the first candidate encoding that decodes the file's first 4 MB.

    A UTF-16 byte-order mark is checked first because UTF-16 text decodes
    "successfully" as latin-1 into unusable NUL-laced garbage.
    """
    with open(filepath, "rb") as handle:
        sample = handle.read(_DETECTION_BYTES)
    if sample.startswith(codecs.BOM_UTF16_LE) or sample.startswith(codecs.BOM_UTF16_BE):
        return "utf-16"
    for encoding in _ENCODING_CANDIDATES:
        try:
            sample.decode(encoding)
            return encoding
        except UnicodeDecodeError:
            continue
    return "latin-1"


def dedupe_header(names):
    """Make header names unique and non-empty.

    Duplicate names used to collapse into one dict key (last value wins) with
    no warning, so a file with two ``amount`` columns silently lost one.
    """
    taken = set()
    result = []
    for index, raw in enumerate(names, start=1):
        name = str(raw).strip() or f"column_{index}"
        candidate, suffix = name, 1
        while candidate in taken:
            suffix += 1
            candidate = f"{name}_{suffix}"
        taken.add(candidate)
        result.append(candidate)
    return result


class CsvParser:
    """Streaming CSV parser with type inference and quoted-field support.

    Type inference reads up to ``sample_size`` rows (1000 by default rather
    than the original 50). A small sample is cheap but wrong often enough to
    matter: a column that is numeric for its first fifty rows and contains
    "N/A" at row five thousand would be typed ``int``, and every later value
    that failed to cast was silently returned as a string, leaving one column
    holding two different Python types.
    """

    def __init__(
        self,
        filepath,
        separator=",",
        infer_types=True,
        sample_size=DEFAULT_SAMPLE_SIZE,
        encoding=None,
    ):
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")
        self.filepath = filepath
        self.separator = separator
        self.encoding = encoding or detect_encoding(filepath)
        self.header = self._get_header()
        self.column_types = (
            self._infer_types(sample_size)
            if infer_types
            else {column: "str" for column in self.header}
        )

    def _open(self):
        return open(self.filepath, "r", encoding=self.encoding, newline="")

    def _reader(self, handle):
        return csv.reader(handle, delimiter=self.separator)

    def _get_header(self):
        try:
            with self._open() as source:
                raw = next(self._reader(source), [])
        except UnicodeDecodeError as exc:
            raise CsvParseError(
                "The file is not valid text in a supported encoding "
                "(UTF-8, Windows-1252, Latin-1 or UTF-16)."
            ) from exc
        except csv.Error as exc:
            raise CsvParseError(f"The CSV header could not be read: {exc}") from exc
        if not any(str(value).strip() for value in raw):
            raise CsvParseError("The CSV file has no header row.")
        return dedupe_header(raw)

    def get_header(self):
        return self.header

    def get_column_types(self):
        return self.column_types

    def _is_int(self, value):
        try:
            int(value)
            return True
        except (ValueError, TypeError):
            return False

    def _is_float(self, value):
        try:
            float(value)
            return True
        except (ValueError, TypeError):
            return False

    def _cast_value(self, column, value):
        """Cast to the column's inferred type.

        A value that cannot be cast becomes ``None`` rather than the raw
        string. Returning the string kept a "helpful" value in place but made
        the column heterogeneous, so downstream comparisons could compare an
        int against a str. ``None`` means "missing", which every consumer
        already handles. Non-finite floats (``inf``, ``nan``) are also
        missing: JSON has no representation for them and the browser rejects
        the whole response when one leaks through.
        """
        if value is None or is_null_marker(value):
            return None
        column_type = self.column_types.get(column, "str")
        if column_type == "int":
            try:
                return int(value)
            except (ValueError, TypeError):
                try:
                    number = float(value)
                except (ValueError, TypeError):
                    return None
                return int(number) if math.isfinite(number) else None
        if column_type == "float":
            try:
                number = float(value)
            except (ValueError, TypeError):
                return None
            return number if math.isfinite(number) else None
        return value

    def _infer_types(self, sample_size=DEFAULT_SAMPLE_SIZE):
        types = {column: "int" for column in self.header}
        seen = {column: False for column in self.header}
        try:
            with self._open() as source:
                reader = self._reader(source)
                next(reader, None)
                sampled = 0
                for values in reader:
                    if sampled >= sample_size:
                        break
                    if not values or len(values) != len(self.header):
                        continue
                    for column, raw_value in zip(self.header, values):
                        value = raw_value.strip()
                        # Missing markers say nothing about the column's type.
                        if is_null_marker(value) or types[column] == "str":
                            continue
                        seen[column] = True
                        if types[column] == "int" and not self._is_int(value):
                            types[column] = "float"
                        if types[column] == "float" and not self._is_float(value):
                            types[column] = "str"
                    sampled += 1
        except UnicodeDecodeError as exc:
            raise CsvParseError(
                "The file is not valid text in a supported encoding."
            ) from exc
        except csv.Error as exc:
            raise CsvParseError(f"The CSV file could not be parsed: {exc}") from exc
        # A column with no observed values is text, not an integer.
        for column in self.header:
            if not seen[column]:
                types[column] = "str"
        return types

    def parse(self, cast=True):
        skipped = 0
        try:
            with self._open() as source:
                reader = self._reader(source)
                next(reader, None)
                for line_number, values in enumerate(reader, start=2):
                    if not values or all(value.strip() == "" for value in values):
                        continue
                    if len(values) != len(self.header):
                        skipped += 1
                        if skipped <= 5:
                            logger.warning(
                                "Skipping malformed row %s in %s: expected %s columns, got %s",
                                line_number,
                                os.path.basename(self.filepath),
                                len(self.header),
                                len(values),
                            )
                        continue
                    row = {
                        column: value.strip()
                        for column, value in zip(self.header, values)
                    }
                    if cast:
                        row = {
                            column: self._cast_value(column, value)
                            for column, value in row.items()
                        }
                    yield row
        except UnicodeDecodeError as exc:
            raise CsvParseError(
                "The file is not valid text in a supported encoding."
            ) from exc
        except csv.Error as exc:
            # Raising here is the point: a swallowed error meant the upload
            # reported fewer rows than the file held and nobody was told.
            raise CsvParseError(f"The CSV file could not be parsed: {exc}") from exc
        if skipped:
            logger.warning(
                "Skipped %s malformed rows in %s", skipped, os.path.basename(self.filepath)
            )

    def parse_chunks(self, chunk_size=1000, cast=True):
        batch = []
        for row in self.parse(cast=cast):
            batch.append(row)
            if len(batch) >= chunk_size:
                yield batch
                batch = []
        if batch:
            yield batch
