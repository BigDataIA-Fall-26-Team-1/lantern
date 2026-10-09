summary: Build a reproducible, layout-aware, XBRL-validated parsing pipeline for SEC 10-K filings, versioned with DVC.
id: lantern-case-study-1
categories: data-engineering
tags: dvc, pdf-parsing, ocr, xbrl, docling
environments: Web
status: Draft
authors: BigDataIA Fall 2026 Team 1
feedback link: https://github.com/BigDataIA-Fall-26-Team-1/lantern/issues

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

Big Data Fall 2026 Team 1
 1. Pradyumna Reddy Cherla
 2. Pranav Avinash Waghmare
 3. Preksha Praveen


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
| `data/parsed/ocr_log.csv` | One row per page: document, page, triggered, reason, engine, mean confidence, and the three signal values |

### The OCR trigger

The trigger checks three signals on every page: the character count, the share of junk tokens such as `(cid:NN)`, and how much of the page is covered by images. The `reason` column in the log records which signals fired. All thresholds, the OCR resolution and the Tesseract settings live in `params.yaml`:

```yaml
ocr:
  min_chars: 50
  junk_ratio: 0.3
  dpi: 300
  image_coverage: 0.6
  tesseract_config: "--oem 1 --psm 6"
```

Tesseract returns boxes in pixels. The stage converts them back to points (`pt = px * 72 / dpi`) so OCR words and pdfplumber words use the same coordinates.

### Run it

On the full corpus:

```bash
dvc repro parse_pdfplumber
```

On the committed fixtures only (the same command CI runs):

```bash
python src/parse_text.py --input tests/fixtures --output /tmp/parsed_fixtures
```

### Check the results

Every page has a `.txt` file:

```bash
pdfinfo data/rendered/AAPL_10K_20250927.pdf | grep Pages
ls data/parsed/AAPL_10K_20250927_p*.txt | wc -l
```

| Filing | PDF pages | `.txt` files | Pages sent to OCR |
|---|---|---|---|
| AAPL_10K_20250927 | 61 | 61 | 0 |
| AAPL_10K_20240928 | 60 | 60 | 0 |

A word record from `words.jsonl`:

```json
{"doc_id": "0000320193-25-000079", "stem": "AAPL_10K_20250927", "page": 1, "text": "UNITED", "bbox": [255.33, 67.76, 303.71, 80.75], "units": "pt", "origin": "top-left", "source": "pdfplumber", "ocr": false, "conf": null}
```

The OCR log for the fixtures (the `sed` fills empty cells so `column` keeps them aligned):

```bash
sed 's/,,/,-,/g' /tmp/parsed_fixtures/ocr_log.csv | column -s, -t
```

| Fixture | Page | Triggered | Reason | Engine | Mean conf | Text chars |
|---|---|---|---|---|---|---|
| scanned | 1 | True | low_chars; image_coverage | tesseract | 95.4 | 3,808 |
| scanned | 2 | True | low_chars; image_coverage | tesseract | 95.7 | 6,108 |
| scanned | 3 | True | low_chars; image_coverage | tesseract | 95.5 | 4,565 |
| statement | 1 | False | none | pdfplumber | n/a | 1,194 |
| multicol | 1 | False | none | pdfplumber | n/a | 3,846 |

The scanned fixture has no text layer (0 characters, full-page image), so every page goes to Tesseract and comes back with non-empty text. The born-digital fixtures and all 121 pages of the two filings stay on pdfplumber.

![OCR log for the fixtures](img/p1-ocr-log.png)

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

`src/schema.py` defines the Appendix B schema as a pydantic model (`"schema": "lantern/1.0"`). Every record is validated when it is written, so a bad field fails the stage that produced it.

| Field | Example from the FY2025 10-K | Source |
|---|---|---|
| `doc_id` | `0000320193-25-000079` | accession number, from `manifest.csv` |
| `company`, `cik`, `ticker`, `form` | `Apple Inc.`, `0000320193`, `AAPL`, `10-K` | `manifest.csv` |
| `fiscal_year`, `fiscal_period` | `2025`, `FY` | `dei:DocumentFiscalYearFocus`, `dei:DocumentFiscalPeriodFocus` |
| `page`, `block_id`, `block_type` | `1`, `p0001_b001`, `Table` | layout stage |
| `bbox`, `units`, `origin` | `[8.38, 39.0, 555.3, 762.93]`, `pt`, `top-left` | layout stage |
| `section` | Item heading, else nearest Title | export stage |
| `text`, `table` | block text, or `{columns, rows, raw_cells, scale}` | Parts 1 and 2 |
| `extractor`, `extractor_version` | `pdfplumber`, `pdfplumber 0.11.10` | the stage that produced the text |
| `ocr`, `ocr_conf` | `false`, `null` | Part 1 |
| `source_path`, `sha256` | `data/rendered/AAPL_10K_20250927.pdf`, file hash | the rendered PDF |

Each record also carries three fields beyond Appendix B: `detector` and `detector_score` (the layout model and its confidence for the block) and `figure_path` (the crop in `data/figures/` for Figure blocks).

### Run it

```bash
dvc repro export
```

### Check the results

One record, pretty-printed:

```bash
head -n 1 data/export/AAPL_10K_20250927.jsonl | python -m json.tool
```

The first record on page 1 is a cover-page block that the layout model (`tf_efficientdet_d0`) labelled `Table` with a score of 0.254. It carries no `table` object, and its text came from pdfplumber. This is the PubLayNet domain shift the tutorial warns about: the model was trained on journal articles, not SEC cover pages.

Keys are identical across all records in both filings. This prints a set with exactly one tuple of keys:

```bash
python -c "import json,glob; print({tuple(sorted(json.loads(l))) for f in glob.glob('data/export/*.jsonl') for l in open(f)})"
```

```text
{('bbox', 'block_id', 'block_type', 'cik', 'company', 'detector', 'detector_score', 'doc_id', 'extractor', 'extractor_version', 'figure_path', 'fiscal_period', 'fiscal_year', 'form', 'ocr', 'ocr_conf', 'origin', 'page', 'schema', 'section', 'sha256', 'source_path', 'table', 'text', 'ticker', 'units')}
```

Provenance in the Markdown. Every block is preceded by an HTML comment with the accession number, page and block id. A reader never sees it, but a chunker in Case Study 2 can keep it as metadata. Use single quotes here: in zsh, `!` inside double quotes triggers history expansion.

```bash
grep -n -A1 '<!--' data/export/AAPL_10K_20250927.md | head
```

```text
3:<!-- 0000320193-25-000079 p1 p0001_b001 -->
12:<!-- 0000320193-25-000079 p1 p0001_b002 -->
66:<!-- 0000320193-25-000079 p1 p0001_b003 -->
72:<!-- 0000320193-25-000079 p1 p0001_b004 -->
```

![JSONL record](img/p5-jsonl-record.png)

![Markdown with provenance comments](img/p5-markdown-provenance.png)

### Trace one number

Net income for FY2025 appears in the Markdown as a table row (values in millions, FY2025, FY2024, FY2023):

```bash
grep -n -i 'net income' data/export/AAPL_10K_20250927.md | head -5
```

```text
2591:| Net income | 112,010 | 93,736 | 96,995 |
```

The nearest provenance comment above that line names the block it came from:

```bash
awk 'NR<=2591 && /<!--/{c=$0} NR==2591{print c; exit}' data/export/AAPL_10K_20250927.md
```

```text
<!-- 0000320193-25-000079 p32 p0032_b003 -->
```

The matching JSONL record (selected fields):

```bash
python -c "import json; [print(json.dumps({k:r[k] for k in ('page','block_id','block_type','bbox','section','extractor')})) for r in map(json.loads, open('data/export/AAPL_10K_20250927.jsonl')) if r['block_id']=='p0032_b003']"
```

```json
{"page": 32, "block_id": "p0032_b003", "block_type": "Table", "bbox": [3.39, 95.22, 604.84, 548.43], "section": "Item 8", "extractor": "camelot-stream"}
```

So the number traces back to a Table block on page 32 of the rendered PDF, in Item 8 (Financial Statements), extracted with Camelot stream. The bbox covers the whole statement table, not the single row: provenance is stored per block.

To draw that box on the page:

```bash
python -c "import pdfplumber; pdf=pdfplumber.open('data/rendered/AAPL_10K_20250927.pdf'); pdf.pages[31].to_image(resolution=110).draw_rect((3.39, 95.22, 604.84, 548.43), stroke='red', stroke_width=3).save('docs/img/p5-bbox-highlight.png')"
```


![bbox highlighted on the rendered page](img/p5-bbox-highlight.png)

## Part 6: Storage formats
Duration: 0:06:00

The `export` stage writes every filing in three formats from the same validated records, so they never disagree about content. They differ only in what they keep.

| Role | Format | File |
|---|---|---|
| Source of truth | JSONL | `data/export/{stem}.jsonl` |
| Feeds Case Study 2 (retrieval and question answering) | Markdown, generated from the JSONL records and never edited by hand | `data/export/{stem}.md` |
| Baseline only | TXT | `data/export/{stem}.txt` |

### Measure size and token cost

```bash
python -m src.format_stats
cat reports/format_stats.csv
```

This writes `reports/format_stats.csv` and cuts a 4-page test slice (FY2025 PDF pages 31 to 34: the Item 8 heading, statement index, income statement, comprehensive income and balance sheet) into `reports/format_test/`. Tokens are approximated as characters / 4.

| Scope | Format | Bytes | Approx. tokens | vs TXT |
|---|---|---|---|---|
| FY2025 full filing | JSONL | 795,499 | 198,384 | 3.9× |
| | Markdown | 249,975 | 62,062 | 1.2× |
| | TXT | 207,509 | 51,446 | 1.0× |
| FY2024 full filing | JSONL | 811,191 | 202,352 | 4.0× |
| | Markdown | 248,304 | 61,669 | 1.2× |
| | TXT | 204,869 | 50,810 | 1.0× |
| FY2025 pages 31–34 | JSONL | 30,565 | 7,635 | 6.4× |
| | Markdown | 6,449 | 1,610 | 1.4× |
| | TXT | 4,771 | 1,190 | 1.0× |

JSONL costs about 4× the tokens of plain text for a whole filing, and 6.4× on statement pages, where every table is stored twice (raw and normalized) with its metadata fields. Markdown costs 20 to 40% more than plain text while keeping the Item headings, the tables and a provenance comment before every block.

### Ask the same three questions of each format

The same slice was given to the same model in a new chat per format, with a prompt that said to use only the document. Scoring rules were fixed before any answer was seen. Answers are recorded word for word in `reports/format_test/llm_answers.md`.

| Question | Tests | Correct answer |
|---|---|---|
| Q1. Net income in the latest fiscal year, and on which page? | provenance | $112,010 million; PDF page 32 (printed page 29) |
| Q2. "Other income/(expense), net" in fiscal 2023: income or expense? | sign, table structure | $(565) million, an expense |
| Q3. Which Item has the balance sheets; total assets at the end of fiscal 2025? | section, number | Item 8; $359,241 million |

| Format | Scored run | Where the Q1 page came from |
|---|---|---|
| JSONL | 3 / 3 | the `page` field, plus the footer record for the printed page |
| Markdown | 3 / 3 | the provenance comment `p32` |
| TXT | 2 / 3 + 1 partial | matching detached page numbers to statement titles by order; the answer changed between runs |

All three formats got every number and sign right. The difference is how well each answer is grounded: JSONL and Markdown gave the page from explicit markers, while TXT had to guess.

![Scored results of the format test, Run 2](img/p6-question.png)

### The decision

JSONL is the source of truth. It is the only format that keeps every field (page, bbox, block id, extractor version, raw and normalized cells, sha256), and Part 11 and any Case Study 2 citation need those fields. At about 200,000 tokens per filing, it is too expensive as LLM context.

Markdown feeds Case Study 2. It answered as accurately as JSONL at about a fifth of the tokens on the slice, its `## Item` headings let text be split by section, and every block keeps its `<!-- doc_id pN block_id -->` comment, which leads back to the JSONL record and its bbox.

TXT is kept only as a baseline, to show what the structure in the other two formats is worth.

<aside class="negative">
Limits of this test: one model, two runs, one 4-page slice of statement pages, and lookup questions whose answers appear in all three formats. Token counts are characters / 4, not a real tokenizer. Full details are in <code>reports/format_decision.md</code>.
</aside>

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

- Repository: [github.com/BigDataIA-Fall-26-Team-1/lantern](https://github.com/BigDataIA-Fall-26-Team-1/lantern)
- Demo video: TODO
- Deployed app: [http://52.15.107.141:8501/](http://52.15.107.141:8501/)
