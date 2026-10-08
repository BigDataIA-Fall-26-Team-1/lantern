"""
Part 9 - Regression tests on extraction quality

Runs Part 1 (text) and Part 2 (tables) on tests/fixtures into a temporary folder,
grades them against tests/fixtures/gt with src/evaluate.py, and fails if quality drops
below the thresholds in params.yaml (eval.thresholds). The thresholds are the measured
baseline plus a small margin.

Runs in CI: needs no network, no DVC data and no credentials.

To prove the tests can fail, run them with a deliberately broken parameter file:
    LANTERN_PARAMS=/tmp/params_broken.yaml pytest -q tests/test_quality.py
(thresholds are always read from the real params.yaml).
"""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures"
GT = FIXTURES / "gt"
RUN_PARAMS = Path(os.environ.get("LANTERN_PARAMS", ROOT / "params.yaml"))


def run(*args) -> None:
    """Run one pipeline script; fail the test with its error output if it breaks."""
    res = subprocess.run([sys.executable, *map(str, args)], cwd=ROOT,
                         capture_output=True, text=True)
    if res.returncode != 0:
        pytest.fail(f"{args[0]} failed:\n{res.stderr[-2000:]}")


@pytest.fixture(scope="module")
def thresholds() -> dict:
    with open(ROOT / "params.yaml", encoding="utf-8") as f:
        return yaml.safe_load(f)["eval"]["thresholds"]


@pytest.fixture(scope="module")
def metrics(tmp_path_factory) -> dict:
    if not (GT / "pages.csv").exists():
        pytest.skip("no fixture ground truth (tests/fixtures/gt/pages.csv)")
    tmp = tmp_path_factory.mktemp("quality")
    run("src/parse_text.py", "--input", FIXTURES, "--output", tmp / "parsed",
        "--params", RUN_PARAMS)
    run("src/tables.py", "--input", FIXTURES, "--output", tmp / "tables",
        "--params", RUN_PARAMS)
    run("src/evaluate.py", "--gt", GT, "--parsed", tmp / "parsed", "--tables", tmp / "tables",
        "--layout", "none", "--docling", "none", "--out", tmp / "eval")
    return json.loads((tmp / "eval" / "metrics.json").read_text(encoding="utf-8"))


def stratum(metrics: dict, name: str) -> dict:
    s = metrics.get("text_by_stratum", {}).get(name, {}).get("pdfplumber")
    if not s:
        pytest.skip(f"no '{name}' page in the fixture ground truth yet")
    return s


def test_statement_text_wer(metrics, thresholds):
    wer = stratum(metrics, "statement")["wer"]
    assert wer <= thresholds["statement_max_wer"], (
        f"statement text WER {wer:.4f} is above {thresholds['statement_max_wer']}")


def test_statement_numeric_recall(metrics, thresholds):
    rec = metrics["text"]["pdfplumber"]["numeric_recall"]
    assert rec >= thresholds["min_numeric_recall"], (
        f"numeric recall {rec:.4f} is below {thresholds['min_numeric_recall']}")


def test_table_cell_f1(metrics, thresholds):
    tables = metrics.get("tables", {}).get("traditional")
    if not tables:
        pytest.skip("no fixture table ground truth yet")
    assert tables["f1"] >= thresholds["table_min_f1"], (
        f"table cell F1 {tables['f1']:.4f} is below {thresholds['table_min_f1']}")


def test_scanned_ocr_wer(metrics, thresholds):
    wer = stratum(metrics, "scanned")["wer"]
    assert wer <= thresholds["scanned_max_wer"], (
        f"scanned (OCR) WER {wer:.4f} is above {thresholds['scanned_max_wer']}")