# Part 7: Build vs buy, managed document AI

## Recommendation

**Keep the open-source pipeline as the primary path. Do not send every page to a managed service.** On our corpus, AWS Textract matched the open-source pipeline number for number, on every page we compared: the statements, the scanned fixture and the low-score tables. Its one measurable benefit was cleaner table structure (complete column headers, no caption text inside the table) on two tables whose numbers were already right.

Keep Textract as the **optional, cached fallback** it is now. It is off by default, and **costs nothing until it is turned on**. Turn it on only for pages the open-source path demonstrably gets wrong. On our corpus that set was empty. We recommend triggering it from a validation failure (an XBRL mismatch in Part 11, or a failed statement identity) rather than from Camelot's score alone, because a low Camelot score did not mean wrong numbers (see Evidence 3).

## What was sent, and how

| | |
|---|---|
| Service | AWS Textract, `AnalyzeDocument`, features `TABLES` + `LAYOUT`, region `us-east-1` |
| Pages sent | **7 of the 10 allowed**: FY2025 income statement (p32), balance sheet (p34), the two below-threshold tables (p22, p47), and scanned fixture pages 1–3 |
| Who sent them | Pranav, from his own AWS account, on 2026-10-07 (22:41–23:04 GMT, from AWS's response headers). The team account is on AWS's free plan, which does not include Textract |
| Stored where | `data/managed/`: the 7 raw responses, keyed by `sha256(source PDF hash, page, provider, API, features)`, tracked with `dvc add` (`data/managed.dvc`), imported into this format by `src/managed/import_responses.py` without new calls |
| Mapped into our schema | `src/managed/mapper.py`: every record passes `src/schema.py` (bbox converted from page fractions to PDF points, top-left; tables normalized by Part 2's normalizer) |
| As a fallback | `src/managed/fallback.py`, called from `src/export.py` (tables) and `src/parse_text.py` (OCR). With `managed.enabled: false` (the default) it reads cache hits and never calls the API; `dvc repro` runs without credentials. Every decision is logged (`data/export/managed_fallback_log.csv`, `data/parsed/managed_fallback_log.csv`) |

All numbers below come from `python -m src.managed.compare`, written to `reports/managed/` (`comparison.json`, `table_cells.csv`, `ocr_scores.csv`, `below_threshold.csv`, and the side-by-side CSVs).

## Evidence 1: statement tables (side by side, cell by cell)

Both tables normalized by the same Part 2 normalizer, using the same page text for the scale. A cell is (row label, which occurrence of the label, position among the row's numbers, value).

| Page | Part 2 numeric cells | Textract numeric cells | Identical | Gold checks on Textract's table |
|---|---|---|---|---|
| FY2025 p32, income statement | 57 | 57 | **57** | all 9 gold lines × 3 years, (321)/(565) negative, EPS unscaled, arithmetic: passed |
| FY2025 p34, balance sheet | 54 | 54 | **54** | all 8 accounting identities, totals as on the page: passed |

The open-source pipeline passed the same gold checks in Part 2. **No difference in any number.**

## Evidence 2: OCR on the scanned fixture

`tests/fixtures/scanned.pdf` is FY2025 pages 5–7 rasterized at 300 DPI (image only: 0 characters in its text layer), so the born-digital text of those pages is an exact reference. Tesseract ran with our Part 1 settings (`ocr.dpi`, `ocr.tesseract_config`).

| Scanned page (words) | Tesseract WER: raw / typography-normalized | Textract WER: raw / typography-normalized | Numbers correct | Self-reported confidence (Tesseract / Textract) |
|---|---|---|---|---|
| 1 (544) | 0.000 / **0.000** | 0.031 / **0.004** | both 100% | 95.4 / 99.1 |
| 2 (855) | 0.000 / **0.000** | 0.019 / **0.002** | both 100% | 95.7 / 99.8 |
| 3 (681) | 0.002 / **0.002** | 0.021 / **0.004** | both 100% | 95.5 / 99.7 |

"Typography-normalized" treats curly and straight quotes, dashes, and the ®/™/© signs as equal. Textract's raw gap was almost entirely typography (`company’s` → `company's`, dropped ® signs). Its remaining errors were a handful of real misreads, such as `sec-filings` → `sec-flings`. Tesseract's only difference was one quote mark.

Two findings:
- **Both engines are essentially perfect on this scan,** and every number was read correctly by both.
- **Self-reported confidence is not comparable across engines.** Textract reported higher confidence (99.1–99.8) than Tesseract (95.4–95.7), yet Tesseract was at least as accurate. Confidence thresholds have to be calibrated per engine.

## Evidence 3: the two tables the fallback replaced

These are the only Part 3 table blocks below `tables.accept_score` (80) whose page was sent to Textract; the fallback replaced Camelot's table with Textract's in the exported corpus (status `managed`). Checked against the page image, cell by cell:

| Page | Camelot (best attempt) | Textract | Values correct |
|---|---|---|---|
| p22, share repurchases | `camelot-network`, score 78.46. Headers split over 6 rows, and the top header lines were cut off ("Total Number of", "Approximate Dollar" missing); the "(1)" footnote mark became its own cell | complete one-row headers, 2 empty spacer rows | **11/11 both** |
| p47, term debt | `camelot-stream`, score 72.69. An extra first row holding part of the sentence above the table | no extra row; two-line headers merged ("Amount (in millions)"); "–"/"—" written as "-" | **21/21 both** |

**The fallback fixed structure, not numbers.** The low Camelot scores reflected fragmented headers and a caption caught inside the table box, not wrong values. Better headers do matter for Case Study 2 (a question about "average price paid per share" depends on the column being named), but a low Camelot score is not evidence that the numbers are wrong.

## Cost

Prices from AWS's Textract pricing page (checked 2026-10-07; page updated 2026-09-25). AWS's pricing example for financial reports states that **Layout is free when used with Tables**, and prices Tables at **$0.015 per page for the first 1 million pages in a month, and $0.010 per page after that** (quoted for US West, Oregon). OCR is included in `AnalyzeDocument` output. The free tier (three months, new AWS customers) covers 100 pages per month for Forms, Tables and Layout, and 1,000 pages per month for plain text detection.

| Volume | Pages | Textract, Tables + Layout |
|---|---|---|
| Our corpus (2 filings) | 121 | about **$1.82** |
| 5,000 filings a year, every page (our filings average 60.5 pages) | about 302,500 a year, about 25,000 a month | about **$4,540 a year** (all within the first-tier price) |
| 5,000 filings a year, fallback only | at most 20 of every 121 pages triggered on our corpus (16.5%), so at most about 50,000 a year | at most about **$750 a year** |
| The 7 pages actually sent | 7 | about $0.11, within the free tier |

The self-hosted cost per page (hardware, run time) is measured in Part 10 (`reports/benchmarks.md`); engineering time is not included in either path.

## Data handling: what we would need answered for client documents

From AWS's Textract FAQ (checked 2026-10-07):

| Question | What AWS states | What we would need before sending client documents |
|---|---|---|
| **Is our content used for training?** | Textract may store and use document inputs to provide the service and to improve Textract and other Amazon AI technologies | an **AI-services opt-out policy** in AWS Organizations (our team account is a standalone free-plan account, so this would have to be set up first) |
| **Where is it stored?** | encrypted at rest in the region where Textract is used; unless opted out, part of the content may be stored in another region for that improvement work | opt-out in place, and the region pinned to one the client accepts |
| **How long is it kept, and can it be deleted?** | deletion of stored inputs can be requested through AWS Support | a written retention period and deletion process for the client |
| **Who owns it?** | the customer keeps ownership | confirmed in the contract |
| **Compliance and network path** | HIPAA eligible; PCI, ISO and SOC compliant; callable through a VPC endpoint (PrivateLink) | which of these the client requires, and whether calls must avoid the public internet |
| **Access** | API calls are logged in CloudTrail | a least-privilege IAM role (only `AnalyzeDocument` / `DetectDocumentText`), keys never in the repo |

Public 10-K filings are low risk. The same pipeline used on client documents would not be.

## Limitations

- Seven pages, one company, one provider. The "scan" is a clean 300 DPI rasterization of a digital page: degraded scans (skewed, faded, photocopied), where a managed service is usually expected to help, were not tested.
- The two replaced tables were checked by eye against the page image; there is no gold transcription for them.
- `section` is not set on records mapped directly from Textract (`src/managed/mapper.py`); in the exported corpus, replaced tables keep the section of their Part 3 block.
- Prices are list prices from AWS's page on the date checked, and may change.

## Reproduce

```bash
dvc pull
python -m src.managed.mapper          # maps the 7 cached responses into schema records (no API calls)
python -m src.managed.compare         # writes reports/managed/ (no API calls; needs Tesseract)
```

Sources: AWS Textract pricing, https://aws.amazon.com/textract/pricing/ ; AWS Textract FAQs, https://aws.amazon.com/textract/faqs/ (both checked 2026-10-07).