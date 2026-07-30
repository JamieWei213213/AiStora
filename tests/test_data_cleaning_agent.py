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
    assert result == {"input_rows": 5, "output_rows": 3}
    assert rows[0] == ["Customer_Name", "Amount"]
    assert rows[1] == ["Alice", "10"]
    assert rows[2] == ["Bob", ""]
    assert rows[3] == ["Carol", "20"]
    assert source.exists()
