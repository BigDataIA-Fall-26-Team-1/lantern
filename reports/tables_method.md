# Part 2: Table extraction method

## 1. Scope and data

- **Fixture:** `tests/fixtures/statement.pdf` (the FY2025 income statement page).
- **Real filings:** both AAPL 10-Ks from `data/rendered/` (`AAPL_10K_20240928`, `AAPL_10K_20250927`).
- **Statements found:** pages 32 to 36 in both filings: income, comprehensive income,
  balance sheet, shareholders' equity, cash flows.
- **Evidence files:** `reports/evidence/bakeoff_fixture.csv`, `bakeoff_real.csv`,
  `tables_log_fixture.csv`, `tables_log_real.csv`.

Reproduce:

    python src/tables.py --input tests/fixtures --output /tmp/tables_fixture
    python src/tables.py --output /tmp/tables_real

## 2. Methods compared

| Method | How it finds structure |
|---|---|
| Camelot lattice | Drawn lines and cell borders |
| Camelot stream | Whitespace gaps between text |
| Camelot network | Graph of text alignments |
| Camelot hybrid | Network plus lattice |
| pdfplumber, text strategy | Text positions for rows and columns (bake-off reference only) |

## 3. Finding statement pages

A page is a statement page when a statement title is (nearly) a whole line within
its first `title_top_lines` lines and the page has at least `min_numeric_tokens`
numbers. Two problems on the real filings, both fixed:

- **False positive:** the Item 15 exhibit index (page 56/57) lists
  "Consolidated Statements of Operations for the years ended ... 29" near the top
  and has many numbers. Fix: the title may have at most `title_max_extra_chars`
  extra characters on its line, so index lines are rejected.
- **Miss:** the comprehensive income statement (page 33) is short and had fewer
  than 40 numbers. Fix: `min_numeric_tokens` lowered to 20, which is safe once the
  title rule is strict.

Result: exactly pages 32 to 36 in both filings, no false positives.

## 4. What the extractor chose

Identical in both filings (Apple uses the same layout each year).

| Page | Statement | Method chosen | Shape | Score |
|---|---|---|---|---|
| 32 | Income | stream | 27x4 | 90.38 |
| 33 | Comprehensive income | stream | 16x4 | 87.21 / 87.08 |
| 34 | Balance sheet | **network** | 39x3 | 87.95 |
| 35 | Shareholders' equity | stream | 23x4 | 91.36 / 91.37 |
| 36 | Cash flows | stream | 38x4 | 91.88 |

## 5. Bake-off on two statement pages

**Income statement** (fixture = FY2025 page 32)

| Method | Shape | Accuracy | Whitespace | Coverage | Label merge | Valid |
|---|---|---|---|---|---|---|
| lattice | 5x3 (largest of 6) | 100.0 | 0.0% | 15.2% | 100% | No |
| **stream** | 27x4 | 99.64 | 18.5% | 90.9% | 0% | **Yes** |
| network | 22x3 | 100.0 | 3.0% | 60.6% | 100% | No |
| hybrid | 5x3 (largest of 6) | 100.0 | 0.0% | 15.2% | 100% | No |
| pdfplumber text | 32x9 | n/a | 52.4% | 90.9% | 0% | Yes |

**Balance sheet** (FY2025 page 34)

| Method | Shape | Accuracy | Whitespace | Coverage | Label merge | Valid |
|---|---|---|---|---|---|---|
| lattice | 7x2 (largest of 5) | 100.0 | 0.0% | 10.8% | 100% | No |
| stream | 17x4 | 98.35 | 33.8% | 40.0% | 0% | No |
| **network** | 39x3 | 97.35 | 18.8% | 86.2% | 0% | **Yes** |
| hybrid | 7x2 (largest of 5) | 100.0 | 0.0% | 10.8% | 100% | No |
| pdfplumber text | 41x8 | n/a | 54.0% | 89.2% | 2.9% | Yes |

Whitespace is measured on the cleaned table. Coverage is the share of the page's
numbers that landed in value cells. Label merge is the share of labels that end in
a number, which means values shifted into the label column.

## 6. Hand-checks: 10 cells per method on each statement

Values were read from the PDF page image. A cell is correct only if the value sits
in the correct year's column on a row a reader can identify.

**Income statement**

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

**Balance sheet**

| # | Cell | PDF value | lattice | stream | network | hybrid | pdfplumber |
|---|---|---|---|---|---|---|---|
| b01 | Cash and cash equivalents 2025 | 35,934 | ✗ merged | ✓ | ✓ | ✗ merged | ✓ |
| b02 | Marketable securities (current) 2024 | 35,228 | ✗ wrong col | ✓ | ✓ | ✗ wrong col | ✓ |
| b03 | Total current assets 2025 | 147,957 | ✗ merged | ✓ | ✓ | ✗ merged | ✓ |
| b04 | Total assets 2025 | 359,241 | ✗ merged | ✓ | ✓ | ✗ merged | ✓ |
| b05 | Total assets 2024 | 364,980 | ✗ wrong col | ✓ | ✓ | ✗ wrong col | ✓ |
| b06 | Term debt (non-current) 2025 | 78,328 | ✗ merged | ✗ missing | ✓ | ✗ merged | ✓ |
| b07 | Total liabilities 2024 | 308,030 | ✗ wrong col | ✗ missing | ✓ | ✗ wrong col | ✓ |
| b08 | Common stock and APIC 2025 | 93,568 | ✗ merged | ✗ missing | ✓ | ✗ merged | ✓ |
| b09 | Accumulated deficit 2025 | (14,264) | ✗ merged | ✗ missing | ✓ | ✗ merged | ✓ |
| b10 | Total liabilities and equity 2025 | 359,241 | ✗ merged | ✗ missing | ✓ | ✗ merged | ✓ |
| | **Correct** | | **0/10** | **5/10** | **10/10** | **0/10** | **10/10** |

All twenty PDF values were verified against the rendered pages.

## 7. How each method failed

- **Lattice and hybrid** (identical output on both pages) split each statement into
  fragments with no year headers and merged every row label with its latest-year
  value (`Operating income\n133,050`). Reading by column position returns the wrong
  year every time. The pages have no cell borders, only underlines under totals and
  shaded row bands, which lattice treats as grid lines.
- **Stream** was perfect on the income statement but stopped at "Total assets" on
  the balance sheet, missing the whole liabilities and equity half (coverage 40%).
- **Network** captured the whole balance sheet with labels intact, but dropped every
  row label on the income statement.
- **pdfplumber text** kept values aligned on both pages, but split labels mid-word
  (`Cash and cas | h equivalents`), cut off section headers (`ent assets:`), added an
  empty row between every line, and dropped closing parentheses on three negatives
  (`(565`, `(19,154`, `(7,172`).

## 8. Key findings

1. **Camelot's accuracy score ranks the methods almost exactly backwards.** Lattice
   and hybrid score 100 on both pages yet get 0/10 on both hand-checks. Accuracy
   measures how cleanly text fits into cells, not whether the structure is right.
2. **No single method wins every statement.** Stream wins the income statement (10/10
   vs network 0/10); network wins the balance sheet (10/10 vs stream 5/10). This is
   the counter-example for stream, and the reason for a per-page hybrid extractor.
3. **Size-only guards are not enough.** With only `min_rows` and `min_cols`, an early
   version accepted a 5x3 lattice fragment with a perfect score while every value
   sat one column to the left.

## 9. Hybrid extractor design

Implemented in `src/tables.py`; every threshold is in `params.yaml` under `tables`.

1. **Clean.** Strip cells; drop empty rows and columns; drop `$`-only columns and any
   value column with no digits (a `$` column that also caught the caption); join a
   label that wraps onto a second line (first line has no values and ends in `;` or
   `,`, second line starts with a lowercase letter or a digit).
2. **Choose an order.** At least `min_rulings` horizontal rules of length
   `rule_min_len_pt` or more means a ruled page (`order_ruled`, lattice first);
   otherwise `order_borderless` (stream first). All AAPL statement pages are
   borderless.
3. **Structural checks.** Valid only with at least `min_rows` x `min_cols` cells,
   coverage of at least `min_coverage`, and label merge of at most `max_label_merge`.
4. **Score.** `accuracy - whitespace_weight x whitespace`, on the cleaned table.
5. **Accept or fall back.** The first flavor with a valid table scoring at least
   `accept_score` wins; otherwise the best valid table is kept as
   `best_below_threshold`. Every decision is written to `tables_log.csv`.

On the balance sheet, stream is tried first and rejected (coverage 40%), then
network is accepted (87.95).

## 10. Normalization

Raw and normalized grids are stored side by side (`.raw.csv`, `.norm.csv`).

- `(1,234)` becomes -1234; `(1,234` with a missing closing parenthesis is also negative.
- Dashes become 0; `$` and commas are stripped; trailing footnote markers are stripped
  (`(a)`, `*`, `[1]`, and `(1)` directly after a number). A standalone `(1)` is -1.
- The scale comes from the caption. For AAPL, "In millions, except number of shares,
  which are reflected in thousands, and per-share amounts" gives money rows
  x 1,000,000, share-count rows x 1,000 and per-share rows unscaled.
- Each row is tagged `header`, `money`, `shares` or `per_share`; year rows are headers
  and never scaled. Percentages are kept unscaled with unit `pct`.
- 25 unit tests in `tests/test_tables_normalize.py` pass.

Checked: Net income 112,010 becomes 112010000000; (321) becomes -321000000;
Diluted EPS stays 7.46; Diluted shares 15,004,697 becomes 15004697000.
Balance sheet: Total liabilities 285,508 + Total shareholders' equity 73,733 =
359,241 = Total assets.

## 11. Preferred method per table type

| Statement | Preferred | Evidence |
|---|---|---|
| Income statement | stream | Hand-check 10/10; network 0/10 |
| Balance sheet | network | Hand-check 10/10; stream 5/10 (missed half the page) |
| Comprehensive income, equity, cash flows | stream | Chosen by the extractor with valid structure in both filings; not hand-checked |

## 12. Known limitations for later parts

- **Curly apostrophes:** labels like "Shareholders’ equity" use `’`; label matching in
  Part 11 should treat `’` and `'` as the same.
- **Wrapped labels** are joined only for the `;` / `,` pattern seen here; other
  wrapping styles may still split.
- **Only borderless statements were tested:** the lattice-first path for ruled pages
  is implemented but has no evidence on these filings.
- **pdfplumber drops closing parentheses** on negatives; the normalizer compensates,
  but it is one more reason not to use it as an extractor.