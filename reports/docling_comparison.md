# Part 4: Docling vs the traditional pipeline

Every number here comes from a file in the repo: `reports/metrics.json` (Part 9),
`reports/xbrl.md` and `data/xbrl/summary.json` (Part 11), `reports/benchmarks.md` and
`data/bench/` (Part 10), and `data/docling/timing.csv` (Part 4).

## 1. Setup

| Item | Value |
|---|---|
| Docling | 2.134.0 (docling-core 2.100.0, docling-ibm-models 4.0.3, docling-parse 7.22.2) |
| Pipeline options | `do_ocr: false`, TableFormer `accurate` (`params.yaml` → `docling`) |
| Inputs | both rendered 10-K PDFs (60 and 61 pages) and the iXBRL HTML of FY2024 |
| Traditional path | pdfplumber text (Part 1), Camelot/pdfplumber hybrid tables (Part 2), LayoutParser routing (Part 3) |
| Machine | Intel Core 7 150U, 12 threads, 15.7 GiB RAM, no GPU, Windows (see `reports/benchmarks.md` §1) |
| Bounding boxes | Docling writes BOTTOMLEFT boxes. `src/docling_parse.py` converts them with `bbox.to_top_left_origin(page_height)` into `{stem}.items.jsonl` (top-left, points) before any comparison. The lossless JSON is left untouched. |

## 2. Comparison table

| Dimension | Metric (source) | Traditional | Docling | Better |
|---|---|---|---|---|
| Text accuracy | WER, 16 GT pages (Part 9) | **1.67%** (pdfplumber) | 6.32% | Traditional |
| Text accuracy | CER, 16 GT pages (Part 9) | **1.54%** | 4.27% | Traditional |
| Numeric fidelity (text) | numeric-token recall (Part 9) | **99.48%** | 87.43% | Traditional |
| Table structure | cell precision / recall / F1, 2 tables, 111 cells (Part 9) | 1.00 / 1.00 / **1.00** | 1.00 / 1.00 / **1.00** | Tie |
| Numeric fidelity (statements) | XBRL match rate, 4 statements × 2 filings (Part 11) | **456 / 456 (100%)** | 450 / 450 (100%) | Traditional (coverage) |
| Reading order | WER on cover pages, 2 pages (Part 9) | 3.74% pdfplumber, 59.87% layout-routed | **6.90%** | See §3 |
| Footnotes | label counts in `items.jsonl` | no footnote label in Part 1/2 output | labelled separately (`footnote`, `caption`) | Docling |
| Provenance | page + bbox per record | per word (Part 1) and per block (Part 3) | per item, built in | Tie |
| Throughput | s/page p50 / p95 (Part 10) | **0.70 / 3.49** (full path) | 4.52 / 19.22 | Traditional |
| Memory | peak RSS (Part 10) | **1,055 MiB** (layout) | 3,597 MiB | Traditional |
| Cost | USD/year at 5,000 filings (Part 10) | **$5.63** (p50) | $36.35 (p50) | Traditional |

## 3. Findings per dimension

**Text accuracy.** pdfplumber reads the rendered PDF's text layer directly and has the
lowest error on every stratum. Docling's WER is highest on statement pages (9.87% vs
1.67%), because its per-page Markdown wraps statement rows in table syntax and splits or
merges cells. Docling's numeric recall (87.4%) is the clearest gap: numbers inside
statement tables go into Markdown table cells, and some are dropped or merged with
labels. Both paths were scored with the same normalizer (Markdown markup removed before
scoring, see `reports/eval.md`).

**Reading order.** Our rendered filings have no true multi-column prose pages, so the
best evidence is the cover page, which has side-by-side fields. Docling (6.90% WER) is
far better than our LayoutParser-routed text (59.87%), which reorders the cover's
blocks. Plain pdfplumber (3.74%) is still best, because the side-by-side fields happen
to line up row by row in the text layer.

**Table structure.** On the two ground-truth statement tables (111 cells), both paths
are perfect. Docling found row labels on its own; the Camelot path needed pdfplumber
position matching to recover them (Part 2).

**Numeric fidelity against XBRL.** Every number either path extracted matches XBRL. The
difference is coverage. Docling's cash flow tables lost the first row ("Cash, cash
equivalents, and restricted cash and cash equivalents, beginning balances") in both
filings: TableFormer merged it into the column header (`Years ended.September 27,
2025 $ 29,943` in `AAPL_10K_20250927_pdf_p0036_t00.csv`), so 6 numbers never became
data cells. The traditional path extracted that row.

**Footnotes.** Docling labels footnotes and captions as separate items, so they can be
kept out of body text. The traditional path has no footnote label; footnote text stays
in the page text and in table cells unless Part 2's normalizer strips the marker.

**Provenance.** Both paths can trace every record to a page and a bbox. Docling gives this
per item out of the box; the traditional path builds it per word (Part 1) and per block
(Part 3). After conversion both use the schema's top-left origin in points.

**Throughput.** Docling is about 6.5× slower than the whole traditional path at the median
(4.52 vs 0.70 s/page) and needs 3.4× the memory. Its time is concentrated on table-heavy
pages (statements and notes, pages 32–51), which is TableFormer on the CPU. Full-filing
runs in `data/docling/timing.csv` took 380–966 s per filing, depending on machine load.

## 4. PDF vs HTML (what rendering changed)

The same filing (FY2024) converted from the original iXBRL HTML and from the rendered PDF:

| | Rendered PDF | iXBRL HTML |
|---|---|---|
| Tables found | 51 | 63 |
| Conversion time | 380–966 s | 39–97 s |
| Pages / bbox | yes (60 pages) | none |

- **Tables:** HTML yields 12 more tables. In the PDF, some small tables are split across
  page breaks or read as text blocks; in HTML every `<table>` is a table.
- **Speed:** HTML skips the layout model and TableFormer, so it is about 10× faster.
- **Provenance:** HTML has no pages and no boxes, so it cannot support the page-and-bbox
  citations the brief requires. It is useful as a cross-check of table content, not as
  the source of truth.

Rendering therefore changes table boundaries, not table content: XBRL matches the PDF
tables at 100% on both paths.

## 5. Recommendation to Lina

Keep the **traditional pipeline (pdfplumber + Camelot/pdfplumber tables + LayoutParser
routing) as the primary path**: on our measured pages it has the lowest WER (1.7% vs 6.3%)
and the highest numeric recall (99.5% vs 87.4%), it matched XBRL on all 456 statement
numbers including a cash flow row Docling dropped, and it costs about 6× less compute
($5.63 vs $36.35 a year at 5,000 filings). Use **Docling as the fallback** where the
traditional path is weak: pages with side-by-side layout (Docling's cover-page WER is
6.9% vs 59.9% for our layout-routed text), footnote separation, and as an independent
second reading of statement tables, where the two paths agreed cell for cell (F1 1.0 on
both). Both paths are already DVC stages, so switching costs nothing; Docling should run
one worker per 8 GiB machine because of its 3.6 GiB peak memory.

## 6. Limitations

- Ground truth is 16 pages and 2 tables (Part 9); a small sample.
- No true multi-column prose page exists in our filings, so reading order is judged on
  the cover page only.
- Docling was run CPU-only; a GPU would change throughput but was not measured.
- HTML conversion was done for one filing (FY2024).
- Timing varied between runs on the same laptop (380–966 s per filing); Part 10's
  benchmark numbers are the controlled figures.