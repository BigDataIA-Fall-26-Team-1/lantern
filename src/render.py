"""
Part 0 – Render SEC filing HTML to PDF with Playwright

Usage (DVC stage):
    python src/render.py

Standalone / CI:
    python src/render.py --input data/raw --output data/rendered

Reads params.yaml for render settings and download.ticker.
Writes one PDF per filing named {TICKER}_{FORM}_{PERIOD}.pdf
and a manifest.csv mapping stems to accession numbers.
"""

from __future__ import annotations

import argparse
import csv
import re
from pathlib import Path
from importlib.metadata import version

import yaml
from playwright.sync_api import sync_playwright


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def load_params(path: str = "params.yaml") -> dict:
    """Load params.yaml and return the full config."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def _parse_sec_header(header_text: str) -> dict[str, str]:
    """
    Extract key fields from the SEC SGML header block at the top of
    full-submission.txt.
    """
    fields: dict[str, str] = {}
    for line in header_text.splitlines():
        line = line.strip()
        match = re.match(r"^([A-Z][A-Z \-/]+?):\s+(.+)$", line)
        if match:
            key = match.group(1).strip().replace(" ", "-").upper()
            fields[key] = match.group(2).strip()
    return fields


def _extract_header_from_submission(filing_dir: Path) -> dict[str, str]:
    """Read the SEC header from full-submission.txt in the filing dir."""
    sub_file = filing_dir / "full-submission.txt"
    if not sub_file.exists():
        return {}

    with open(sub_file, encoding="utf-8", errors="replace") as f:
        header_lines = []
        for line in f:
            if "<DOCUMENT>" in line:
                break
            header_lines.append(line)

    return _parse_sec_header("".join(header_lines))


def _find_details_file(filing_dir: Path) -> Path | None:
    """
    Locate the HTML file to render.

    sec-edgar-downloader creates a details file:
    - v5+: primary-document.html
    - v4:  filing-details.html

    Falls back to the largest .htm in unpacked/ if neither exists.
    """
    # Check for downloader's details files first
    for name in ("primary-document.html", "filing-details.html"):
        candidate = filing_dir / name
        if candidate.exists():
            return candidate

    # Fallback: largest .htm in unpacked/
    unpacked = filing_dir / "unpacked"
    search_dirs = [unpacked, filing_dir] if unpacked.exists() else [filing_dir]

    for search_dir in search_dirs:
        htm_files = sorted(
            search_dir.glob("*.htm"),
            key=lambda p: p.stat().st_size,
            reverse=True,
        )
        if htm_files:
            # Filter out R-files (exhibit reports)
            primary = [
                f for f in htm_files
                if not re.match(r"^R\d+\.htm$", f.name, re.IGNORECASE)
            ]
            if primary:
                return primary[0]
            return htm_files[0]
    return None


def discover_filings(raw_dir: Path, ticker: str) -> list[Path]:
    """
    Find all filing directories under the sec-edgar-downloader layout:
    raw_dir/sec-edgar-filings/TICKER/FORM/ACCESSION/
    """
    base = raw_dir / "sec-edgar-filings" / ticker
    filing_dirs: list[Path] = []
    if base.exists():
        for form_dir in sorted(base.iterdir()):
            if form_dir.is_dir():
                for acc_dir in sorted(form_dir.iterdir()):
                    if acc_dir.is_dir():
                        filing_dirs.append(acc_dir)
    return filing_dirs


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render SEC HTML filings to PDF"
    )
    parser.add_argument(
        "--input", type=str, default="data/raw",
        help="Raw filings directory (default: data/raw)",
    )
    parser.add_argument(
        "--output", type=str, default="data/rendered",
        help="Output directory for PDFs (default: data/rendered)",
    )
    parser.add_argument("--params", type=str, default="params.yaml")
    args = parser.parse_args()

    params = load_params(args.params)
    ticker = params["download"]["ticker"]
    page_format = params.get("render", {}).get("page_format", "Letter")

    input_dir = Path(args.input)
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    filing_dirs = discover_filings(input_dir, ticker)
    if not filing_dirs:
        print(f"[ERROR] No filing directories found under {input_dir}")
        return

    print(f"[INFO] Found {len(filing_dirs)} filing(s) for {ticker}")

    manifest_rows: list[dict[str, str]] = []

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        renderer_version = f"playwright {version('playwright')} / chromium {browser.version}"
        page = browser.new_page()

        for fdir in filing_dirs:
            # Extract metadata from SEC header
            header = _extract_header_from_submission(fdir)
            accession = header.get("ACCESSION-NUMBER", fdir.name)
            cik = header.get("CENTRAL-INDEX-KEY", "")
            form_type = header.get("FORM-TYPE", "")
            period = header.get("CONFORMED-PERIOD-OF-REPORT", "")
            if not period or not form_type:
                raise ValueError(f"Missing period or form type in SEC header of {fdir}")
            company_name = header.get("COMPANY-CONFORMED-NAME", "")

            # Build stem: {TICKER}_{FORM}_{PERIOD}
            form_clean = form_type.replace("-", "")
            stem = f"{ticker}_{form_clean}_{period}"

            # Find the HTML to render
            html_path = _find_details_file(fdir)
            if html_path is None:
                print(f"[WARN] No HTML found in {fdir}, skipping")
                continue

            pdf_path = output_dir / f"{stem}.pdf"
            print(f"[INFO] Rendering {html_path.name} -> {pdf_path.name}")

            # Render HTML to PDF
            file_uri = html_path.resolve().as_uri()
            page.goto(file_uri, wait_until="networkidle", timeout=120_000)
            page.pdf(
                path=str(pdf_path),
                format=page_format,
                print_background=True,
            )

            manifest_rows.append({
                "stem": stem,
                "accession": accession,
                "cik": cik,
                "ticker": ticker,
                "form": form_type,
                "period": period,
                "company": company_name,
                "source_file": html_path.name,
                "renderer": "playwright-chromium",
                "renderer_version": renderer_version,
                "page_format": page_format,
            })

        browser.close()

    # Write manifest.csv
    manifest_path = output_dir / "manifest.csv"
    if manifest_rows:
        fieldnames = list(manifest_rows[0].keys())
        with open(manifest_path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(manifest_rows)
        print(f"[INFO] Wrote {manifest_path} with {len(manifest_rows)} row(s)")
    else:
        print("[WARN] No filings rendered — manifest.csv not written")

    print("[INFO] Render complete")


if __name__ == "__main__":
    main()