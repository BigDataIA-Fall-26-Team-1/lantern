# Test fixtures

Small files committed to Git (not DVC) so CI can run without EDGAR or the DVC remote.

**Page numbers** are 1-based pages in the rendered PDF, the same as the
`page` field in the record schema. Printed page numbers are shown in
brackets for reference only.

All AAPL fixtures come from `AAPL_10K_20250927.pdf` (accession
0000320193-25-000079), rendered with Playwright Chromium, Letter format.

| File | Source | How it was made |
|------|--------|-----------------|
| `statement.pdf` | AAPL 10-K FY2025, page 32 [printed 29]: Consolidated Statements of Operations | `pdfseparate -f 32 -l 32` |
| `scanned.pdf` | AAPL 10-K FY2025, pages 5–7 | `pdftoppm -r 300 -png`, then `img2pdf` into an image-only PDF |
| `multicol.pdf` | JPM 10-K FY2024, accession 0000019617-25-000270, page 10 [printed 8]: Human Capital section with side-by-side employee breakdown tables | Rendered with Playwright Chromium, then `pdfseparate -f 10 -l 10` |

## Multi-column fixture source

AAPL's 10-K filings have no multi-column pages, so this fixture comes from
another public SEC filing, as the brief allows (Section 4).

- Company: JPMorgan Chase & Co. (JPM)
- Form: 10-K
- Accession: 0000019617-25-000270
- Page: 10 (rendered PDF)
- EDGAR: https://www.sec.gov/Archives/edgar/data/19617/000001961725000270/

## Ground truth

`gt/` holds transcriptions of each fixture page and a CSV of the
statement table (added in Part 9).