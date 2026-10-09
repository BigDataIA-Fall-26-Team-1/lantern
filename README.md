# Project LANTERN — Financial Report Parsing Pipeline

DAMG 7245 Big Data and Intelligent Analytics · Fall 2026 · Case Study 1 (Part 1) · Team 1

| | |
|---|---|
| **Codelab** | [https://bigdataia-fall-26-team-1.github.io/lantern/lantern-case-study-1/](https://bigdataia-fall-26-team-1.github.io/lantern/lantern-case-study-1/) |
| **Demo video** | TODO: link |
| **Deployed app** | [http://52.15.107.141:8501](http://52.15.107.141:8501) |

## Project Summary

FinTrust Analytics' analysts read 10-K filings by hand and cannot always say where a number came from. LANTERN automates this for Apple (AAPL), two 10-K filings (FY2024, period ending 2024-09-28; FY2025, period ending 2025-09-27).

The pipeline:

1. downloads each filing and its Inline XBRL from SEC EDGAR, and renders the HTML filing to PDF;
2. extracts text (pdfplumber, with a Tesseract OCR fallback), tables (Camelot/pdfplumber hybrid) and layout blocks (LayoutParser EfficientDet);
3. runs Docling as a second, independent parsing path;
4. writes provenance-tagged JSONL (every record has page and bbox), section Markdown and a TXT baseline;
5. validates every extracted statement number against the filing's own XBRL facts (Arelle);
6. measures quality (WER, CER, numeric recall, table cell F1), throughput and cost, and compares the open-source path with a managed service (AWS Textract).

Everything is a DVC stage driven by `params.yaml`, so the corpus and every metric rebuild with `dvc repro`.

### Headline results

| Question | Answer | Source |
|---|---|---|
| Text accuracy (18 ground-truth pages) | pdfplumber WER **1.91%**, Docling 11.71%, layout-routed 12.37% | `reports/metrics.json`, `reports/eval.md` |
| Numeric recall in text | pdfplumber **99.5%**, Docling 83.2%, layout 96.7% | `reports/metrics.json` |
| Table cells (2 statements, 111 double-keyed cells) | F1 **1.0** on both paths | `reports/metrics.json` |
| XBRL match rate | Traditional **456 / 456 (100%)**, Docling 450 / 452 (99.6%) | `reports/xbrl.md` |
| Throughput (median s/page) | Traditional **0.70**, Docling 4.52 | `reports/benchmarks.md` |
| Cost at 5,000 filings/year | Traditional **$5.63**, Docling $36.35, Textract list price $4,537.50 | `reports/benchmarks.md` |
| Primary path | Traditional pipeline; Docling as fallback; Textract as an off-by-default cached fallback | `reports/docling_comparison.md`, `reports/build_vs_buy.md` |
| Source of truth | JSONL; Markdown (generated from it) feeds Case Study 2 | `reports/format_decision.md` |

## Architecture Diagram

![System Architecture](docs/img/architecture.png)


## Repository Layout

```
lantern/
  dvc.yaml  dvc.lock  params.yaml  requirements.txt
  .dvc/config                      # DVC remotes (public read-only + team write)
  .github/workflows/smoke.yml      # CI smoke test on every pull request
  config/label_map.yaml            # curated PDF label -> XBRL concept map
  src/                             # one script per DVC stage (+ src/managed/ for Textract)
  data/                            # tracked by DVC, not Git
  tests/                           # pytest suites + small fixtures committed to Git
  reports/                         # every written deliverable, metrics and plots
  notebooks/xbrl_validation.ipynb
  docs/codelab.md                  # Codelab source; exported site in docs/lantern-case-study-1/
  app/  deploy/                    # Streamlit UI, FastAPI backend, deployment scripts
```

## Prerequisites

- Linux (graders) or macOS/Windows; **Python 3.11**
- System packages:
  ```bash
  sudo apt-get update
  sudo apt-get install -y tesseract-ocr poppler-utils libgl1
  ```
  Tesseract is needed for the OCR fallback; Poppler (`pdftoppm`, `pdfseparate`) was used to build the test fixtures; `libgl1` is needed by OpenCV.
- Only if you re-run stages from scratch (not needed after `dvc pull`):
  ```bash
  playwright install chromium     # render stage (HTML -> PDF)
  ```
  The first `layout` and `parse_docling` runs download model weights from Hugging Face, and the first `xbrl` run lets Arelle cache the US-GAAP taxonomy, so these need internet access once.

## Reproduction Steps

```bash
git clone https://github.com/BigDataIA-Fall-26-Team-1/lantern.git lantern && cd lantern
git checkout submission
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
dvc pull            # from the public remote, no credentials needed
dvc repro           # runs with managed.enabled: false (no API calls)
dvc metrics show
pytest -q
```

What to expect:

- After `dvc pull`, `dvc repro` should skip or restore every stage, because `dvc.lock` matches the pulled data. A second `dvc repro` with no changes skips everything.
- `managed.enabled: false` is the default in `params.yaml`. The Textract fallback then reads only its cache in `data/managed/` and never calls AWS, so the pipeline runs without credentials.
- `pytest -q` runs 65 tests.
- **Run `docling_parse` as a module** (`python -m src.docling_parse`), never as `python src/docling_parse.py`: the file name shadows the installed `docling_parse` package. The DVC stage already does this.

## DVC Remote Access

`.dvc/config` defines two remotes for the same S3 bucket:

| Remote | URL | Use |
|---|---|---|
| `public` (default) | `https://lantern-dvc-team1-2026.s3.us-east-2.amazonaws.com/dvc` | read-only over HTTPS; `dvc pull` works with **no credentials** |
| `store` | `s3://lantern-dvc-team1-2026/dvc` (us-east-2) | team write access for `dvc push` |

To push (team members only): get the AWS key from the team, then

```bash
dvc remote default --local store
dvc remote modify --local store access_key_id <KEY_ID>
dvc remote modify --local store secret_access_key <SECRET>
```

These go into `.dvc/config.local`, which is never committed.

## Pipeline Stages

All canonical stage names from the brief are present in `dvc.yaml`. There are **no frozen stages**: every stage, including LayoutParser, runs locally.

| Stage | Command (abridged) | Main output |
|---|---|---|
| `download` | `python src/download.py --output data/raw` | `data/raw/` (filings, unpacked iXBRL + `.xsd`) |
| `render` | `python src/render.py --input data/raw --output data/rendered` | `data/rendered/*.pdf`, `manifest.csv` |
| `parse_pdfplumber` | `python src/parse_text.py --input data/rendered --output data/parsed` | page text, word boxes, `ocr_log.csv` |
| `tables` | `python src/tables.py --input data/rendered --output data/tables` | raw and normalized statement CSVs |
| `layout` | `python src/layout.py --input data/rendered --output data/layout --figures data/figures` | `blocks.jsonl`, figure crops |
| `parse_docling` | `python -m src.docling_parse --input data/rendered --output data/docling --raw data/raw` | Docling Markdown, JSON, table CSVs, per-page text |
| `export` | `python -m src.export ...` | `data/export/{stem}.jsonl`, `.md`, `.txt` |
| `xbrl` | `python src/xbrl.py` | `data/xbrl/facts.csv`, `comparison.csv`, `summary.json` |
| `parse_fixtures` *(extra)* | parse_text + layout + docling on `tests/fixtures/` | `data/fixtures/` (scored by `evaluate` for the scanned and multi-column strata) |
| `evaluate` | `python src/evaluate.py --out reports ...` | `reports/metrics.json`, `eval_pages.csv`, `eval_tables.csv` |

Not stages, but tracked with `dvc add`:

- `data/managed/` — the Textract response cache (7 pages), a dependency of `parse_pdfplumber` and `export`.
- `data/ground_truth/` — hand-made ground truth (Part 9).
- `data/bench/` — benchmark measurements (Part 10). Timings vary between runs, so they are a measurement, not a reproducible stage. Re-run with the commands in `reports/benchmarks.md` §9.

File naming: files use the **stem** (rendered PDF name, e.g. `AAPL_10K_20250927`); records carry `doc_id`, the accession number. `data/rendered/manifest.csv` maps one to the other.

## Expected Run Times

Measured on an Intel Core 7 150U laptop (12 threads, 15.7 GiB RAM, no GPU), both filings (60 + 61 pages). Details in `reports/benchmarks.md`.

| Stage | Expected time (from scratch) | Basis |
|---|---|---|
| After `dvc pull` | all stages skipped, seconds | `dvc.lock` matches |
| `parse_pdfplumber` | about 15 s | 0.096 s/page median |
| `tables` | a few seconds | 0.21 s/page on 5 statement pages per filing |
| `layout` | about 1.5–2 min, plus 3–8 s model load | 0.58 s/page median, full stage |
| `parse_docling` | **about 27 min** (966 s FY2024, 653 s FY2025, plus 97 s for the HTML conversion) | `data/docling/timing.csv` |
| `export` | under 1 s | 0.005 s/page |
| `download`, `render`, `xbrl`, `evaluate`, `parse_fixtures` | not timed separately | — |

Docling is CPU-heavy and peaks at about 3.6 GiB RAM; run it with nothing else heavy open.

## Continuous Integration

`.github/workflows/smoke.yml` runs on every pull request (and on pushes to `main`):

1. sets up Python 3.11 and installs `tesseract-ocr` and `libgl1`;
2. installs `requirements.txt`;
3. runs Part 1 text extraction and Part 2 table extraction on `tests/fixtures/`;
4. runs `pytest -q`.

It needs no EDGAR access, no DVC remote and no cloud credentials: every stage script accepts `--input` / `--output` paths, and the fixtures and their ground truth are committed to Git.

Regression thresholds live in `params.yaml` under `eval.thresholds` (measured baseline plus a margin). A deliberately broken run is recorded in `reports/evidence/quality_failing_run.txt` (table cell F1 fell from 1.0 to 0.0).

## Test Fixtures

`tests/fixtures/` (Git, not DVC; sources in `tests/fixtures/README.md`):

| File | Source |
|---|---|
| `statement.pdf` | AAPL FY2025 page 32, income statement |
| `scanned.pdf` | AAPL FY2025 pages 5–7, rasterized at 300 DPI and rebuilt as an image-only PDF |
| `multicol.pdf` | JPMorgan 10-K FY2024 page 10 (AAPL has no multi-column pages) |
| `gt/` | transcriptions of each fixture page and a CSV of the statement table |

## Part-to-Code Map

| Part | Code | Reports and evidence |
|------|------|---------|
| P0 Repository, data, rendering | `src/download.py`, `src/render.py`, `params.yaml` | `data/rendered/manifest.csv`, `tests/fixtures/README.md` |
| P1 Text + OCR fallback | `src/parse_text.py` | `data/parsed/ocr_log.csv` |
| P2 Tables | `src/tables.py` | `reports/tables_method.md`, `reports/evidence/bakeoff_*.csv`, `reports/evidence/tables_log_*.csv` |
| P3 Layout | `src/layout.py` | `reports/layout_audit.md`, `reports/layout/*.png` |
| P4 Docling | `src/docling_parse.py` | `reports/docling_comparison.md` |
| P5 Schema + provenance | `src/schema.py`, `src/export.py` | `data/export/*.jsonl`, `data/export/*.md` |
| P6 Storage formats | `src/format_stats.py`, `src/export.py` | `reports/format_decision.md`, `reports/format_stats.csv`, `reports/format_test/` |
| P7 Managed service | `src/managed/` | `reports/build_vs_buy.md`, `reports/managed/`, `data/managed.dvc` |
| P8 DVC + CI | `dvc.yaml`, `dvc.lock`, `params.yaml`, `.github/workflows/smoke.yml` | this README |
| P9 Evaluation + regression | `src/evaluate.py`, `src/plot_drift.py`, `tests/test_quality.py` | `reports/eval.md`, `reports/metrics.json`, `reports/eval_*.csv`, `reports/ground_truth_conventions.md`, `reports/plots/drift.png`, `reports/evidence/quality_*_run.txt` |
| P10 Benchmarks + cost | `src/bench.py` | `reports/benchmarks.md`, `data/bench/` |
| P11 XBRL validation | `src/xbrl.py`, `config/label_map.yaml`, `notebooks/xbrl_validation.ipynb` | `reports/xbrl.md`, `data/xbrl/` |
| Docs | `docs/codelab.md` | `docs/lantern-case-study-1/` (exported Codelab) |
| App | `app/api/`, `app/ui/`, `deploy/` | Deployed app section below |

## Codelab

**Link:** [Project LANTERN Codelab](https://bigdataia-fall-26-team-1.github.io/lantern/lantern-case-study-1/)

A step-by-step walkthrough of the pipeline, one step per Part, with commands, results and screenshots.

- Source: `docs/codelab.md` (claat Markdown format)
- Exported site: `docs/lantern-case-study-1/`, published with GitHub Pages from `main`, folder `/docs`
- To rebuild after editing the source:
  ```bash
  cd docs
  claat export codelab.md
  ```
- To preview locally:
  ```bash
  cd docs/lantern-case-study-1
  claat serve      # then open http://localhost:9090/
  ```

## Demo Video

TODO: link

## Deployed App

**URL:** [http://52.15.107.141:8501](http://52.15.107.141:8501) (Streamlit UI; the FastAPI backend runs on the same server, bound to localhost only)

The app is a read-only viewer over the pipeline outputs. It runs no pipeline stages and calls no paid APIs.

| Page | What it shows |
|---|---|
| Home | The two filings from `manifest.csv`, page and record counts, records by block type |
| Explorer | Any rendered page with every block's bbox drawn on it, and the page's JSONL records |
| Trace | Search a value or phrase and follow it to the page, the bbox, the JSONL record and the Markdown line with its provenance comment |
| Tables | Each extracted table: normalized values next to the raw cell strings, with extractor and scale |
| Reports | Every file in `reports/`, grouped by Part |

**Architecture:** one EC2 instance (Ubuntu 24.04, us-east-2, same region as the DVC remote). systemd runs two services: `lantern-api` (FastAPI, `app/api/main.py`, 127.0.0.1:8000) and `lantern-ui` (Streamlit, `app/ui/`, port 8501). No load balancer or NAT gateway. The security group allows port 8501 from anywhere and SSH from one IP.

**Data:** the server runs `dvc pull data/export data/rendered` from the public read-only DVC remote, so it holds no AWS or GitHub credentials.

**Deploying:** the org disables GitHub deploy keys, so code is copied from a clean local checkout with `rsync` (excluding `.venv/`, `data/`, the DVC cache and `.env`).

1. First time, on the server: `bash deploy/setup.sh` (installs Python 3.11, the app packages from `app/requirements-app.txt`, pulls the data, starts both services).
2. Updates: check out the branch or tag locally, re-run the rsync, then on the server run `bash deploy/update.sh`. It prints the deployed commit.

**Running locally:**

```bash
pip install -r app/requirements-app.txt
uvicorn app.api.main:app --port 8000
LANTERN_API=http://127.0.0.1:8000 streamlit run app/ui/Home.py
```

## Generative AI Use Declaration

We used Claude (Anthropic) for initial ideas on the architecture diagram, for help with coding, and for help drafting and editing documentation (this README, the Codelab and report text). In Part 6, Claude was also the model the three test questions were asked of, as recorded in `reports/format_test/llm_answers.md`. The team reviewed, tested and modified all AI-assisted output, and every measurement, metric and conclusion in `reports/` comes from our own pipeline runs.

## Attestation

WE ATTEST THAT WE HAVEN'T USED ANY OTHER STUDENTS' WORK IN OUR ASSIGNMENT AND ABIDE BY THE POLICIES LISTED IN THE STUDENT HANDBOOK.

- Member 1: Preksha Praveen — 33%
- Member 2: Pradyumna Reddy Cherla — 33%
- Member 3: Pranav Waghmare — 33%
