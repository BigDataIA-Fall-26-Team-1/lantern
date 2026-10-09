# Part 9: Evaluation and regression tests

## 1. Ground truth

Rules: `reports/ground_truth_conventions.md`. Filing pages: `data/ground_truth/` (DVC).
Fixture pages: `tests/fixtures/gt/` (Git), read directly by the evaluation and by CI.

**Pages: 10 per filing across the six strata (18 distinct pages).**

| Stratum | FY2025 | FY2024 |
|---|---|---|
| Statements | p32 income, p34 balance sheet, p36 cash flows | same pages |
| Notes | p40, p45 | p40, p45 |
| Prose | p5, p20 | p5, p20 |
| Cover | p1 | p1 |
| Multi-column | `multicol.pdf` fixture (shared) | |
| Scanned | `scanned.pdf` fixture, page 1 (shared) | |

The filings contain no multi-column or scanned page, so those strata use the Part 0
fixtures, shared by both filings, as the brief allows: `multicol.pdf` (a public JPMorgan
filing page, documented in `tests/fixtures/README.md`) and `scanned.pdf` (FY2025 page 5
rasterized). The DVC stage `parse_fixtures` runs every extraction path (pdfplumber with
OCR fallback, layout, Docling) on the fixtures, so these two strata are scored exactly
like the filing pages. The statement fixture is FY2025 page 32, already scored, so it is
excluded from the main metrics to avoid counting it twice.

**How pages were made:** typed from the rendered page image, or "html-assisted"
(copied from the original HTML filing, a different source from the PDF text layer the
parsers read, then corrected against the page image). The method of every page is in
`pages.csv`. No ground truth was made from parser output.

**Tables:** the FY2025 income statement and balance sheet (111 cells) were keyed by two
people independently. They agreed on 110 of 111 cells (F1 0.991). The one difference, a
digit transposition in diluted shares for 2024 (15,048,095 vs 15,408,095), was resolved
against the page image (15,408,095).

## 2. Metrics

`src/evaluate.py`, DVC stage `evaluate`, output `reports/metrics.json`.

- **WER / CER** per page, averaged per stratum. Normalization on both sides: Unicode
  NFKC, curly quotes to straight, dashes to "-", "$" separated, whitespace collapsed,
  lowercase. **Punctuation is kept**, so a lost sign such as (321) to 321 is an error.
- **Numeric recall:** share of the page's numbers (sign included) found in the output.
- **Cell precision / recall / F1:** each table becomes (row label, year, occurrence) ->
  value, so tables of different shapes are compared fairly.

Sources: **pdfplumber** (Part 1 text, with the Tesseract OCR fallback), **layout**
(Part 3 blocks in reading order), **Docling** (Part 4 per-page Markdown, markup removed).
In `eval_pages.csv`, fixture pages are marked `source_set = fixture`.

## 3. Results (18 pages)

| Source | WER | CER | Numeric recall |
|---|---|---|---|
| pdfplumber | **1.91%** | 1.76% | **99.5%** |
| Docling | 11.71% | 9.72% | 83.2% |
| layout | 12.37% | 10.88% | 96.7% |

**WER per stratum**

| Stratum (pages) | pdfplumber | Docling | layout |
|---|---|---|---|
| Statements (6) | **1.67%** | 9.87% | 4.74% |
| Notes (4) | **2.20%** | 2.98% | 6.78% |
| Prose (4) | **0.11%** | 4.05% | 5.37% |
| Cover (2) | **3.74%** | 6.90% | 59.87% |
| Multi-column (1, fixture) | 7.62% | 9.60% | **3.15%** |
| Scanned (1, fixture) | **0.00%** | 100.00% | 22.79% |

**Tables (111 cells, double-keyed)**

| Path | Precision | Recall | F1 |
|---|---|---|---|
| Traditional (Part 2) | 1.0 | 1.0 | 1.0 |
| Docling | 1.0 | 1.0 | 1.0 |

## 4. Findings

1. **pdfplumber reads text best on five of six strata.** It reads the text layer
   directly, and its OCR fallback reads the scanned page perfectly.
2. **Multi-column is where layout earns its place: 3.2% vs pdfplumber's 7.6%.**
   pdfplumber reads straight across the two side-by-side tables and interleaves their
   rows; layout's column-aware reading order reads the left table, then the right.
3. **Docling cannot read a scanned page in our setup (100% WER).** It runs without OCR,
   so an image-only page yields no text. This is exactly the case the traditional path's
   OCR fallback (Part 1) exists for. On filing pages Docling sits between the other two,
   and it misses numbers (numeric recall 83%): Part 11 found one cause, a cash flow row
   merged into the table header. Its tables, where it finds them, are exact (F1 1.0).
4. **Layout breaks on form-like pages:** 60% WER on the cover, where the Part 3 audit
   found page-sized low-confidence Table and Figure boxes, and 23% on the scanned page,
   where each detected block is OCR'd separately, which is less accurate than OCR on the
   whole page.
5. **OCR scored 0.0 WER on the scanned fixture (544 words).** The image is a clean
   digital render, not a physical scan, so this is a best case for Tesseract, not a
   typical one.
6. **Evaluation found a Part 4 bug:** Docling's per-page Markdown was shifted by one page
   (its p32 file held p31). Its WER was 90% until the fix; the numbers above are after it.

## 5. Regression tests

`tests/test_quality.py` runs Part 1 and Part 2 on the fixtures, grades them with
`evaluate.py`, and compares with thresholds in `params.yaml` (`eval.thresholds`):
measured baseline plus a margin.

| Check | Measured | Fails if |
|---|---|---|
| Statement text WER | 2.27% | above 5% |
| Numeric recall | 100% | below 98% |
| Table cell F1 | 1.0 | below 0.98 |
| Scanned (OCR) WER | 0.0% | above 5% |

`pytest -q`: 65 passed. Runs in CI with no network, DVC data or credentials.

## 6. A failing run, on purpose

With Part 2's structural guards switched off and lattice first (a temporary parameter
file, `LANTERN_PARAMS=/tmp/params_broken.yaml`), the selector reverted to lattice
fragments, which have no year headers. **Table cell F1 fell from 1.0 to 0.0** and
`test_table_cell_f1` failed; both text tests still passed.
Logs: `reports/evidence/quality_failing_run.txt`, `quality_passing_run.txt`.

## 7. Drift between two pipeline versions

`src/plot_drift.py` -> `reports/plots/drift.png`. Signal: layout block length (words per
block), both filings, with Part 3's text snapping distance at 0 pt and at 15 pt.

| | 0 pt | 15 pt |
|---|---|---|
| Blocks | 1,796 | 1,705 (-5.1%) |
| Blocks under 5 words | 35.0% | 31.6% |
| Median words per block | 8 | 9 |
| Total words kept | 61,375 | 61,423 |

The drop is concentrated in 1- and 2-word blocks: clipped words joining their paragraph
instead of standing alone. The 48 extra words at 15 pt are fragments so short that the
fallback's minimum-length filter discarded them at 0 pt. (A distance of 0 still snaps
words that straddle a box edge, so the full effect of snapping is larger: when it was
introduced, fallback blocks fell by 30%; see `reports/layout_audit.md`.)

## 8. Challenges

| Problem | Fix |
|---|---|
| Docling's per-page files shifted by one page (90% WER) | Reported; fixed in `docling_parse.py` and its stage rerun |
| Four pages listed twice in `pages.csv` (20 instead of 16) | File rewritten; averages had been double-weighted |
| A page file named `p036` instead of `p0036` | Renamed; it had been silently skipped |
| Fixture strata scored only in CI, pdfplumber only | New `parse_fixtures` stage runs all three paths on the fixtures |
| Running `python src/docling_parse.py` failed with a circular import | The script's name shadows the `docling_parse` package; run as a module (`python -m src.docling_parse`), as its own stage does |
| Docling's script needs a manifest | A small `tests/fixtures/manifest_fixtures.csv` (named so other scripts do not pick it up) |

## 9. Limitations

- Ground truth covers 16 filing pages and two fixtures; only two tables are double-keyed.
- The multi-column and scanned strata have one page each, from fixtures.
- The scanned fixture is a clean render; a real scan would score worse.
- Docling runs without OCR here; with OCR enabled its scanned score would change.

## 10. dvc metrics diff

`dvc metrics diff main --md`, run on branch `p9-fixture-strata`: the change from scoring
16 filing pages in 4 strata to 18 pages in all 6 strata.
| Path                 | Metric                                       | main   | workspace   | Change   |
|----------------------|----------------------------------------------|--------|-------------|----------|
| reports/metrics.json | text.docling.cer                             | 0.0427 | 0.0972      | 0.0545   |
| reports/metrics.json | text.docling.numeric_recall                  | 0.8743 | 0.8318      | -0.0425  |
| reports/metrics.json | text.docling.pages                           | 16     | 18          | 2        |
| reports/metrics.json | text.docling.wer                             | 0.0632 | 0.1171      | 0.0539   |
| reports/metrics.json | text.layout.cer                              | 0.1089 | 0.1088      | -0.0001  |
| reports/metrics.json | text.layout.numeric_recall                   | 0.9948 | 0.9667      | -0.0281  |
| reports/metrics.json | text.layout.pages                            | 16     | 18          | 2        |
| reports/metrics.json | text.layout.wer                              | 0.123  | 0.1237      | 0.0007   |
| reports/metrics.json | text.pdfplumber.cer                          | 0.0154 | 0.0176      | 0.0022   |
| reports/metrics.json | text.pdfplumber.numeric_recall               | 0.9948 | 0.9954      | 0.0006   |
| reports/metrics.json | text.pdfplumber.pages                        | 16     | 18          | 2        |
| reports/metrics.json | text.pdfplumber.wer                          | 0.0167 | 0.0191      | 0.0024   |
| reports/metrics.json | text_by_stratum.multicolumn.docling.cer      | -      | 0.0662      | -        |
| reports/metrics.json | text_by_stratum.multicolumn.docling.pages    | -      | 1           | -        |
| reports/metrics.json | text_by_stratum.multicolumn.docling.wer      | -      | 0.096       | -        |
| reports/metrics.json | text_by_stratum.multicolumn.layout.cer       | -      | 0.0353      | -        |
| reports/metrics.json | text_by_stratum.multicolumn.layout.pages     | -      | 1           | -        |
| reports/metrics.json | text_by_stratum.multicolumn.layout.wer       | -      | 0.0315      | -        |
| reports/metrics.json | text_by_stratum.multicolumn.pdfplumber.cer   | -      | 0.0704      | -        |
| reports/metrics.json | text_by_stratum.multicolumn.pdfplumber.pages | -      | 1           | -        |
| reports/metrics.json | text_by_stratum.multicolumn.pdfplumber.wer   | -      | 0.0762      | -        |
| reports/metrics.json | text_by_stratum.scanned.docling.cer          | -      | 1.0         | -        |
| reports/metrics.json | text_by_stratum.scanned.docling.pages        | -      | 1           | -        |
| reports/metrics.json | text_by_stratum.scanned.docling.wer          | -      | 1.0         | -        |
| reports/metrics.json | text_by_stratum.scanned.layout.cer           | -      | 0.1817      | -        |
| reports/metrics.json | text_by_stratum.scanned.layout.pages         | -      | 1           | -        |
| reports/metrics.json | text_by_stratum.scanned.layout.wer           | -      | 0.2279      | -        |
| reports/metrics.json | text_by_stratum.scanned.pdfplumber.cer       | -      | 0.0         | -        |
| reports/metrics.json | text_by_stratum.scanned.pdfplumber.pages     | -      | 1           | -        |
| reports/metrics.json | text_by_stratum.scanned.pdfplumber.wer       | -      | 0.0         | -        |

