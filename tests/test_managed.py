"""
Part 7 tests: managed-service cache and fallback. No AWS access and no data/managed needed:
each test builds a tiny cache in a temporary folder from the committed fixtures.
"""
import sys
import types
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.managed import cache, textract  # noqa: E402
from src.managed.blocks import page_size_of  # noqa: E402
from src.managed.fallback import Fallback  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures"
STATEMENT, SCANNED = FIX / "statement.pdf", FIX / "scanned.pdf"
MP = {"enabled": False, "provider": "aws-textract", "region": "us-east-1", "api": "AnalyzeDocument",
      "features": ["TABLES", "LAYOUT"], "ocr_conf_threshold": 70}


def bb(l, t, w, h):
    return {"BoundingBox": {"Left": l, "Top": t, "Width": w, "Height": h}}


def table_response():
    """A Textract-like response with one 2x2 TABLE in the top half of the page."""
    blocks, cells = [], []
    for r, row in enumerate([["Net income", "112,010"], ["Other income/(expense), net", "(565)"]], 1):
        for c, txt in enumerate(row, 1):
            wid = f"W{r}{c}"
            blocks.append({"Id": wid, "BlockType": "WORD", "Text": txt, "Confidence": 99.0,
                           "Geometry": bb(0.1, 0.1, 0.1, 0.02)})
            blocks.append({"Id": f"C{r}{c}", "BlockType": "CELL", "RowIndex": r, "ColumnIndex": c, "Confidence": 97.0,
                           "Geometry": bb(0.1 + 0.4 * (c - 1), 0.1 + 0.1 * r, 0.4, 0.1),
                           "Relationships": [{"Type": "CHILD", "Ids": [wid]}]})
            cells.append(f"C{r}{c}")
    blocks.append({"Id": "T1", "BlockType": "TABLE", "Confidence": 99.0, "Geometry": bb(0.1, 0.1, 0.8, 0.3),
                   "Relationships": [{"Type": "CHILD", "Ids": cells}]})
    return {"Blocks": blocks, "DocumentMetadata": {"Pages": 1}, "AnalyzeDocumentModelVersion": "1.0"}


def text_response():
    return {"Blocks": [
        {"Id": "L1", "BlockType": "LINE", "Text": "Apple designs smartphones.", "Confidence": 99.8,
         "Geometry": bb(0.1, 0.1, 0.5, 0.02)},
        {"Id": "W1", "BlockType": "WORD", "Text": "Apple", "Confidence": 99.9, "Geometry": bb(0.1, 0.1, 0.1, 0.02)}],
        "DocumentMetadata": {"Pages": 1}, "AnalyzeDocumentModelVersion": "1.0"}


def put(cache_dir, pdf, page, response):
    sha = cache.sha256_file(pdf)
    cache.write(cache_dir, cache.cache_key(sha, page, MP["provider"], MP["api"], MP["features"]),
                {"source": pdf.as_posix(), "source_sha256": sha, "page": page}, response)


class NoBoto3(types.ModuleType):
    """Stands in for boto3: any use fails the test."""
    def __getattr__(self, name):
        raise AssertionError("boto3 was used although the API is disabled")


# ---- cache key ----------------------------------------------------------------------------

def test_cache_key_is_stable_and_specific():
    k = cache.cache_key("a" * 64, 32, "aws-textract", "AnalyzeDocument", ["TABLES", "LAYOUT"])
    assert k == cache.cache_key("a" * 64, 32, "aws-textract", "AnalyzeDocument", "LAYOUT+TABLES")
    assert k != cache.cache_key("a" * 64, 33, "aws-textract", "AnalyzeDocument", ["TABLES", "LAYOUT"])
    assert k != cache.cache_key("a" * 64, 32, "aws-textract", "AnalyzeDocument", ["TABLES"])
    assert k != cache.cache_key("b" * 64, 32, "aws-textract", "AnalyzeDocument", ["TABLES", "LAYOUT"])


# ---- API disabled / enabled ---------------------------------------------------------------

def test_disabled_miss_makes_no_call(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", NoBoto3("boto3"))
    resp, status = textract.fetch(STATEMENT, 1, MP, tmp_path)
    assert resp is None and status == "miss, API disabled"
    assert list(tmp_path.iterdir()) == []                      # nothing written


def test_enabled_miss_calls_once_then_hits(tmp_path, monkeypatch):
    calls = []

    class Client:
        def analyze_document(self, Document, FeatureTypes):
            calls.append(FeatureTypes)
            return text_response()

    fake = types.ModuleType("boto3")
    fake.client = lambda *a, **k: Client()
    monkeypatch.setitem(sys.modules, "boto3", fake)
    on = dict(MP, enabled=True)
    assert textract.fetch(STATEMENT, 1, on, tmp_path)[1] == "called"
    assert textract.fetch(STATEMENT, 1, on, tmp_path)[1] == "hit"
    assert calls == [["TABLES", "LAYOUT"]]                     # exactly one call


# ---- table fallback -----------------------------------------------------------------------

def test_table_trigger_follows_the_part2_rule():
    fb = Fallback(MP, "unused", accept_score=80)
    assert fb.table_trigger({"status": "best_below_threshold", "score": 72.69})
    assert fb.table_trigger({"status": "no_valid_table"})
    assert fb.table_trigger({"status": "accepted", "score": 75})          # below accept_score
    assert fb.table_trigger({"status": "accepted", "score": 89.8}) is None


def test_table_fallback_uses_the_overlapping_cached_table(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", NoBoto3("boto3"))
    put(tmp_path, STATEMENT, 1, table_response())
    w, h = page_size_of(STATEMENT, 1)
    fb = Fallback(MP, tmp_path, accept_score=80)
    hit = fb.table(STATEMENT, 1, [0.1 * w, 0.1 * h, 0.9 * w, 0.4 * h], "best_below_threshold", "s", "p0001_b001")
    assert hit["grid"] == [["Net income", "112,010"], ["Other income/(expense), net", "(565)"]]
    assert hit["ocr_conf"] == 97.0 and "LAYOUT+TABLES" in hit["extractor_version"]
    miss = fb.table(STATEMENT, 1, [0.1 * w, 0.8 * h, 0.9 * w, 0.9 * h], "no_valid_table", "s", "p0001_b002")
    assert miss is None                                        # no Textract table there
    assert [(r["cache"], r["used"]) for r in fb.rows] == [("hit", True), ("hit", False)]


def test_table_fallback_disabled_miss_changes_nothing(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", NoBoto3("boto3"))
    fb = Fallback(MP, tmp_path, accept_score=80)
    assert fb.table(STATEMENT, 1, [10, 10, 500, 300], "best_below_threshold", "s", "p0001_b001") is None
    assert fb.rows[0]["cache"] == "miss, API disabled" and fb.rows[0]["used"] is False


# ---- text (OCR) fallback: forced threshold ------------------------------------------------

def test_ocr_fallback_does_not_fire_at_the_configured_threshold():
    fb = Fallback(MP, "unused")                                # threshold 70 from params
    assert fb.text_trigger(95.4, "measured Tesseract confidence on our scanned fixture") is None
    assert fb.text_trigger(None, "") == "ocr_empty"


def test_ocr_fallback_forced_threshold_uses_cached_text(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "boto3", NoBoto3("boto3"))
    put(tmp_path, SCANNED, 1, text_response())
    fb = Fallback(MP, tmp_path, ocr_threshold=99.9)            # forced above every real score
    trigger = fb.text_trigger(95.4, "tesseract text")
    assert trigger and "95.4" in trigger
    hit = fb.text(SCANNED, 1, (612.0, 792.0), trigger, "scanned")
    assert hit["text"] == "Apple designs smartphones."
    assert hit["words"][0]["text"] == "Apple" and hit["words"][0]["bbox"][0] == pytest.approx(61.2)
    assert hit["mean_conf"] == 99.8
    assert fb.rows[-1]["cache"] == "hit" and fb.rows[-1]["used"] is True
