# Part 4: Docling vs the traditional pipeline

Every number here comes from a file in the repo: `reports/metrics.json` and `reports/eval.md`
(Part 9), `reports/xbrl.md` (Part 11), `reports/benchmarks.md` and `data/bench/` (Part 10),
and `data/docling/timing.csv` and `data/docling/*.items.jsonl` (Part 4).

## 1. Setup

| Item | Value |
|---|---|
| Docling | 2.134.0 (docling-core 2.100.0, docling-ibm-models 4.0.3, docling-parse 7.22.2) |
| Pipeline options | `do_ocr: false`, TableFormer `accurate` (`params.yaml` → `docling`) |
| Inputs | both rendered 10-K PDFs (60 and 61 pages) and the iXBRL HTML of FY2024 |
| Traditional path | pdfplumber text with Tesseract OCR fallback (Part 1), Camelot/pdfplumber hybrid tables (Part 2), LayoutParser routing (Part 3) |
| Machine | Intel Core 7 150U, 12 threads, 15.7 GiB RAM, no GPU, Windows (`reports/benchmarks.md` §1) |
| Bounding boxes | Docling writes BOTTOMLEFT boxes. The lossless JSON (`{stem}.json`) is kept exactly as Docling wrote it. `{stem}.items.jsonl` holds one record per item with boxes converted by `bbox.to_top_left_origin(page_height)` to the schema's top-left origin in points; every comparison uses this file. |

## 2. Comparison table

| Dimension | Metric (source) | Traditional | Docling | Better |
|---|---|---|---|---|
| Text accuracy | WER, 18 GT pages (Part 9) | **1.91%** (pdfplumber) | 11.71% | Traditional |
| Text accuracy | CER, 18 GT pages (Part 9) | **1.76%** | 9.72% | Traditional |
| Numeric fidelity (text) | numeric-token recall (Part 9) | **99.54%** | 83.18% | Traditional |
| Table structure | cell P / R / F1, 2 tables, 111 cells (Part 9) | 1.00 / 1.00 / **1.00** | 1.00 / 1.00 / **1.00** | Tie |
| Numeric fidelity (statements) | XBRL match rate, 4 statements × 2 filings (Part 11) | **456 / 456 (100%)** | 450 / 452 (99.6%) | Traditional |
| Reading order | WER, multi-column page (Part 9) | 7.62% pdfplumber, **3.15%** layout-routed | 9.60% | Traditional (layout) |
| Reading order | WER, cover pages, 2 pages (Part 9) | **3.74%** pdfplumber, 59.87% layout-routed | 6.90% | pdfplumber; Docling beats layout routing |
| Scanned pages | WER, scanned fixture page (Part 9) | **0.00%** pdfplumber + OCR | 100% (no text, `do_ocr: false`) | Traditional |
| Footnotes | label counts in `data/export/*.jsonl` and `data/docling/*.items.jsonl`, plus a manual check of the PDFs | 0 `Footnote` blocks | 0 `footnote` / `caption` items | N/A: our filings have no footnotes |
| Provenance | page + bbox per record | per word (Part 1) and per block (Part 3) | per item, built in | Tie |
| Throughput | s/page p50 / p95, full path (Part 10) | **0.70 / 3.49** | 4.52 / 19.22 | Traditional |
| Memory | peak RSS (Part 10) | **1,055 MiB** (layout stage) | 3,597 MiB | Traditional |
| Cost | USD/year at 5,000 filings, p50 (Part 10) | **$5.63** | $36.35 | Traditional |

## 3. Findings per dimension

**Text accuracy.** pdfplumber reads the rendered PDF's text layer directly and has the
lowest overall error (1.91% WER). Docling's 11.71% has two measured sources: the scanned
page, where Docling produced no text at all (100% WER), and statement pages (9.87% vs
1.67%), where its per-page Markdown wraps rows in table syntax. On prose and notes the
gap is small (prose 4.05% vs 0.11%, notes 2.98% vs 2.20%). Both paths were scored with
the same normalizer, which removes Markdown markup (`reports/eval.md`).

**Scanned pages.** We run Docling with `do_ocr: false`, because no rendered EDGAR page
needs OCR (Part 1: 0 of 121 pages triggered). On the image-only fixture this means
Docling returns nothing, while the traditional path's OCR trigger fires and Tesseract
recovers the text (0% WER). Turning on Docling's OCR would fix this but was not measured.

**Reading order.** On the multi-column page, LayoutParser routing reads the columns in
the right order (3.15% WER), better than plain pdfplumber (7.62%) and Docling (9.60%). On
the cover page, with its side-by-side fields, the result reverses: Docling (6.90%) is far
better than layout routing (59.87%), which reorders the blocks, and plain pdfplumber
(3.74%) is best because the fields line up row by row in the text layer. Each of these
strata has only 1–2 pages, so these are indications, not general results.

**Table structure.** On the two ground-truth statement tables (111 cells), both paths are
perfect (F1 1.00). Docling found row labels on its own; the Camelot path needed
pdfplumber position matching to recover them (Part 2).

**Numeric fidelity against XBRL.** Every number either path extracted matches XBRL; there
are no mismatches, sign errors or scale errors on either path. The difference is coverage.
Docling's cash flow tables lost the first row ("Cash, cash equivalents, and restricted
cash and cash equivalents, beginning balances") in both filings: TableFormer merged it
into the column header (`Years ended.September 27, 2025 $ 29,943` in
`AAPL_10K_20250927_pdf_p0036_t00.csv`). That is 6 printed numbers but 2 distinct XBRL
facts, because each year's beginning cash is the previous year's ending cash, which
Docling did capture. Only the oldest beginning balance in each filing is missing, hence
450 / 452. The traditional path extracted the row in both filings.

**Footnotes.** Our filings have no footnotes (manual check of the rendered PDFs), so this
dimension cannot separate the two paths. Both report zero: the traditional export has
0 `Footnote` blocks, although the schema supports that type, and Docling's `items.jsonl`
has 0 `footnote` and 0 `caption` items in both filings. Testing it would need a filing
that has footnotes.

**Provenance.** Both paths trace every record to a page and a bbox. Docling gives this per
item out of the box; the traditional path builds it per word (Part 1) and per block
(Part 3). After conversion both use the schema's top-left origin in points.

**Throughput and memory.** Docling is about 6.5× slower than the whole traditional path at
the median (4.52 vs 0.70 s/page) and its peak memory is 3.4× the layout stage's. Its time
is concentrated on table-heavy pages (statements and notes, pages 32–51), which is
TableFormer on the CPU. Full-filing runs in `data/docling/timing.csv` took 965.9 s
(FY2024) and 652.5 s (FY2025).

## 4. PDF vs HTML (what rendering changed)

The same filing (FY2024), converted from the original iXBRL HTML and from the rendered PDF
in the same run (`data/docling/timing.csv`):

| | Rendered PDF | iXBRL HTML |
|---|---|---|
| Tables found | 51 | 63 |
| Conversion time | 965.9 s | 96.8 s |
| Pages / bbox | yes (60 pages) | none |

- **Tables:** the HTML yields 12 more tables. A likely cause is that in the PDF some
  small tables are split across page breaks or read as text blocks, while in HTML every
  `<table>` element is a table; we did not match the tables one by one.
- **Speed:** HTML conversion was 10× faster, because it skips the layout model and
  TableFormer.
- **Provenance:** HTML has no pages and no boxes, so it cannot support the page-and-bbox
  citations the brief requires. It is useful as a cross-check of table content, not as
  the source of truth.

## 5. Recommendation to Lina

Keep the **traditional pipeline as the primary path**: on 18 ground-truth pages it has
the lowest WER (1.9% vs 11.7%) and the highest numeric recall (99.5% vs 83.2%), it matched
XBRL on all 456 statement numbers, including the cash flow beginning-balance row that
Docling dropped (Docling 450 / 452), its OCR fallback handles scanned pages that Docling
with `do_ocr: false` returns empty, and it costs about 6× less compute ($5.63 vs $36.35 a
year at 5,000 filings). Use **Docling as the fallback and cross-check**: as an independent
second reading of statement tables, where both paths agreed cell for cell (F1 1.00 on
111 cells), and for pages with side-by-side fields like the cover, where Docling (6.9%
WER) is far better than our layout routing (59.9%). Both paths are already DVC stages, so
either can be switched on; Docling should run one worker per 8 GiB machine because of its
3.6 GiB peak memory.

## 6. Limitations

- Ground truth is 18 pages and 2 tables (Part 9); the multi-column, scanned and cover
  strata have only 1–2 pages each.
- Our filings have no footnotes, so footnote handling could not be compared.
- Docling was run with `do_ocr: false` and CPU-only; its OCR mode and GPU throughput were
  not measured.
- HTML conversion was done for one filing (FY2024), and its tables were counted, not
  matched one by one.
- Full-filing Docling timings in `timing.csv` are single runs on a laptop under varying
  load; Part 10's benchmark numbers are the controlled figures.