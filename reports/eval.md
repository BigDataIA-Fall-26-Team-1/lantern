# Part 9: Evaluation and regression tests

## 1. Ground truth

Rules: `reports/ground_truth_conventions.md`. Data: `data/ground_truth/` (DVC);
fixture copies in `tests/fixtures/gt/` (Git, for CI).

**Pages: 8 per filing, plus 2 fixtures (18 pages).**

| Stratum | FY2025 | FY2024 |
|---|---|---|
| Statements | p32 income, p34 balance sheet, p36 cash flows | same pages |
| Notes | p40, p45 | p40, p45 |
| Prose | p5, p20 | p5, p20 |
| Cover | p1 | p1 |
| Multi-column | `multicol.pdf` fixture (shared) | |
| Scanned | `scanned.pdf` fixture, page 1 (shared) | |

The filings contain no multi-column or scanned page, so those strata use the Part 0
fixtures, shared by both filings: `multicol.pdf` (a public JPMorgan filing page,
documented in `tests/fixtures/README.md`) and `scanned.pdf` (FY2025 page 5 rasterized).

**How pages were made:** typed from the rendered page image, or "html-assisted"
(copied from the original HTML filing, a different source from the PDF text layer the
parsers read, then corrected against the page image). The method of every page is in
`data/ground_truth/pages.csv`. No ground truth was made from parser output.

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

Sources: **pdfplumber** (Part 1 text), **layout** (Part 3 blocks in reading order),
**Docling** (Part 4 per-page Markdown, markup removed).

## 3. Results on the filings (16 pages)

| Source | WER | CER | Numeric recall |
|---|---|---|---|
| pdfplumber | **1.67%** | 1.54% | 99.5% |
| Docling | 6.32% | 4.27% | **87.4%** |
| layout | 12.30% | 10.89% | 99.5% |

**WER per stratum**

| Stratum (pages) | pdfplumber | Docling | layout |
|---|---|---|---|
| Statements (6) | 1.67% | 9.87% | 4.74% |
| Notes (4) | 2.20% | 2.98% | 6.78% |
| Prose (4) | 0.11% | 4.05% | 5.37% |
| Cover (2) | 3.74% | 6.90% | **59.87%** |

**Tables (111 cells, double-keyed)**

| Path | Precision | Recall | F1 |
|---|---|---|---|
| Traditional (Part 2) | 1.0 | 1.0 | 1.0 |
| Docling | 1.0 | 1.0 | 1.0 |

**Fixtures (pdfplumber with OCR fallback, as run in CI)**

| Fixture | WER |
|---|---|
| Statement (FY2025 p32) | 2.27% |
| Multi-column | 7.62% |
| Scanned (OCR) | 0.00% |

## 4. Findings

1. **pdfplumber reads text best on every stratum.** It reads the text layer directly,
   with no model between the page and the words.
2. **Docling misses about 13% of the numbers on a page** (numeric recall 87.4%), most on
   statements (9.9% WER). Part 11 found one cause: on the cash flow statement, Docling
   merged the first data row into the column header, so its values never became cells.
   Its tables, where it finds them, are exact (F1 1.0).
3. **Layout's reading order is the weak point on form-like pages:** 60% WER on the cover,
   where the Part 3 audit found page-sized low-confidence Table and Figure boxes. On
   statements, notes and prose it stays at 5 to 7%, every number present (99.5%).
4. **Multi-column is pdfplumber's hardest stratum** (7.6%): it reads straight across the
   two side-by-side tables, interleaving their rows, while the ground truth reads the
   left table and then the right one.
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

## 8. Limitations

- Ground truth covers 16 filing pages and two fixtures; only two tables are double-keyed.
- The multi-column and scanned strata use fixtures, not pages of these filings.
- The scanned fixture is a clean render; a real scan would score worse.
- Fixture metrics cover pdfplumber only (CI does not run layout or Docling).

## 9. dvc metrics diff

`dvc metrics diff main --md`, run on branch `p9-wrapup`. On `main`, `reports/metrics.json`
was still the empty skeleton placeholder, so every metric appears as new ("-" under
`main`). Later changes to any stage now show up here as numeric changes.
| Path                 | Metric                                     | main   | workspace   | Change   |
|----------------------|--------------------------------------------|--------|-------------|----------|
| reports/metrics.json | keyer_agreement_f1                         | -      | 0.9912      | -        |
| reports/metrics.json | tables.docling.cells_gt                    | -      | 111         | -        |
| reports/metrics.json | tables.docling.f1                          | -      | 1.0         | -        |
| reports/metrics.json | tables.docling.precision                   | -      | 1.0         | -        |
| reports/metrics.json | tables.docling.recall                      | -      | 1.0         | -        |
| reports/metrics.json | tables.docling.tables                      | -      | 2           | -        |
| reports/metrics.json | tables.traditional.cells_gt                | -      | 111         | -        |
| reports/metrics.json | tables.traditional.f1                      | -      | 1.0         | -        |
| reports/metrics.json | tables.traditional.precision               | -      | 1.0         | -        |
| reports/metrics.json | tables.traditional.recall                  | -      | 1.0         | -        |
| reports/metrics.json | tables.traditional.tables                  | -      | 2           | -        |
| reports/metrics.json | text.docling.cer                           | -      | 0.0427      | -        |
| reports/metrics.json | text.docling.numeric_recall                | -      | 0.8743      | -        |
| reports/metrics.json | text.docling.pages                         | -      | 16          | -        |
| reports/metrics.json | text.docling.wer                           | -      | 0.0632      | -        |
| reports/metrics.json | text.layout.cer                            | -      | 0.1089      | -        |
| reports/metrics.json | text.layout.numeric_recall                 | -      | 0.9948      | -        |
| reports/metrics.json | text.layout.pages                          | -      | 16          | -        |
| reports/metrics.json | text.layout.wer                            | -      | 0.123       | -        |
| reports/metrics.json | text.pdfplumber.cer                        | -      | 0.0154      | -        |
| reports/metrics.json | text.pdfplumber.numeric_recall             | -      | 0.9948      | -        |
| reports/metrics.json | text.pdfplumber.pages                      | -      | 16          | -        |
| reports/metrics.json | text.pdfplumber.wer                        | -      | 0.0167      | -        |
| reports/metrics.json | text_by_stratum.cover.docling.cer          | -      | 0.0645      | -        |
| reports/metrics.json | text_by_stratum.cover.docling.pages        | -      | 2           | -        |
| reports/metrics.json | text_by_stratum.cover.docling.wer          | -      | 0.069       | -        |
| reports/metrics.json | text_by_stratum.cover.layout.cer           | -      | 0.5371      | -        |
| reports/metrics.json | text_by_stratum.cover.layout.pages         | -      | 2           | -        |
| reports/metrics.json | text_by_stratum.cover.layout.wer           | -      | 0.5987      | -        |
| reports/metrics.json | text_by_stratum.cover.pdfplumber.cer       | -      | 0.0416      | -        |
| reports/metrics.json | text_by_stratum.cover.pdfplumber.pages     | -      | 2           | -        |
| reports/metrics.json | text_by_stratum.cover.pdfplumber.wer       | -      | 0.0374      | -        |
| reports/metrics.json | text_by_stratum.notes.docling.cer          | -      | 0.0171      | -        |
| reports/metrics.json | text_by_stratum.notes.docling.pages        | -      | 4           | -        |
| reports/metrics.json | text_by_stratum.notes.docling.wer          | -      | 0.0298      | -        |
| reports/metrics.json | text_by_stratum.notes.layout.cer           | -      | 0.0628      | -        |
| reports/metrics.json | text_by_stratum.notes.layout.pages         | -      | 4           | -        |
| reports/metrics.json | text_by_stratum.notes.layout.wer           | -      | 0.0678      | -        |
| reports/metrics.json | text_by_stratum.notes.pdfplumber.cer       | -      | 0.0238      | -        |
| reports/metrics.json | text_by_stratum.notes.pdfplumber.pages     | -      | 4           | -        |
| reports/metrics.json | text_by_stratum.notes.pdfplumber.wer       | -      | 0.022       | -        |
| reports/metrics.json | text_by_stratum.prose.docling.cer          | -      | 0.0124      | -        |
| reports/metrics.json | text_by_stratum.prose.docling.pages        | -      | 4           | -        |
| reports/metrics.json | text_by_stratum.prose.docling.wer          | -      | 0.0405      | -        |
| reports/metrics.json | text_by_stratum.prose.layout.cer           | -      | 0.0493      | -        |
| reports/metrics.json | text_by_stratum.prose.layout.pages         | -      | 4           | -        |
| reports/metrics.json | text_by_stratum.prose.layout.wer           | -      | 0.0537      | -        |
| reports/metrics.json | text_by_stratum.prose.pdfplumber.cer       | -      | 0.0006      | -        |
| reports/metrics.json | text_by_stratum.prose.pdfplumber.pages     | -      | 4           | -        |
| reports/metrics.json | text_by_stratum.prose.pdfplumber.wer       | -      | 0.0011      | -        |
| reports/metrics.json | text_by_stratum.statement.docling.cer      | -      | 0.0727      | -        |
| reports/metrics.json | text_by_stratum.statement.docling.pages    | -      | 6           | -        |
| reports/metrics.json | text_by_stratum.statement.docling.wer      | -      | 0.0987      | -        |
| reports/metrics.json | text_by_stratum.statement.layout.cer       | -      | 0.0366      | -        |
| reports/metrics.json | text_by_stratum.statement.layout.pages     | -      | 6           | -        |
| reports/metrics.json | text_by_stratum.statement.layout.wer       | -      | 0.0474      | -        |
| reports/metrics.json | text_by_stratum.statement.pdfplumber.cer   | -      | 0.0109      | -        |
| reports/metrics.json | text_by_stratum.statement.pdfplumber.pages | -      | 6           | -        |
| reports/metrics.json | text_by_stratum.statement.pdfplumber.wer   | -      | 0.0167      | -        |

