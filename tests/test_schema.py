"""Tests for src/schema.py (Part 5): valid records pass, every broken field fails."""
import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.schema import Record, record_keys, write_jsonl  # noqa: E402

GOOD = {
    "schema": "lantern/1.0",
    "doc_id": "0000320193-25-000079",
    "company": "Apple Inc.",
    "cik": "0000320193",
    "ticker": "AAPL",
    "form": "10-K",
    "fiscal_year": 2025,
    "fiscal_period": "FY",
    "page": 32,
    "section": "Item 8",
    "block_id": "p0032_b004",
    "block_type": "Table",
    "bbox": [8.0, 102.8, 612.0, 527.2],
    "units": "pt",
    "origin": "top-left",
    "text": None,
    "table": {
        "columns": ["", "2025", "2024", "2023"],
        "rows": [["Net income", 112010000000.0, 93736000000.0, 96995000000.0]],
        "raw_cells": [["Net income", "$ 112,010", "$ 93,736", "$ 96,995"]],
        "scale": {"money": 1000000.0, "shares": 1000.0, "per_share": 1.0},
        "row_kinds": ["money"],
        "method": "camelot-stream",
        "source_csv": "data/tables/AAPL_10K_20250927_p0032_income.norm.csv",
    },
    "extractor": "camelot-stream",
    "extractor_version": "camelot-py 2.0.0",
    "ocr": False,
    "ocr_conf": None,
    "source_path": "data/rendered/AAPL_10K_20250927.pdf",
    "sha256": "997ae38cf81f2152b77dce5727177f96bb7f65b84b9dff6a39e8ab5869b9ef47",
}


def changed(**kw):
    r = json.loads(json.dumps(GOOD))
    r.update(kw)
    return r


def test_good_record_validates():
    rec = Record.model_validate(GOOD)
    assert rec.schema_ == "lantern/1.0"


def test_json_keys_are_fixed_and_complete():
    out = json.loads(Record.model_validate(GOOD).model_dump_json(by_alias=True))
    assert list(out) == record_keys()
    assert "schema" in out and "schema_" not in out


def test_text_record_without_table_validates():
    Record.model_validate(changed(block_type="Text", text="Net sales increased.", table=None,
                                  extractor="pdfplumber", extractor_version="pdfplumber 0.11.10"))


@pytest.mark.parametrize("field, value", [
    ("doc_id", "AAPL_10K_20250927"),        # a stem, not an accession number
    ("cik", "320193"),                      # not zero-padded
    ("fiscal_year", "twenty"),
    ("fiscal_period", "annual"),
    ("page", 0),
    ("block_id", "b3"),
    ("block_type", "Paragraph"),
    ("bbox", [100, 50, 90, 60]),           # x0 > x1
    ("bbox", [10, 60, 90, 50]),            # top > bottom
    ("bbox", [10, 20, 30]),                # 3 numbers
    ("units", "px"),
    ("origin", "bottom-left"),
    ("source_path", "C:\\Users\\x\\file.pdf"),
    ("source_path", "/Users/x/file.pdf"),
    ("sha256", "abc"),
])
def test_bad_field_is_rejected(field, value):
    with pytest.raises(ValidationError):
        Record.model_validate(changed(**{field: value}))


def test_unknown_field_is_rejected():
    with pytest.raises(ValidationError):
        Record.model_validate(changed(colour="red"))


def test_ocr_conf_needs_ocr():
    with pytest.raises(ValidationError):
        Record.model_validate(changed(ocr=False, ocr_conf=95.0))


def test_text_block_needs_text():
    with pytest.raises(ValidationError):
        Record.model_validate(changed(block_type="Text", text=None, table=None))


def test_only_tables_carry_table_objects():
    with pytest.raises(ValidationError):
        Record.model_validate(changed(block_type="Text", text="x"))


def test_table_rows_must_match_columns():
    t = dict(GOOD["table"], rows=[["Net income", 1.0, 2.0]])     # 3 cells, 4 columns
    with pytest.raises(ValidationError):
        Record.model_validate(changed(table=t))


def test_write_jsonl_validates_and_writes_lf(tmp_path):
    out = tmp_path / "x.jsonl"
    assert write_jsonl([GOOD, changed(block_id="p0032_b005")], out) == 2
    data = out.read_bytes()
    assert b"\r\n" not in data and data.count(b"\n") == 2
    with pytest.raises(ValidationError):
        write_jsonl([GOOD, changed(cik="1")], tmp_path / "bad.jsonl")
    assert not (tmp_path / "bad.jsonl").exists()     # nothing written when a record is bad
