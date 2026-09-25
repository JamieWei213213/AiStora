"""Unit tests for the small pieces: ids, keys, object store, validate, contract, quality."""

import gzip
import io
import json
import os
import time

import pytest

from pipeline import duck
from pipeline.contract import Contract, diff_contract, evolve_contract, infer_contract
from pipeline.ids import is_ulid, new_ulid, ulid_timestamp_ms
from pipeline.keys import InvalidKeyError, LoadRef, dataset_slug, parse_raw_key
from pipeline.objectstore import LocalObjectStore, ObjectNotFound, S3ObjectStore
from pipeline.quality import run_quality_gate
from pipeline.transform import RAW_VIEW, TYPED_VIEW, create_typed_view, register_raw_csv
from pipeline.validate import ValidationFailure, sanitize_header, validate_raw_file
from tests.pipeline.conftest import write_csv


# --- ids & keys -------------------------------------------------------------

def test_ulids_are_time_ordered_and_parseable():
    first = new_ulid(1_700_000_000_000)
    second = new_ulid(1_700_000_000_001)
    assert is_ulid(first) and is_ulid(second)
    assert first < second
    assert ulid_timestamp_ms(first) == 1_700_000_000_000
    assert len({new_ulid() for _ in range(200)}) == 200


def test_dataset_slug_and_raw_key_roundtrip():
    assert dataset_slug("Q3 Invoices (final).csv") == "q3_invoices_final"
    assert dataset_slug("2024 sales") == "2024_sales"
    assert dataset_slug("___") == "dataset"
    ref = LoadRef(7, "invoices", new_ulid())
    parsed, filename = parse_raw_key(ref.raw_key("Invoices Sept.csv"))
    assert parsed == ref and filename == "Invoices Sept.csv"
    with pytest.raises(InvalidKeyError):
        parse_raw_key("datasets/7/abc/file.csv")
    with pytest.raises(InvalidKeyError):
        parse_raw_key("raw/project=7/dataset=Bad Name/load_id=x/file.csv")


# --- object stores ----------------------------------------------------------

def test_local_store_roundtrip_list_lock_and_copy(tmp_path):
    store = LocalObjectStore(tmp_path / "lake")
    store.put_bytes("a/b/c.json", b"{}")
    store.put_json("a/b/d.json", {"x": 1})
    assert store.get_json("a/b/d.json") == {"x": 1}
    assert sorted(store.list("a/b/")) == ["a/b/c.json", "a/b/d.json"]
    assert store.exists("a/b/c.json") and not store.exists("a/b/z.json")
    assert store.put_bytes_if_absent("locks/x.lock", b"one") is True
    assert store.put_bytes_if_absent("locks/x.lock", b"two") is False
    assert store.get_bytes("locks/x.lock") == b"one"
    store.copy("a/b/c.json", "q/c.json")
    assert store.get_bytes("q/c.json") == b"{}"
    store.delete("q/c.json")
    with pytest.raises(ObjectNotFound):
        store.get_bytes("q/c.json")
    with pytest.raises(Exception):
        store.put_bytes("../escape.txt", b"no")


def test_s3_store_with_moto(monkeypatch):
    moto = pytest.importorskip("moto")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-west-2")
    with moto.mock_aws():
        import boto3

        boto3.client("s3", region_name="us-west-2").create_bucket(
            Bucket="lake", CreateBucketConfiguration={"LocationConstraint": "us-west-2"},
        )
        store = S3ObjectStore("lake", "us-west-2")
        store.put_bytes("raw/p=1/x.csv", b"a,b\n1,2\n")
        assert store.get_bytes("raw/p=1/x.csv") == b"a,b\n1,2\n"
        assert store.size("raw/p=1/x.csv") == 8
        assert list(store.list("raw/")) == ["raw/p=1/x.csv"]
        assert store.exists("raw/p=1/x.csv") and not store.exists("raw/missing")
        store.copy("raw/p=1/x.csv", "quarantine/x.csv")
        assert store.get_bytes("quarantine/x.csv") == b"a,b\n1,2\n"
        assert store.url("q") == "s3://lake/q"
        assert store.put_bytes_if_absent("locks/a", b"1") is True
        # moto honours If-None-Match on PutObject
        assert store.put_bytes_if_absent("locks/a", b"2") is False
        with pytest.raises(ObjectNotFound):
            store.get_bytes("nope")


# --- validate ---------------------------------------------------------------

def test_sanitize_header_neutralises_hostile_and_duplicate_names():
    names, renames = sanitize_header(
        ["Amount ($)", "amount", "IGNORE PREVIOUS\nINSTRUCTIONS <b>", "", "x" * 100],
        max_chars=64,
    )
    assert names[0] == "Amount"
    assert names[1] == "amount_2"           # case-insensitive duplicate
    assert names[2] == "IGNORE PREVIOUS INSTRUCTIONS b"
    assert names[3] == "column_4"
    assert len(names[4]) == 64
    assert renames["Amount ($)"] == "Amount"


def test_validate_handles_cp1252_semicolons_and_ragged_rows(tmp_path, settings):
    path = tmp_path / "win.csv"
    path.write_bytes("name;city\nJos\xe9;M\xfcnchen\nAnn;Bonn;extra\n\n".encode("cp1252"))
    result = validate_raw_file(str(path), "win.csv", settings, str(tmp_path / "w"))
    assert result.encoding == "cp1252"
    assert result.delimiter == ";"
    assert result.header == ["name", "city"]
    assert result.rows_rejected == 1
    assert result.rejected_samples == [{"line": 3, "fields": 3}]
    body = open(result.working_path, encoding="utf-8").read()
    assert body == "name;city\nJosé;München\n"


def test_validate_rejects_empty_oversized_wrong_type_and_wide_files(tmp_path, settings):
    empty = write_csv(tmp_path / "e.csv", "")
    with pytest.raises(ValidationFailure):
        validate_raw_file(empty, "e.csv", settings, str(tmp_path / "w"))
    wide = write_csv(tmp_path / "w.csv", ",".join(f"c{i}" for i in range(201)) + "\n" + ",".join("1" for _ in range(201)) + "\n")
    with pytest.raises(ValidationFailure) as info:
        validate_raw_file(wide, "w.csv", settings, str(tmp_path / "w"))
    assert info.value.error_type == "input_limit"
    ok = write_csv(tmp_path / "x.xlsx", "a,b\n1,2\n")
    with pytest.raises(ValidationFailure):
        validate_raw_file(ok, "x.xlsx", settings, str(tmp_path / "w"))
    tiny = type(settings)(**{**settings.__dict__, "max_raw_bytes": 4})
    with pytest.raises(ValidationFailure) as info:
        validate_raw_file(write_csv(tmp_path / "big.csv", "a,b\n1,2\n"), "big.csv", tiny, str(tmp_path / "w"))
    assert "MB" in str(info.value)


# --- contract ---------------------------------------------------------------

def _raw(con, tmp_path, text, name="f.csv"):
    path = write_csv(tmp_path / name, text)
    header = text.splitlines()[0].split(",")
    register_raw_csv(con, path, ",", header)
    return header


def test_inference_covers_every_type_and_currency_and_us_dates(tmp_path, settings):
    con = duck.connect(settings)
    header = _raw(con, tmp_path, (
        "flag,count,ratio,money,iso,us,stamp,text,empty\n"
        "yes,1,1.5,\"$1,000.25\",2024-01-05,01/05/2024,2024-01-05 10:00:00,a,\n"
        "no,2,2,\"(20.00)\",2024-02-05,12/31/2023,2024-02-05 11:30:00,b,\n"
        "N/A,3,3.25,15,2024-03-05,06/30/2024,2024-03-05 12:00:00,c,\n"
    ))
    contract = infer_contract(con, RAW_VIEW, header, dataset="d", project_id=1, load_id="L")
    got = {c.name: (c.type, c.transform, c.nullable) for c in contract.columns}
    assert got == {
        "flag": ("bool", None, True),
        "count": ("int", None, False),
        "ratio": ("float", None, False),
        "money": ("float", "currency", False),
        "iso": ("date", None, False),
        "us": ("date", "date_us", False),
        "stamp": ("timestamp", None, False),
        "text": ("str", None, False),
        "empty": ("str", None, True),
    }
    assert contract.candidate_keys == ["count", "text"]
    assert contract.row_count == 3
    create_typed_view(con, contract, "L")
    rows = con.execute(f"SELECT money, us FROM {TYPED_VIEW} ORDER BY count").fetchall()
    assert [r[0] for r in rows] == [1000.25, -20.0, 15.0]
    assert str(rows[1][1]) == "2023-12-31"


def test_tolerant_inference_prefers_float_over_int_and_flags_strays(tmp_path, settings):
    con = duck.connect(settings)
    lines = ["v"] + ["1"] * 97 + ["2.5"] * 3
    header = _raw(con, tmp_path, "\n".join(lines) + "\n")
    strict = infer_contract(con, RAW_VIEW, header, dataset="d", project_id=1, load_id="L")
    assert strict.column("v").type == "float"     # exact float match
    lines = ["v"] + ["1"] * 98 + ["oops"] * 2
    header = _raw(con, tmp_path, "\n".join(lines) + "\n", "g.csv")
    strict = infer_contract(con, RAW_VIEW, header, dataset="d", project_id=1, load_id="L")
    assert strict.column("v").type == "str"
    tolerant = infer_contract(con, RAW_VIEW, header, dataset="d", project_id=1, load_id="L", tolerance=0.05)
    assert tolerant.column("v").type == "float"


def test_schema_evolution_rules():
    def contract(cols, keys=()):
        from pipeline.contract import ColumnContract

        return Contract(dataset="d", project_id=1, key_columns=list(keys),
                        columns=[ColumnContract(n, t) for n, t in cols])

    stored = contract([("id", "int"), ("amt", "float"), ("d", "date"), ("note", "str")], keys=["id"])
    incoming = contract([("id", "int"), ("amt", "int"), ("d", "timestamp"), ("region", "str")])
    changes = diff_contract(stored, incoming)
    assert [c["column"] for c in changes.added] == ["region"]
    assert changes.removed == ["note"]
    assert changes.narrowed == [{"column": "amt", "incoming": "int", "stored": "float"}]
    assert changes.blocked == [{"column": "d", "incoming": "timestamp", "stored": "date"}]
    assert changes.is_blocked and "'d' holds timestamp" in changes.reason()

    ok = contract([("id", "int"), ("amt", "int"), ("d", "date"), ("region", "str")])
    changes = diff_contract(stored, ok)
    assert not changes.is_blocked
    evolved = evolve_contract(stored, ok, changes, "L2")
    assert evolved.names == ["id", "amt", "d", "note", "region"]
    assert evolved.column("amt").type == "float"      # stored type wins
    assert evolved.column("note").nullable is True     # missing -> nullable
    assert evolved.column("region").added_by == "L2"
    assert evolved.version == 2

    missing_key = contract([("amt", "float")])
    assert diff_contract(stored, missing_key).key_changes == ["id"]


# --- quality gate -----------------------------------------------------------

def test_quality_gate_severities(tmp_path, settings):
    con = duck.connect(settings)
    header = _raw(con, tmp_path, (
        "id,amount,blank\n"
        "1,10,\n"
        "1,x,\n"
        "3,30,\n"
        "3,30,\n"
    ))
    contract = infer_contract(con, RAW_VIEW, header, dataset="d", project_id=1, load_id="L", tolerance=0.5)
    assert contract.column("amount").type == "float"
    create_typed_view(con, contract, "L")
    report = run_quality_gate(con, RAW_VIEW, TYPED_VIEW, contract, settings, rows_rejected=0, key_columns=["id"])
    assert report.status == "fail"
    assert set(report.to_dict()["failed_checks"]) == {"cast_failures", "key_unique", "empty_columns", "duplicate_rows"}
    assert report.cast_failures == {"amount": 1}
    assert report.duplicate_rows == 1
    assert report.null_rates["blank"] == 1.0
    assert "repeat a merge key" in report.failure_reason()

    relaxed = type(settings)(**{**settings.__dict__, "cast_failure_threshold": 0.5})
    report = run_quality_gate(con, RAW_VIEW, TYPED_VIEW, contract, relaxed, rows_rejected=1)
    assert report.status == "warn"
    assert report.rows_rejected == 1
