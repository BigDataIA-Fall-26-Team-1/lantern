summary: Build a reproducible, layout-aware, XBRL-validated parsing pipeline for SEC 10-K filings, versioned with DVC.
id: lantern-case-study-1
categories: data-engineering
tags: dvc, pdf-parsing, ocr, xbrl, docling
environments: Web
status: Draft
authors: BigDataIA Fall 2026 Team 1

# Project LANTERN: Parsing SEC Filings into a Traceable Corpus

## Overview
Duration: 0:03:00

FinTrust Analytics wants every number in an analyst memo to point back to a page and a bounding box in the source filing. Project LANTERN builds that corpus: it downloads two Apple (AAPL) 10-K filings (FY2024 and FY2025) from EDGAR, renders them to PDF, extracts text, tables and layout, attaches provenance, checks the numbers against the filing's own XBRL, and versions every artifact with DVC.

### What you will build

- A DVC pipeline with the stages `download`, `render`, `parse_pdfplumber`, `tables`, `layout`, `parse_docling`, `export`, `xbrl`, `evaluate`
- A provenance-tagged JSONL corpus plus section Markdown
- Evaluation metrics, benchmarks, an XBRL validation report and a build-vs-buy recommendation

### Architecture

TODO: add the architecture diagram image.

![LANTERN architecture](img/architecture.png)

### Team

TODO: names and the Parts each member owned.

## Setup and reproduction
Duration: 0:10:00

### Prerequisites

- Python 3.11
- System packages: Tesseract, Poppler (and Ghostscript if Camelot asks for it)
- Chromium for Playwright (only needed if you re-run the `render` stage)

macOS:

```bash
brew install tesseract poppler
```

Ubuntu / WSL:

```bash
sudo apt-get install -y tesseract-ocr poppler-utils
```

### Reproduce the corpus

These are the exact commands from the brief's reproducibility contract:

```bash
git clone <repo-url> lantern && cd lantern
git checkout submission
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
dvc pull
dvc repro
dvc metrics show
pytest -q
```

`dvc pull` reads from the S3 remote documented in the README (read-only, no credentials needed). After a pull, `dvc repro` skips every stage, because every output already matches `dvc.lock`.

TODO: screenshot of `dvc repro` showing every stage skipped.

![dvc repro skipping stages](img/setup-dvc-repro.png)

## Part 0: Download and render
Duration: 0:05:00

TODO (owner): fill in. Checklist from the brief:

- `src/download.py`: filings from `params.yaml` with `download_details=True`, `full-submission.txt` unpacked into `unpacked/`
- `src/render.py`: `data/rendered/{TICKER}_{FORM}_{PERIOD}.pdf` and `data/rendered/manifest.csv`
- The three fixtures in `tests/fixtures/` (scanned, statement, multi-column from another filing, documented in `tests/fixtures/README.md`)
- Commands: `dvc repro download`, `dvc repro render`
- Results: page count and MB per filing, `data/raw` size under 100 MB
- Screenshot: one rendered page, `manifest.csv`

## Part 1: Text extraction with OCR fallback
Duration: 0:08:00

The `parse_pdfplumber` stage reads every rendered PDF page by page. For each page it saves the text, saves every word with its bounding box, and decides whether the page needs OCR.

### What the stage writes

| Output | Contents |
|---|---|
| `data/parsed/{stem}_p{NNNN}.txt` | Text of one page |
| `data/parsed/{stem}.words.jsonl` | One word per line with `bbox` in PDF points, top-left origin |
| `data/parsed/ocr_log.csv` | One row per OCR decision: document, page, trigger reason, engine, mean confidence |

### The OCR trigger

A page goes to Tesseract when at least one of two signals fires: too few characters, or too many junk tokens such as `(cid:NN)`. Both thresholds and the OCR resolution live in `params.yaml`:

```yaml
ocr:
  min_chars: 50
  junk_ratio: 0.3
  dpi: 300
```

Tesseract returns boxes in pixels. The stage converts them back to points (`pt = px * 72 / dpi`) so OCR words and pdfplumber words use the same coordinates.

### Run it

On the full corpus:

```bash
dvc repro parse_pdfplumber
```

On the committed fixtures only (this is what CI runs):

```bash
python src/parse_text.py --input tests/fixtures --output /tmp/parsed_fixtures
```

### Check the results

```bash
# one .txt per page
ls data/parsed/AAPL_10K_20250927_p*.txt | wc -l

# a word record
head -n 1 data/parsed/AAPL_10K_20250927.words.jsonl

# OCR decisions
column -s, -t < data/parsed/ocr_log.csv | head
```

TODO: results.

- Pages per filing and `.txt` files per filing (should be equal)
- OCR pages on the scanned fixture: all of them, with mean confidence
- OCR pages on the rendered filings (expected: none, or the log explains why)

![OCR log for the scanned fixture](img/p1-ocr-log.png)

![Scanned fixture page and its OCR text](img/p1-scanned-fixture.png)

<aside class="positive">
Rendered EDGAR PDFs almost never need OCR, so the scanned fixture is the only real test of the OCR path. That is why it is committed to Git and run in CI.
</aside>

## Part 2: Tables and the hybrid extractor
Duration: 0:05:00

TODO (owner): fill in. Checklist from the brief:

- Bake-off on two or more statement pages: Camelot lattice, stream, network or hybrid, pdfplumber "text"; shape, parsing report, 10 hand-checked cells per method
- `src/tables.py` hybrid extractor and its log of which method won
- Normalization: parentheses, dashes, currency, footnote markers, scale, per-share exception; raw and normalized cells
- Command: `dvc repro tables`
- Results: bake-off table from `reports/tables_method.md`
- Screenshot: a clean income statement CSV next to the PDF page

## Part 3: Layout detection and routing
Duration: 0:05:00

TODO (owner): fill in. Checklist from the brief:

- `data/layout/{stem}.blocks.jsonl`, figure crops in `data/figures/`
- Routing, reading order, section attachment
- Command: `dvc repro layout` (or note if the stage is frozen, and why)
- Results: audit table from `reports/layout_audit.md`
- Screenshot: one QA overlay from `reports/layout/`

## Part 4: Docling path and comparison
Duration: 0:05:00

TODO (owner): fill in. Checklist from the brief:

- `src/docling_parse.py`, run as `python -m src.docling_parse`
- Markdown, JSON, table CSVs, per-page Markdown for WER; HTML vs rendered-PDF comparison
- Command: `dvc repro parse_docling`
- Results: comparison table and recommendation from `reports/docling_comparison.md` (WER, cell F1, XBRL match rate for both paths)
- Screenshot: one table, Docling vs traditional

## Part 5: Metadata schema and provenance
Duration: 0:08:00

The `export` stage turns the traditional path's outputs (layout blocks routed through Parts 1 and 2) into one JSONL record per block, then rebuilds each section as Markdown.

### The schema

`src/schema.py` defines the Appendix B schema as a pydantic model. Every record is validated when it is written, so a bad field fails the stage that produced it.

| Field | Example | Source |
|---|---|---|
| `doc_id` | accession number | `manifest.csv` |
| `cik`, `ticker`, `form` | `0000320193`, `AAPL`, `10-K` | `manifest.csv` |
| `fiscal_year`, `fiscal_period` | `2025`, `FY` | `dei:DocumentFiscalYearFocus`, `dei:DocumentFiscalPeriodFocus` |
| `page`, `block_id`, `block_type` | `45`, `p0045_b003`, `Table` | layout stage |
| `bbox`, `units`, `origin` | `[x0, top, x1, bottom]`, `pt`, `top-left` | layout stage |
| `section` | `Item 7` | Item heading, else nearest Title |
| `table` | `{columns, rows, raw_cells, scale}` | tables stage |
| `extractor`, `extractor_version`, `ocr`, `ocr_conf` | | the stage that produced the text |
| `source_path`, `sha256` | | the rendered PDF |

### Run it

```bash
dvc repro export
```

### Check the results

One record, pretty-printed:

```bash
head -n 1 data/export/AAPL_10K_20250927.jsonl | python -m json.tool
```

Keys are identical across documents (should print one set of keys):

```bash
python -c "import json,glob; print({tuple(sorted(json.loads(l))) for f in glob.glob('data/export/*.jsonl') for l in open(f)})"
```

Provenance in the Markdown. Every block is preceded by an HTML comment with the document, page and block id, which a reader never sees but a chunker can keep:

```bash
grep -n -A1 "<!--" data/export/AAPL_10K_20250927.md | head
```

### Trace one number

TODO: pick the net income line. Show the Markdown line, its comment, the matching JSONL record (page, bbox), and the highlighted box on the rendered page.

![JSONL record](img/p5-jsonl-record.png)

![Markdown with provenance comments](img/p5-markdown-provenance.png)

![bbox highlighted on the rendered page](img/p5-bbox-highlight.png)

## Part 6: Storage formats
Duration: 0:05:00

One filing is exported in three formats: JSONL and Markdown from Part 5, plus a plain-text baseline at `data/export/{stem}.txt`. Each was measured for size and approximate tokens (characters / 4), and the same three retrieval-style questions were asked of each in an LLM chat interface.

### Check the results

```bash
column -s, -t < reports/format_stats.csv
ls reports/format_test/
```

TODO: copy the numbers from `reports/format_stats.csv` and the answers from `reports/format_test/`.

| Format | Size | Approx. tokens | Q1 | Q2 | Q3 |
|---|---|---|---|---|---|
| JSONL | | | | | |
| Markdown | | | | | |
| TXT | | | | | |

TODO: the decision in two sentences, from `reports/format_decision.md`: which format is the source of truth, which feeds Case Study 2, and why.

![Question asked of the Markdown export](img/p6-question.png)

## Part 7: Build vs buy with AWS Textract
Duration: 0:08:00

The same pages were sent through AWS Textract (`AnalyzeDocument` with `TABLES`): a clean statement page and a page from the scanned fixture, well under the 10-page limit. Textract's output was mapped into the Part 5 schema and compared cell by cell with the open-source output.

### The fallback

Textract is wired in as an optional fallback in `src/managed/`. It is used only when a page's OCR confidence or a table's score is low, and every response is cached by page hash in `data/managed/`.

```yaml
managed:
  enabled: false
  provider: textract
```

With `enabled: false` (the default) the code reads cache hits but never calls the API, so the pipeline runs without AWS credentials. The cache is a DVC-tracked folder, not a stage output:

```bash
dvc add data/managed      # already done; creates data/managed.dvc
dvc pull data/managed.dvc # graders get the cached responses
dvc repro                 # succeeds with the fallback disabled
```

<aside class="negative">
Live calls need AWS credentials in environment variables or an AWS profile, never in the repo. They use a separate IAM user with Textract permissions only, kept apart from the DVC remote user. A billing alert was set before the first call.
</aside>

### Check the results

TODO: results from `reports/managed/` and `reports/build_vs_buy.md`.

- Side-by-side: one page of text and one table, Textract vs open source; which errors Textract fixed and which it introduced
- Price per 1,000 pages from the AWS public pricing page (date checked), cost for this corpus and for 5,000 filings a year
- Data-handling questions for client documents: region, retention, training use
- Recommendation: whether and where to use a managed service

![Textract vs open source, one table](img/p7-side-by-side.png)

![Cache hit with managed.enabled false](img/p7-cache-hit.png)

## Part 8: DVC pipeline and CI
Duration: 0:05:00

TODO (owner): fill in. Checklist from the brief:

- `dvc.yaml` with all nine canonical stages, `dvc.lock` committed, `dvc dag` output
- A second `dvc repro` skips every stage
- `.github/workflows/smoke.yml`: what it installs and runs; screenshot of a green PR run
- Frozen stages, if any, and why

### DVC remote access

TODO (Preksha): remote type (S3, us-east-2), how read-only access works without credentials, and the exact `dvc remote` config graders will see. Keep it consistent with the README section.

## Part 9: Evaluation and regression tests
Duration: 0:05:00

TODO (owner): fill in. Checklist from the brief:

- Ground truth: 10 pages per filing across strata, two statement CSVs, conventions
- WER and CER per page and stratum, numeric-token accuracy, cell P/R/F1, both paths
- Command: `dvc repro evaluate`, `dvc metrics show`, `dvc metrics diff`
- The documented failing test run
- Screenshot: `reports/plots/drift.png`

## Part 10: Cost and throughput
Duration: 0:05:00

TODO (owner): fill in. Checklist from the brief:

- Batch of 50 to 100 pages per stage: s/page p50 and p95, peak RSS, failures, cold vs warm
- Cost per 1,000 pages and for 5,000 filings a year, open source vs managed, with assumptions
- Bottleneck stages and hardware recommendation; machine specs
- Results: table from `reports/benchmarks.md`

## Part 11: XBRL extraction and validation
Duration: 0:05:00

TODO (owner): fill in. Checklist from the brief:

- `src/xbrl.py` with Arelle, `data/xbrl/facts.csv`, `config/label_map.yaml`
- Mapping method per line (manual, label, fuzzy)
- Match rate per statement and per path (traditional and Docling)
- Every non-match with its diagnosed cause and fix, from `reports/xbrl.md`
- Screenshot: the notebook comparison table

## Summary and recommendations
Duration: 0:03:00

TODO: one short paragraph each, using numbers from the reports:

- Primary parsing path and fallback (Part 4)
- Source-of-truth format and the format for Case Study 2 (Part 6)
- Build vs buy (Parts 7 and 10)

### Links

- Repository: TODO
- Demo video: TODO
- Deployed app: TODO
