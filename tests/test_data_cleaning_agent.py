import csv

from services.data_cleaning_agent import build_cleaning_preview, write_cleaned_copy


def make_dirty_csv(path):
    with path.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow([" Customer Name ", "Amount"])
        writer.writerow([" Alice ", "10"])
        writer.writerow([" Alice ", "10"])
        writer.writerow(["Bob", "N/A"])
        writer.writerow([" ", " "])
        writer.writerow(["Carol", "20", "extra"])


def test_cleaning_preview_finds_safe_automatic_fixes(tmp_path):
    source = tmp_path / "dirty.csv"
    make_dirty_csv(source)

    preview = build_cleaning_preview(source)
    action_ids = {action["id"] for action in preview["actions"]}

    assert preview["total_rows"] == 5
    assert preview["estimated_output_rows"] == 3
    assert preview["duplicate_rows"] == 1
    assert preview["empty_rows"] == 1
    assert preview["malformed_rows"] == 1
    assert preview["standardized_nulls"] == 1
    assert preview["header_changes"][0]["to"] == "Customer_Name"
    assert {
        "normalize_headers",
        "trim_whitespace",
        "standardize_nulls",
        "remove_duplicates",
        "remove_empty_rows",
        "repair_row_width",
    }.issubset(action_ids)


def test_cleaning_writes_a_new_normalized_copy(tmp_path):
    source = tmp_path / "dirty.csv"
    destination = tmp_path / "cleaned.csv"
    make_dirty_csv(source)
    preview = build_cleaning_preview(source)

    result = write_cleaned_copy(
        source,
        destination,
        [action["id"] for action in preview["actions"]],
    )

    with destination.open("r", encoding="utf-8", newline="") as cleaned:
        rows = list(csv.reader(cleaned))
    assert result["input_rows"] == 5
    assert result["output_rows"] == 3
    assert rows[0] == ["Customer_Name", "Amount"]
    assert rows[1] == ["Alice", "10"]
    assert rows[2] == ["Bob", ""]
    assert rows[3] == ["Carol", "20"]
    assert source.exists()


def test_delimiter_is_detected_and_rewritten_as_comma(tmp_path):
    source = tmp_path / "eu.csv"
    destination = tmp_path / "eu_clean.csv"
    source.write_text("Datum;Kunde;Betrag\n2025-01-03;Kunde 1;12,50\n2025-01-04;Kunde 2;1.234,00\n", encoding="utf-8")

    preview = build_cleaning_preview(source)
    action_ids = [action["id"] for action in preview["actions"]]

    assert preview["delimiter"] == ";"
    assert preview["total_rows"] == 2
    assert "standardize_delimiter" in action_ids
    write_cleaned_copy(source, destination, action_ids)
    with destination.open("r", encoding="utf-8", newline="") as cleaned:
        rows = list(csv.reader(cleaned))
    assert rows[0] == ["Datum", "Kunde", "Betrag"]
    assert rows[1][:2] == ["2025-01-03", "Kunde 1"]


def test_currency_percent_and_yes_no_columns_are_converted(tmp_path):
    source = tmp_path / "export.csv"
    destination = tmp_path / "export_clean.csv"
    with source.open("w", encoding="utf-8", newline="") as output:
        writer = csv.writer(output)
        writer.writerow(["Amount Paid", "Churn Risk", "Renewed", "Notes"])
        writer.writerow(["$1,234.50", "12%", "Yes", "$5 discount"])
        writer.writerow(["USD 954", "7.5 %", "no", "call back"])
        writer.writerow(["(20.00)", "0.12", "Y", "n/a"])
        writer.writerow(["12.00", "", "TRUE", "paid"])
        writer.writerow(["pending", "3%", "N", "x"])
        for value in ("$1", "$2", "$3", "$4", "$5"):
            writer.writerow([value, "1%", "yes", "more"])

    preview = build_cleaning_preview(source)
    numeric = {item["column"]: item for item in preview["numeric_columns"]}
    boolean = {item["column"] for item in preview["boolean_columns"]}
    action_ids = {action["id"] for action in preview["actions"]}

    assert set(numeric) == {"Amount_Paid", "Churn_Risk"}
    assert numeric["Amount_Paid"]["unparsed_cells"] == 1  # "pending"
    assert boolean == {"Renewed"}
    assert "Notes" not in numeric  # mixed free text stays text
    assert {"parse_numbers", "normalize_booleans"}.issubset(action_ids)

    result = write_cleaned_copy(source, destination, sorted(action_ids))
    with destination.open("r", encoding="utf-8", newline="") as cleaned:
        rows = list(csv.reader(cleaned))
    assert rows[1] == ["1234.50", "12", "true", "$5 discount"]
    assert rows[2] == ["954", "7.5", "false", "call back"]
    assert rows[3] == ["-20.00", "0.12", "true", ""]
    assert rows[4] == ["12.00", "", "true", "paid"]
    assert rows[5] == ["", "3", "false", "x"]  # "pending" blanked, reported
    assert rows[6] == ["1", "1", "true", "more"]
    assert result["blanked_unparsed"] == 1
    assert result["converted_numbers"] == 16
