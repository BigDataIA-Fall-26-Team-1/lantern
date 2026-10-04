"""
Part 0 – Download SEC filings and unpack full-submission.txt

Usage (DVC stage):
    python src/download.py

Standalone / CI:
    python src/download.py --output data/raw

Reads download section of params.yaml for ticker, forms, date window,
and User-Agent.
"""

from __future__ import annotations

import argparse
import re
import shutil
from pathlib import Path

import yaml
from sec_edgar_downloader import Downloader


# Only keep these extensions when unpacking (skip images, binaries)
KEEP = {".htm", ".html", ".xml", ".xsd"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def load_params(path: str = "params.yaml") -> dict:
    """Load the download section of params.yaml."""
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["download"]


def _unpack_submission(submission_path: Path, dest: Path) -> Path:
    """
    Parse a full-submission.txt (SGML envelope) and write each embedded
    document to *dest* under its original FILENAME.

    Only keeps .htm, .html, .xml, .xsd files (Arelle needs these).
    Strips <XBRL> and <XML> wrapper tags so files are clean XML/HTML.
    """
    dest.mkdir(parents=True, exist_ok=True)
    raw = submission_path.read_text(encoding="utf-8", errors="replace")

    for doc in re.findall(r"<DOCUMENT>(.*?)</DOCUMENT>", raw, re.S):
        # Extract FILENAME
        name = re.search(r"<FILENAME>([^\n<]+)", doc)
        # Extract payload between <TEXT> and </TEXT>
        body = re.search(r"<TEXT>\n?(.*?)</TEXT>", doc, re.S)

        if not (name and body):
            continue

        fn = name.group(1).strip()

        # Skip images, PDFs, zip files, etc.
        if Path(fn).suffix.lower() not in KEEP:
            continue

        text = body.group(1)

        # XML docs sit inside <XBRL> or <XML> wrapper tags.
        # Strip those wrappers so Arelle gets clean XML.
        x = re.search(r"<(?:XBRL|XML)>\n?(.*?)</(?:XBRL|XML)>", text, re.S)
        content = x.group(1) if x else text

        out_file = dest / fn
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(content, encoding="utf-8", errors="replace", newline="\n")

    return dest


def download_filings(params: dict, output_dir: Path) -> list[Path]:
    """
    Download filings with sec-edgar-downloader and return a list of
    filing directories created under *output_dir*.
    """
    dl = Downloader(
        company_name=params["user_agent_name"],
        email_address=params["user_agent_email"],
        download_folder=str(output_dir),
    )

    ticker = params["ticker"]
    forms = params["forms"]
    if isinstance(forms, str):
        forms = [forms]

    for form in forms:
        dl.get(
            form,
            ticker,
            after=params["after"],
            before=params["before"],
            download_details=True,
        )

    # sec-edgar-downloader layout:
    #   output_dir/sec-edgar-filings/TICKER/FORM/ACCESSION/
    base = output_dir / "sec-edgar-filings" / ticker
    filing_dirs: list[Path] = []
    if base.exists():
        for form_dir in sorted(base.iterdir()):
            if form_dir.is_dir():
                for acc_dir in sorted(form_dir.iterdir()):
                    if acc_dir.is_dir():
                        filing_dirs.append(acc_dir)
    return filing_dirs


def unpack_all(filing_dirs: list[Path]) -> None:
    """
    For each downloaded filing directory, find full-submission.txt
    and unpack it into an unpacked/ subfolder.
    """
    for fdir in filing_dirs:
        sub_file = fdir / "full-submission.txt"
        if not sub_file.exists():
            print(f"[WARN] No full-submission.txt in {fdir}, skipping unpack")
            continue

        unpacked_dir = fdir / "unpacked"
        if unpacked_dir.exists():
            shutil.rmtree(unpacked_dir)

        print(f"[INFO] Unpacking {sub_file.name} -> {unpacked_dir}")
        dest = _unpack_submission(sub_file, unpacked_dir)

        # Sanity check
        xsd_files = list(dest.glob("*.xsd"))
        htm_files = list(dest.glob("*.htm"))
        print(f"       Found {len(xsd_files)} .xsd, {len(htm_files)} .htm files")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Download SEC filings")
    parser.add_argument(
        "--output", type=str, default="data/raw",
        help="Output directory for raw filings (default: data/raw)",
    )
    parser.add_argument(
        "--params", type=str, default="params.yaml",
        help="Path to params.yaml",
    )
    args = parser.parse_args()

    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    params = load_params(args.params)
    print(
        f"[INFO] Downloading {params['ticker']} filings "
        f"({params['after']} to {params['before']})"
    )

    filing_dirs = download_filings(params, output_dir)
    print(f"[INFO] Downloaded {len(filing_dirs)} filing(s)")

    if not filing_dirs:
        print("[ERROR] No filings downloaded. Check ticker/dates in params.yaml")
        return

    unpack_all(filing_dirs)
    print("[INFO] Download and unpack complete")


if __name__ == "__main__":
    main()