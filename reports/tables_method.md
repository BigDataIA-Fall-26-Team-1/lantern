# Part 2: Table extraction method

**Status:** draft. Evidence below is from the statement fixture
(`tests/fixtures/statement.pdf`, AAPL FY2025 10-K income statement). The run on
both rendered 10-Ks (income statement and balance sheet) is still to do and will
be added once the rendered PDFs are on the DVC remote.

## 1. Methods compared

Every statement page is run through five methods:

| Method | How it finds structure |
|---|---|
| Camelot lattice | Drawn lines and cell borders |
| Camelot stream | Whitespace gaps between text |
| Camelot network | Graph of text alignments |
| Camelot hybrid | Network plus lattice |
| pdfplumber, text strategy | Text positions for both rows and columns |

Raw results: `reports/evidence/bakeoff_fixture.csv`.

## 2. Bake-off on the fixture

| Method | Tables | Shape (cleaned) | Camelot accuracy | Whitespace (cleaned) | Coverage of page numbers | Labels merged with values | Valid |
|---|---|---|---|---|---|---|---|
| lattice | 6 | 5x3 (largest) | 100.0 | 0.0% | 15.2% | 100% | No |
| stream | 1 | 27x4 | 99.64 | 18.5% | 90.9% | 0% | **Yes** |
| network | 1 | 22x3 | 100.0 | 3.0% | 60.6% | 100% | No |
| hybrid | 6 | 5x3 (largest) | 100.0 | 0.0% | 15.2% | 100% | No |
| pdfplumber text | 1 | 32x9 | n/a | 52.4% | 90.9% | 0% | Yes |

## 3. Hand-check: 10 cells per method

Cells read from the PDF page image, then looked up in each method's output. A cell
counts as correct only if the value sits alone in the correct year's column on a row
that carries its own label, because that is what normalization and XBRL matching need.

| # | Cell | PDF value | lattice | stream | network | hybrid | pdfplumber |
|---|---|---|---|---|---|---|---|
| c01 | Total net sales 2025 | 416,161 | ✗ 391,035 | ✓ | ✗ no label | ✗ 391,035 | ✓ |
| c02 | Total net sales 2024 | 391,035 | ✗ 383,285 | ✓ | ✗ no label | ✗ 383,285 | ✓ |
| c03 | Gross margin 2023 | 169,148 | ✗ missing | ✓ | ✗ no label | ✗ missing | ✓ |
| c04 | Operating income 2025 | 133,050 | ✗ 123,216 | ✓ | ✗ no label | ✗ 123,216 | ✓ |
| c05 | Other income/(expense) 2025 | (321) | ✗ 269 | ✓ | ✗ no label | ✗ 269 | ✓ |
| c06 | Other income/(expense) 2023 | (565) | ✗ missing | ✓ | ✗ no label | ✗ missing | ✗ `(565` |
| c07 | Net income 2025 | 112,010 | ✗ 93,736 | ✓ | ✗ no label | ✗ 93,736 | ✓ |
| c08 | Net income 2024 | 93,736 | ✗ 96,995 | ✓ | ✗ no label | ✗ 96,995 | ✓ |
| c09 | Diluted EPS 2025 | 7.46 | ✗ 6.08 | ✓ | ✗ no label | ✗ 6.08 | ✓ |
| c10 | Diluted shares 2025 | 15,004,697 | ✗ 15,408,095 | ✓ | ✗ no label | ✗ 15,408,095 | ✓ |
| | **Correct** | | **0/10** | **10/10** | **0/10** | **0/10** | **9/10** |

All ten PDF values were verified against the rendered page.

## 4. What went wrong, per method

- **Lattice and hybrid** (identical output) split the statement into six fragments
  with no year headers and merged each row label with its 2025 value
  (`Operating income\n133,050`). Reading by column position therefore returns the
  previous year's number every time. The page has no cell borders, only underlines
  under totals and shaded row bands, which lattice treats as grid lines.
- **Network** placed every number in the right year column but dropped all row
  labels, so no value can be attributed to a line item.
- **pdfplumber text** kept values aligned but split labels mid-word
  (`Operating inc | ome`), inserted an empty row between every line, and lost one
  closing parenthesis (`(565` instead of `(565)`), which would flip the sign.
- **Stream** was the only method with labels, year headers and values each in their
  own cells. Products + Services = Total net sales holds in its output.

## 5. Key finding

Camelot's accuracy score ranked the methods almost exactly backwards: lattice and
hybrid scored 100 and stream 90.4, yet stream was the only method with 10/10 usable
cells. Accuracy measures how cleanly text fits into cells, not whether the table's
structure is right. A size-only guard (`min_rows`, `min_cols`) was not enough: in
an earlier run it accepted a 5x3 lattice fragment with a perfect score while every
value sat one column to the left.

## 6. Hybrid extractor design

Implemented in `src/tables.py`; every threshold is in `params.yaml` under `tables`.

1. **Choose an order.** Count horizontal rules at least `rule_min_len_pt` long.
   At least `min_rulings` means a ruled page (`order_ruled`, lattice first);
   otherwise `order_borderless` (stream first).
2. **Structural checks first.** A table is valid only if it has at least
   `min_rows` x `min_cols` cells, holds at least `min_coverage` of the page's
   numbers, and at most `max_label_merge` of its labels end in a number.
3. **Then score.** `score = accuracy - whitespace_weight x whitespace`, with
   whitespace measured on the cleaned table (empty and `$`-only columns removed),
   so methods are not penalized for columns that are discarded anyway.
4. **Accept or fall back.** The first flavor with a valid table scoring at least
   `accept_score` wins; otherwise the best valid table is kept and logged as
   `best_below_threshold`. Every decision is written to `tables_log.csv`.

On the fixture this selects stream (score 90.38, coverage 0.909, label merge 0.0).

## 7. Normalization

Raw and normalized grids are stored side by side (`.raw.csv`, `.norm.csv`).

- `(1,234)` becomes -1234, and `(1,234` with a missing closing parenthesis is also
  treated as negative (seen in pdfplumber output).
- Dashes become 0; `$`, commas and trailing footnote markers are stripped.
- The scale comes from the caption. For AAPL, *"In millions, except number of
  shares, which are reflected in thousands, and per-share amounts"* gives money rows
  x 1,000,000, share-count rows x 1,000 and per-share rows unscaled.
- Each row is tagged `header`, `money`, `shares` or `per_share`; year rows are
  headers and never scaled.

Checked on the fixture: Net income 112,010 becomes 112010000000; (321) becomes
-321000000; Diluted EPS stays 7.46; Diluted shares 15,004,697 becomes 15004697000.

## 8. Preferred method per table type

| Table type | Preferred | Evidence |
|---|---|---|
| Borderless financial statements | Camelot stream | Fixture: 10/10 hand-check, only valid structure |
| Ruled tables (exhibits) | Lattice first, structural checks still applied | To confirm on the real filings |

## 9. Still to do

- [ ] Run on both AAPL 10-Ks: income statement and balance sheet, both fiscal years
- [ ] Repeat the hand-check on a balance sheet page
- [ ] Find a table where stream loses (the brief asks for a counter-example)
- [ ] Confirm `min_coverage` and `max_label_merge` hold beyond one page
