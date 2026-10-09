# Part 10 · Cost and throughput benchmarks

Every number in this report comes from a CSV in `data/bench/` (tracked with DVC) or from a cited price.
The harness is `src/bench.py`; it calls the pipeline's own functions, so it times the real code.

## 1. Machine

From `data/bench/machine.json`.

| Item | Value |
|---|---|
| CPU | Intel(R) Core(TM) 7 150U |
| Cores | 10 physical, 12 logical |
| RAM | 15.7 GiB |
| GPU | none |
| OS | Windows 10.0.26300 |
| Python | 3.11.2 |
| Key packages | pdfplumber 0.11.10, pytesseract 0.3.13 (Tesseract 5.5.3), camelot-py 2.0.0, layoutparser 0.3.4, docling 2.134.0, torch 2.14.1 |
| Date | 2026-10-08 |

The laptop was plugged in, with other heavy applications closed. Each stage ran in its own fresh process.

## 2. Batch

All **61 pages** of `AAPL_10K_20250927.pdf` (FY2025), which falls within the brief's 50–100 pages and covers every page type: cover, prose, the five primary statements, dense notes and exhibits. The `tables` stage only runs on statement pages, so it was benchmarked on the 5 pages the pipeline detects (p32–36).

## 3. Per-stage throughput

From `data/bench/summary.csv` (`python src/bench.py --summarize`). Seconds per page are for warm pages; for the two model stages, runs 1 and 2 are pooled.

| Stage | Pages | s/page p50 | s/page p95 | Peak RSS MiB | Failures | Notes |
|---|---|---|---|---|---|---|
| parse_pdfplumber | 61 | 0.096 | 0.233 | 513 | 0 | text layer + word boxes |
| ocr_tesseract | 61 | 1.505 | 2.895 | 85 | 0 | **forced** on every page at 300 DPI; Tesseract's own memory is not in Python's RSS |
| tables | 5 | 0.210 | 0.798 | 537 | 0 | statement pages only (Camelot hybrid) |
| layout (`detect()` only) | 61 | 0.249 | 0.806 | 1,055 | 0 | the EfficientDet model step |
| layout (full stage) | 61 | 0.58 | 3.19 | — | 0 | from the pipeline's own `data/layout/layout_log.csv` (one run); includes Camelot on table regions and OCR on empty blocks |
| parse_docling | 61 | 4.517 | 19.215 | 3,597 | 0 | one page per `convert()` call, TableFormer `accurate` |
| export | 61 | 0.005 | 0.005 | 105 | 0 | whole filing (0.318 s) ÷ 61 pages |

**Failures** counts rows whose status is not `ok` (empty output or an exception). There were none in any stage.

**Why OCR was forced.** No page of the rendered filings triggers the OCR rule, so the OCR cost was measured by running Tesseract on every page. That is the cost of a scanned filing.

**Two layout figures.** Our benchmark times only the model (`detect()`). The full `layout` stage also runs Camelot on each detected table region and OCR on blocks with no text layer, so it is slower: 0.58 s median and 3.19 s at p95. The cost model uses the full-stage figure, since that is what the pipeline spends.

## 4. Cold vs warm (model stages)

From `data/bench/cold_start.csv`. Each run is a fresh process.

| Stage | Run | Model load s | First page s | Warm p50 s | Warm p95 s | Peak RSS MiB |
|---|---|---|---|---|---|---|
| layout | 1 | 7.49 | 0.60 | 0.28 | 0.82 | 1,053 |
| layout | 2 | 3.01 | 0.37 | 0.23 | 0.66 | 1,055 |
| parse_docling | 1 | 9.23 | 4.41 | 5.71 | 19.29 | 1,338 |
| parse_docling | 2 | 3.54 | 3.05 | 4.48 | 17.06 | 3,597 |

- **Model load** shrinks by more than half on the second run for both stages. The first run reads the weights from disk; the second finds them in the operating system's file cache. A long-running worker pays this once, not per page.
- **Docling loads its models lazily**, on the first `convert()` call. The benchmark calls `initialize_pipeline()` inside the model-load timing, otherwise the model load would be hidden inside page 1.
- **Docling's memory is not stable between runs.** In run 2, memory jumped from about 1.1 GiB to 3.5 GiB at page 37 and stayed there; in run 1 the peak was 1.3 GiB. Capacity planning below uses the worse figure.
- Pages 57–59 (near the exhibit index at the end of the filing) are the slowest pages in OCR, layout and Docling alike.

## 5. Cost

### Assumptions

| Assumption | Value | Source |
|---|---|---|
| Instance | m7i-flex.large (2 vCPU, 8 GiB, x86), us-east-2, Linux, on-demand | team choice: free-plan eligible, enough RAM for Docling + LayoutParser, likely deployment target |
| Hourly price | **$0.09576/hr** | AWS EC2 On-Demand pricing page (https://aws.amazon.com/ec2/pricing/on-demand/), US East (Ohio), Linux, checked 2026-10-08 |
| Speed | the timings above, measured on the laptop in §1 | **assumes m7i-flex.large is about as fast as the laptop** (see §8) |
| Workers | 1 | per-worker cost; more workers change wall-clock time, not total CPU-hours |
| Volume | 5,000 filings/year × 60.5 pages/filing = **302,500 pages/year** | 60.5 = mean of our two filings (60 and 61 pages) |
| Managed service | AWS Textract `AnalyzeDocument` TABLES + LAYOUT at **$0.015/page** | AWS Textract pricing page, as cited in `reports/build_vs_buy.md` (Part 7) |
| Engineering time | **not included** | |

Formula: cost per 1,000 pages = s/page × 1,000 ÷ 3,600 × $0.09576; cost per year = s/page × 302,500 ÷ 3,600 × $0.09576.

### Open-source path

| Path | s/page | CPU-hours / year | USD per 1,000 pages | USD per year |
|---|---|---|---|---|
| Traditional, p50 (pdfplumber 0.096 + tables 0.017 + full layout 0.58 + export 0.005) | 0.70 | 58.8 | 0.019 | 5.63 |
| Traditional, p95 (0.233 + 0.065 + 3.19 + 0.005) | 3.49 | 293.3 | 0.093 | 28.08 |
| Traditional + OCR on every page, p50 (+1.505) | 2.21 | 185.3 | 0.059 | 17.74 |
| Traditional + OCR on every page, p95 (+2.895) | 6.39 | 536.5 | 0.170 | 51.38 |
| Docling, p50 | 4.52 | 379.6 | 0.120 | 36.35 |
| Docling, p95 | 19.22 | 1,614.6 | 0.511 | 154.62 |

The tables stage only runs on the 5 statement pages, so its per-page share is 0.210 × 5 ÷ 61 = 0.017 s (p95: 0.798 × 5 ÷ 61 = 0.065 s). Adding p95 figures across stages is deliberately pessimistic, since no page is slowest in every stage at once.

### Managed service

| Service | USD per 1,000 pages | USD per year (302,500 pages) |
|---|---|---|
| Textract TABLES + LAYOUT, list price | 15.00 | 4,537.50 |

### Comparison

At 5,000 filings a year, the most pessimistic open-source figure (Docling at p95, $155/year) is about **29× cheaper** than Textract at list price, and the traditional path at the median is about **800× cheaper**. At this volume, as the course slides put it, engineering hours dominate every line: the compute bill for the open-source path is smaller than one day of engineering time. Textract only becomes attractive where its accuracy saves engineering effort, which Part 7 evaluates (it is kept as an off-by-default cached fallback).

## 6. Bottlenecks

Share of the traditional path's median time per page (0.70 s):

| Stage | s/page p50 | Share |
|---|---|---|
| layout (full stage) | 0.58 | 83% |
| parse_pdfplumber | 0.096 | 14% |
| tables (per page of filing) | 0.017 | 2% |
| export | 0.005 | 1% |

- **On the traditional path, `layout` is the bottleneck**, and a little more than half of its time is the table extraction and routing around the model, not the model itself (0.25 s of 0.58 s is `detect()`).
- **Docling is the bottleneck of the whole pipeline**: 4.5 s/page at the median, about 6.5× the entire traditional path, and 19 s at p95. Its time is concentrated on table-heavy pages (the statements and notes, p32–51, take 8–42 s each), which is TableFormer reconstructing table structure on the CPU.
- **OCR** is cheap only because no real page needs it. A scanned filing would cost about 1.5 s more per page at the median, tripling the traditional path's cost.

## 7. Recommendations

### Hardware: CPU vs GPU

| Instance | vCPU / RAM | GPU | us-east-2 on-demand | Source |
|---|---|---|---|---|
| m7i-flex.large | 2 / 8 GiB | none | $0.09576/hr | AWS EC2 On-Demand pricing page, checked 2026-10-08 |
| g4dn.xlarge | 4 / 16 GiB | 1× NVIDIA T4 | $0.526/hr | AWS EC2 On-Demand pricing page, checked 2026-10-08 |

We did not benchmark on a GPU, so we do not claim a speed-up. From the prices alone, g4dn.xlarge costs $0.526 ÷ $0.09576 = **5.5× more per hour**, so it only lowers Docling's cost per page if it makes Docling more than 5.5× faster than m7i-flex.large. At our volume, Docling on CPU costs $36–155 a year, which is the most a GPU could save. **Recommendation: CPU.** A GPU is worth testing only if latency becomes a requirement (for example, processing a filing within minutes of it appearing on EDGAR), or if volume grows by orders of magnitude.

### Concurrency and memory

Workers per machine = usable RAM ÷ peak RSS per worker, capped at the number of vCPUs. On m7i-flex.large we assume about 7 GiB (7,168 MiB) usable, leaving about 1 GiB for the operating system.

| Stage | Peak RSS MiB | Workers by RAM | Workers on m7i-flex.large (2 vCPU) |
|---|---|---|---|
| parse_pdfplumber | 513 | 13 | 2 |
| tables | 537 | 13 | 2 |
| layout | 1,055 | 6 | 2 |
| parse_docling | 3,597 | 1 | **1** |
| export | 105 | 68 | 2 |

- **Every stage fits in 8 GiB**, but **Docling only fits one worker**: two would need about 7.2 GiB at their peak, right at the limit.
- **Load each model once per worker** and keep the worker alive, so the 3–9 s model load is paid once per worker, not per page or per filing.
- **Parallelize by filing** in a process pool: each worker takes a whole filing through the pipeline, which keeps a filing's pages and the model in one process.
- At this volume one worker is enough: even Docling at p95 needs about 1,615 hours a year, which fits on one machine (8,760 hours in a year).

### EDGAR's 10 requests/second limit

`src/download.py` uses `sec-edgar-downloader` with `download_details=True`, which makes **2 requests per filing** (`full-submission.txt` and the primary document) plus a few lookups per company per run (the ticker-to-CIK map and the company's filing list). Counting **3 requests per filing** as a safe upper bound, at SEC's limit of 10 requests per second, **shared across all workers**, 5,000 filings need about 5,000 × 3 ÷ 10 = 1,500 s, or **25 minutes a year**. Download is not a bottleneck, but it needs:

- **one shared rate limiter** (for example a token bucket) for all workers, no matter how many process pages, and
- the declared User-Agent (team name + NEU email, from `params.yaml`) on every request, as the pipeline already does.

## 8. Limitations

- **One machine, a laptop.** The cost assumes m7i-flex.large is about as fast. That may not hold: the laptop has 12 threads and the instance has 2, and Docling and PyTorch use several threads, so the model stages are likely slower there. Re-running `bench.py` on the instance would replace this assumption with a measurement.
- **One filing's pages** as the batch (61 pages of FY2025). FY2024 has the same structure.
- **Forced OCR** stands in for a scanned filing; real scans may be noisier and slower.
- **Tesseract's memory is not measured.** Tesseract runs as a separate program; on Windows there is no `resource.getrusage(RUSAGE_CHILDREN)` to read the child process's peak memory.
- **The full-stage layout figure** comes from one pipeline run (`layout_log.csv`), not from two benchmark runs.
- **Docling's per-page time** is measured with one `convert()` call per page, which adds some per-call overhead compared with converting the whole PDF at once.
- **List prices on one date** (2026-10-08), on-demand, before any savings plans or spot pricing.
- **Engineering time is excluded** from every cost.

## 9. Reproduce

```bash
python src/bench.py --machine
python src/bench.py --stage parse_pdfplumber
python src/bench.py --stage ocr_tesseract
python src/bench.py --stage tables
python src/bench.py --stage layout --run 1
python src/bench.py --stage layout --run 2
python src/bench.py --stage parse_docling --run 1
python src/bench.py --stage parse_docling --run 2
python src/bench.py --stage export
python src/bench.py --summarize
```

Each stage CSV is appended to; delete it first for a clean re-run. Run each command in a fresh process so model loading is cold. Results are tracked with `dvc add data/bench` (a measurement, not a reproducible stage, since timings vary between runs).