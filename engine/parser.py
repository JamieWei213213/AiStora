import csv
import os


class CsvParser:
    """Streaming CSV parser with type inference and quoted-field support."""

    def __init__(self, filepath, separator=",", infer_types=True, sample_size=50):
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"File not found: {filepath}")
        self.filepath = filepath
        self.separator = separator
        self.header = self._get_header()
        self.column_types = (
            self._infer_types(sample_size)
            if infer_types
            else {column: "str" for column in self.header}
        )

    def _reader(self, handle):
        return csv.reader(handle, delimiter=self.separator)

    def _get_header(self):
        try:
            with open(self.filepath, "r", encoding="utf-8-sig", newline="") as source:
                return [value.strip() for value in next(self._reader(source), [])]
        except (OSError, csv.Error) as exc:
            print(f"Error reading header: {exc}")
            return []

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
        if value == "":
            return None
        column_type = self.column_types.get(column, "str")
        if column_type == "int":
            try:
                return int(value)
            except (ValueError, TypeError):
                return value
        if column_type == "float":
            try:
                return float(value)
            except (ValueError, TypeError):
                return value
        return value

    def _infer_types(self, sample_size=50):
        types = {column: "int" for column in self.header}
        try:
            with open(self.filepath, "r", encoding="utf-8-sig", newline="") as source:
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
                        if value == "" or types[column] == "str":
                            continue
                        if types[column] == "int" and not self._is_int(value):
                            types[column] = "float"
                        if types[column] == "float" and not self._is_float(value):
                            types[column] = "str"
                    sampled += 1
        except (OSError, csv.Error) as exc:
            print(f"Error during type inference: {exc}")
            return {column: "str" for column in self.header}
        return types

    def parse(self, cast=True):
        try:
            with open(self.filepath, "r", encoding="utf-8-sig", newline="") as source:
                reader = self._reader(source)
                next(reader, None)
                for line_number, values in enumerate(reader, start=2):
                    if not values or all(value.strip() == "" for value in values):
                        continue
                    if len(values) != len(self.header):
                        print(
                            f"Warning: Skipping malformed row {line_number}. "
                            f"Expected {len(self.header)} columns, got {len(values)}."
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
        except (OSError, csv.Error) as exc:
            print(f"Error during parsing: {exc}")

    def parse_chunks(self, chunk_size=1000, cast=True):
        batch = []
        for row in self.parse(cast=cast):
            batch.append(row)
            if len(batch) >= chunk_size:
                yield batch
                batch = []
        if batch:
            yield batch
