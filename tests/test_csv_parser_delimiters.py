"""Delimiter detection and malformed-row reporting in the CSV parser."""
from engine.parser import CsvParser, detect_delimiter


def test_semicolon_tab_and_pipe_files_parse_into_columns(tmp_path):
    cases = {
        "semi.csv": ("a;b;c\n1;2;3\n4;5;6\n", ";"),
        "tab.csv": ("a\tb\tc\n1\t2\t3\n", "\t"),
        "pipe.csv": ("a|b|c\n1|2|3\n", "|"),
        "comma.csv": ('a,b,c\n1,"x;y",3\n', ","),
    }
    for name, (content, expected) in cases.items():
        path = tmp_path / name
        path.write_text(content, encoding="utf-8")
        assert detect_delimiter(str(path)) == expected, name
        parser = CsvParser(str(path))
        assert parser.header == ["a", "b", "c"], name
        assert len(list(parser.parse())) == content.count("\n") - 1, name


def test_quoted_commas_do_not_confuse_detection(tmp_path):
    path = tmp_path / "quoted.csv"
    path.write_text('name,note\n"Smith, Jr.","a, b, c"\n"Lee","x"\n', encoding="utf-8")
    parser = CsvParser(str(path))
    rows = list(parser.parse())
    assert parser.separator == ","
    assert rows[0]["name"] == "Smith, Jr."


def test_single_column_file_defaults_to_comma(tmp_path):
    path = tmp_path / "one.csv"
    path.write_text("value\n1\n2\n", encoding="utf-8")
    parser = CsvParser(str(path))
    assert parser.separator == ","
    assert parser.header == ["value"]


def test_skipped_rows_are_counted_on_the_parser(tmp_path):
    path = tmp_path / "ragged.csv"
    path.write_text("id,name,score\n1,Alice,90\n2,Bob,85,extra\n3,Carol\n", encoding="utf-8")
    parser = CsvParser(str(path))
    rows = list(parser.parse())
    assert len(rows) == 1
    assert parser.skipped_rows == 2
