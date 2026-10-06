# Part 3: Layout detection audit

## 1. Setup

- **Model:** LayoutParser EfficientDet (`tf_efficientdet_d0`) trained on PubLayNet,
  weights from Hugging Face `layoutparser/efficientdet`.
- **Pages:** rendered at 150 DPI; boxes converted to PDF points, top-left origin.
- **Threshold:** `score_threshold: 0.25` (0.5 kept almost nothing: most correct blocks
  scored 0.28 to 0.45).
- **Audit sample:** 10 pages of `AAPL_10K_20250927` chosen to cover every page type.
  Overlays for these pages (both filings) are in `reports/layout/`.

| Page | Content |
|---|---|
| 1 | Cover page (form fields, checkboxes, securities table) |
| 3 | Table of contents |
| 5 | Item 1 Business: short headings and paragraphs |
| 20 | Item 1A to Item 2: risk factor, cybersecurity, properties |
| 24 | Item 7 MD&A: headings, bullet lists, paragraphs |
| 32 | Income statement |
| 34 | Balance sheet |
| 40 | Note 4: two stacked investment tables |
| 45 | Tax rollforward table, Note 8 leases, lease table |
| 54 | Auditor's report on internal control |

## 2. Scoring rules

For every real element on the page:

- **Correct:** a box of the right type covers the element.
- **Partial:** right type, but the box clips part of the element.
- **Missed:** no box of its own (recovered by the fallback, or absorbed into another box).
- **Wrong type:** boxed, but with the wrong class.

Page footers ("Apple Inc. | 2025 Form 10-K | 29") are not counted: PubLayNet has no
header/footer class.

## 3. Results per page

| Page | Text (C/P/M/W) | Title (C/M/W) | List (C/M) | Table (C/M) | Notes |
|---|---|---|---|---|---|
| 1 | 5 / 0 / 11 / 0 | 1 / 2 / 0 | 0 / 0 | 0 / 1 | Page-wide Table (0.25) and Figure (0.38); Nasdaq column labelled List; "FORM 10-K" and "Apple Inc." missed |
| 3 | 0 / 0 / 3 / 0 | 0 / 1 / 0 | | 1 / 0 | Whole TOC one Table (0.52), extracted 32x3; header lines absorbed |
| 5 | 5 / 1 / 3 / 0 | 7 / 2 / 0 | | | Two italic sub-headings and three short paragraphs missed |
| 20 | 3 / 1 / 2 / 1 | 4 / 1 / 0 | | | "None." labelled Title; italic risk-factor heading missed |
| 24 | 3 / 1 / 0 / 0 | 5 / 2 / 1 | 2 / 2 | | "Second Quarter 2025:" labelled Text; two of four bullet lists missed |
| 32 | 0 / 0 / 1 / 0 | 1 / 1 / 0 | | 1 / 0 | One tight Table over the whole statement; statement title missed |
| 34 | 0 / 0 / 1 / 0 | 2 / 0 / 0 | | 1 / 0 | One tight Table over the whole statement; caption missed |
| 40 | 1 / 1 / 0 / 0 | 1 / 1 / 0 | | 1 / 1 | 2025 and 2024 tables merged into one box |
| 45 | 2 / 1 / 4 / 0 | 2 / 0 / 0 | | 1 / 1 | Tax rollforward table missed; "Beginning balances" labelled Title |
| 54 | 7 / 0 / 3 / 0 | 3 / 1 / 0 | | | Report title, salutation and two paragraphs missed |

## 4. Totals

| Class | Elements | Correct | Partial | Missed | Wrong type | Detected (C + P) |
|---|---|---|---|---|---|---|
| Text | 60 | 26 | 5 | 28 | 1 | 52% |
| Title | 38 | 26 | 0 | 11 | 1 | 68% |
| List | 4 | 2 | 0 | 2 | 0 | 50% |
| Table | 8 | 5 | 0 | 3 | 0 | 63% |
| Figure | 0 | 0 | 0 | 0 | 0 | n/a |

**False positives:** page-wide Table and page-wide Figure on the cover; the cover's
Nasdaq column as List; "None." (p20) and the table row "Beginning balances" (p45) as Title.

## 5. Full-run numbers (both filings, every page)

| | FY2024 | FY2025 |
|---|---|---|
| Blocks in total (after cleanup and snapping) | 871 | 834 |
| Fallback blocks, before text snapping | 543 | 497 |
| Fallback blocks, after text snapping | 380 (-30%) | 348 (-30%) |
| Text share recovered only by fallback, before text snapping | 19.1% | 20.2% |
| Text share recovered only by fallback, after text snapping | 18.4% | 19.5% |
| Tables detected by the model | 29 | 31 |
| Tables extracted (accepted or best below threshold) | 23 (79%) | 24 (77%) |

Text snapping removed about 30% of fallback blocks: the one- or two-word fragments
cut off by clipped boxes, which broke sentences and reading order. The fallback share
of text fell by only 0.7 points, because most of what remains is whole paragraphs the
model never boxed. Snapping fixes fragmentation, not recall; the roughly 19% of text
that only the fallback recovers matches the 52% text recall measured in the audit.

Failed tables fall into three groups: non-tables boxed as Table (cover, page footers),
non-financial lists (exhibit index, signatures) rejected by the statement-style
structural checks, and one real financial table per filing lost (lease maturities).

## 7. What the pipeline does about it

| Problem | Handling in `src/layout.py` | Parameter |
|---|---|---|
| Missed text | Words in no block become `fallback` Text blocks, so no text is lost | `fallback_*` |
| Clipped table labels | Words on a table's rows near its side snap into the table | `table_snap_pt: 40` |
| Clipped paragraph words | Same for text blocks, with a smaller distance | `text_snap_pt: 15` |
| Duplicate boxes | Drop a box mostly covered by better boxes of the same type | `duplicate_cover_ratio: 0.6` |
| Row labels boxed inside a table | Drop a box mostly inside a table at least as confident | `inside_table_ratio: 0.8` |
| Page-wide low-confidence Table | Cannot delete more confident blocks inside it | (confidence rule) |
| Empty boxes over blank areas or images | Dropped; Tables there are OCR'd first | (empty-overlap rule) |
| Camelot returning a table from elsewhere | Result must lie inside the requested region | `table_region_overlap: 0.8` |

Table routing also helps directly: on the balance sheet, Camelot stream on the full page
(Part 2) stopped at "Total assets" (coverage 40%), but stream limited to the layout
region returned the whole statement (37x3, coverage 90%).

## 8. Known limitations

- **Missed tables become column-wise fallback.** The p45 tax rollforward came out as one
  block of row labels followed by one block per year column, so its reading order is
  column by column. Part 5 should not treat such blocks as running text.
- **Long clipped runs stay as fallback.** Snapping measures distance to the original
  box, so only words near the edge join; a box that covers half a line (p5, payment
  services) leaves the rest of the line as fallback.
- **Reading order** handles one or two columns; three or more columns are not tested.
- **Footers** are kept as fallback Text; PubLayNet has no footer class.

## 9. Recommendation

For 10-K filings, PubLayNet is reliable for finding financial statements but too weak
on body text and small note tables to be used on its own. Keep it for table routing,
and rely on the fallback for text coverage. The comparison with Docling in Part 4
should check whether a layout model trained on more varied documents closes the
text-recall gap.