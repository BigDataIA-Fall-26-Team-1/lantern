"""
Unit tests for number normalization in src/tables.py (Part 2).

Each case comes from the brief's list: parentheses as negatives, dashes,
currency symbols, footnote markers, plain numbers. Only the VALUE is checked
(to_number returns (value, unit)).
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.tables import to_number  # noqa: E402


def val(cell):
    return to_number(cell)[0]


@pytest.mark.parametrize("cell, expected", [
    ("", None),                 # empty cell
    ("$", None),                # currency symbol in its own cell
    ("Net sales", None),        # a label, not a number
    ("-", 0.0),                 # dash = nil (policy from the tutorial)
    ("\u2013", 0.0),            # en dash
    ("\u2014", 0.0),            # em dash
    ("7.49", 7.49),             # plain decimal (per-share)
    ("1,234", 1234.0),          # thousands separator
    ("$ 1,234", 1234.0),        # currency symbol
    ("$ 307,003", 307003.0),
    ("(321)", -321.0),          # parentheses = negative
    ("(1,234)", -1234.0),
    ("$ (565)", -565.0),
    ("1,234(1)", 1234.0),       # footnote marker after a number is dropped
])
def test_to_number(cell, expected):
    got = val(cell)
    if expected is None:
        assert got is None, f"{cell!r} -> {got!r}, expected None"
    else:
        assert got == pytest.approx(expected), f"{cell!r} -> {got!r}, expected {expected}"


@pytest.mark.parametrize("cell", ["", "-", "$", "12%", "(1)", "n/a", "1,234.56", "  42  ", "*", "(a)", "1e5"])
def test_never_raises(cell):
    """Real tables contain every kind of junk; the normalizer must not crash."""
    to_number(cell)
