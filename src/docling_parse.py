"""Part 4 – Docling conversion and exports.

Converts every rendered PDF (and the original iXBRL HTML of one filing)
with Docling.  Exports Markdown, lossless JSON, per-table CSVs,
per-page Markdown, and a provenance JSONL with top-left bboxes.

Run as:  python -m src.docling_parse --input data/rendered --output data/docling --raw data/raw
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import pandas as pd
import yaml
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import (
    PdfPipelineOptions,
    TableFormerMode,
)
from docling.document_converter import DocumentConverter, PdfFormatOption

import docling

DOCLING_VERSION = docling.__version__


# ── helpers ────────────────────────────────────────────────────────

def load_manifest(manifest_path: Path) -> list[dict]:
    """Read manifest.csv for doc_id and HTML path lookup."""
    rows = []
    with manifest_path.open(encoding="utf-8", newline="\n") as f:
        for row in csv.DictReader(f):
            rows.append(row)
    return rows


def stem_to_doc_id(manifest: list[dict], stem: str) -> str:
    """Look up accession (doc_id) from stem; fall back to the stem."""
    for row in manifest:
        if row["stem"] == stem:
            return row["accession"]
    return stem


def build_converter(params: dict) -> DocumentConverter:
    """Create a Docling DocumentConverter from params.yaml settings."""
    do_ocr = params.get("do_ocr", False)
    table_mode_str = params.get("table_mode", "accurate")
    mode = (TableFormerMode.ACCURATE if table_mode_str == "accurate"
            else TableFormerMode.FAST)

    opts = PdfPipelineOptions(do_ocr=do_ocr, do_table_structure=True)
    opts.table_structure_options.mode = mode
    return DocumentConverter(
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=opts),
        }
    )


def write_items_jsonl(doc, stem: str, doc_id: str, out_dir: Path):
    """Write {stem}.items.jsonl with top-left bboxes.

    One JSON line per item: doc_id, stem, page, label, text,
    bbox [x0, top, x1, bottom], units "pt", origin "top-left",
    extractor "docling", extractor_version.
    """
    jsonl_path = out_dir / f"{stem}.items.jsonl"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as f:
        for item, _level in doc.iterate_items():
            if not item.prov:
                continue
            prov = item.prov[0]
            page_no = prov.page_no
            bbox = prov.bbox

            # Get page height for coordinate conversion
            page_height = doc.pages[page_no].size.height

            # Convert to top-left origin
            tl_bbox = bbox.to_top_left_origin(page_height)

            record = {
                "doc_id": doc_id,
                "stem": stem,
                "page": page_no,
                "label": item.label if isinstance(item.label, str) else item.label.value,
                "text": item.text if hasattr(item, "text") else "",
                "bbox": [round(tl_bbox.l, 2), round(tl_bbox.t, 2),
                         round(tl_bbox.r, 2), round(tl_bbox.b, 2)],
                "units": "pt",
                "origin": "top-left",
                "extractor": "docling",
                "extractor_version": DOCLING_VERSION,
            }
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def export_pdf(doc, stem: str, doc_id: str, out_dir: Path) -> dict:
    """Export a PDF conversion: Markdown, JSON, tables, per-page text, items."""
    # 1) Full Markdown
    md_path = out_dir / f"{stem}.md"
    md_path.write_text(doc.export_to_markdown(),
                       encoding="utf-8", newline="\n")

    # 2) Lossless JSON — saved UNTOUCHED (bboxes stay as Docling wrote them)
    json_path = out_dir / f"{stem}.json"
    doc_dict = doc.export_to_dict()
    with json_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(doc_dict, ensure_ascii=False, indent=2))

    # 3) Per-table CSVs — named by page of first prov entry
    n_tables = len(doc.tables)
    page_table_counts = {}  # track how many tables per page
    for table in doc.tables:
        if table.prov:
            pg = table.prov[0].page_no
        else:
            pg = 0
        k = page_table_counts.get(pg, 0)
        page_table_counts[pg] = k + 1

        df = table.export_to_dataframe(doc=doc)
        csv_path = out_dir / f"{stem}_pdf_p{pg:04d}_t{k:02d}.csv"
        df.to_csv(csv_path, index=False, lineterminator="\n")

    # 4) Per-page Markdown
    n_pages = len(doc.pages)
    for pg_no in range(1, n_pages + 1):
        page_md = doc.export_to_markdown(page_no=pg_no)
        pg_path = out_dir / f"{stem}_pdf_p{pg_no:04d}.md"
        pg_path.write_text(page_md, encoding="utf-8", newline="\n")

    # 5) Items JSONL with top-left bboxes
    write_items_jsonl(doc, stem, doc_id, out_dir)

    return {
        "stem": stem,
        "source": "pdf",
        "tables": n_tables,
        "pages": n_pages,
    }


def export_html(doc, stem: str, out_dir: Path) -> dict:
    """Export an HTML conversion: Markdown, JSON, tables (no pages, no bbox)."""
    html_dir = out_dir / "html"
    html_dir.mkdir(parents=True, exist_ok=True)

    # 1) Full Markdown
    md_path = html_dir / f"{stem}.md"
    md_path.write_text(doc.export_to_markdown(),
                       encoding="utf-8", newline="\n")

    # 2) Lossless JSON — untouched
    json_path = html_dir / f"{stem}.json"
    doc_dict = doc.export_to_dict()
    with json_path.open("w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(doc_dict, ensure_ascii=False, indent=2))

    # 3) Per-table CSVs (no page number — HTML has no pages)
    n_tables = len(doc.tables)
    for i, table in enumerate(doc.tables):
        df = table.export_to_dataframe(doc=doc)
        csv_path = html_dir / f"{stem}_t{i:03d}.csv"
        df.to_csv(csv_path, index=False, lineterminator="\n")

    return {
        "stem": stem,
        "source": "html",
        "tables": n_tables,
        "pages": 0,
    }


# ── main ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Docling PDF/HTML conversion")
    parser.add_argument("--input", required=True,
                        help="Path to data/rendered")
    parser.add_argument("--output", required=True,
                        help="Path to data/docling")
    parser.add_argument("--raw", default="data/raw",
                        help="Path to data/raw (for iXBRL HTML)")
    parser.add_argument("--params", default="params.yaml",
                        help="Path to params.yaml")
    parser.add_argument("--manifest", default=None,
                        help="Path to manifest.csv (default: <input>/manifest.csv)")
    args = parser.parse_args()

    rendered_dir = Path(args.input)
    out_dir = Path(args.output)
    raw_dir = Path(args.raw)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Load params
    with open(args.params, encoding="utf-8") as f:
        all_params = yaml.safe_load(f)
    docling_params = all_params.get("docling", {})

    # Load manifest
    manifest_path = Path(args.manifest) if args.manifest else rendered_dir / "manifest.csv"
    manifest = load_manifest(manifest_path)

    # Build converter (models load here — time this separately)
    print("Loading Docling models...")
    t_cold = time.perf_counter()
    conv = build_converter(docling_params)
    cold_start = time.perf_counter() - t_cold
    print(f"  Model loading: {cold_start:.1f}s")

    timings = []

    # ── Convert every rendered PDF ──────────────────────────────
    pdf_files = sorted(rendered_dir.glob("*.pdf"))
    if not pdf_files:
        print(f"ERROR: no PDFs found in {rendered_dir}")
        return

    for pdf_path in pdf_files:
        stem = pdf_path.stem
        doc_id = stem_to_doc_id(manifest, stem)
        print(f"\n{'='*60}")
        print(f"Converting PDF: {pdf_path.name}")
        t0 = time.perf_counter()

        result = conv.convert(str(pdf_path))
        doc = result.document

        summary = export_pdf(doc, stem, doc_id, out_dir)
        elapsed = time.perf_counter() - t0
        n_pages = summary["pages"]

        timings.append({
            "stem": stem,
            "pages": n_pages,
            "seconds": round(elapsed, 1),
            "s_per_page": round(elapsed / n_pages, 2) if n_pages else 0,
            "cold_start_s": round(cold_start, 1) if not timings else 0,
        })

        print(f"  {summary['tables']} tables, {n_pages} pages"
              f"  [{elapsed:.1f}s]")

    # ── Convert iXBRL HTML of one filing ────────────────────────
    html_stem = docling_params.get("html_stem")
    if html_stem:
        # Find the filing in manifest
        row = None
        for m in manifest:
            if m["stem"] == html_stem:
                row = m
                break
        if row is None:
            # Fallback to first filing
            row = manifest[0] if manifest else None

        if row:
            accession = row["accession"]
            ticker = row["ticker"]
            form = row["form"]
            unpacked = (raw_dir / "sec-edgar-filings" / ticker / form
                        / accession / "unpacked")

            # Find the primary .htm file
            htm_files = sorted(unpacked.glob("*.htm")) + sorted(unpacked.glob("*.html"))
            primary = None
            for h in htm_files:
                if h.stem.lower().startswith(ticker.lower()):
                    primary = h
                    break
            if primary is None and htm_files:
                primary = max(htm_files, key=lambda p: p.stat().st_size)

            if primary:
                print(f"\n{'='*60}")
                print(f"Converting HTML: {primary.name}")
                t0 = time.perf_counter()

                html_result = conv.convert(str(primary))
                html_doc = html_result.document

                summary = export_html(html_doc, html_stem, out_dir)
                elapsed = time.perf_counter() - t0

                timings.append({
                    "stem": html_stem,
                    "pages": 0,
                    "seconds": round(elapsed, 1),
                    "s_per_page": 0,
                    "cold_start_s": 0,
                })

                print(f"  {summary['tables']} tables  [{elapsed:.1f}s]")
            else:
                print(f"WARNING: no .htm/.html found in {unpacked}")

    # ── Write timing.csv ────────────────────────────────────────
    timing_path = out_dir / "timing.csv"
    timing_df = pd.DataFrame(timings)
    timing_df.to_csv(timing_path, index=False, lineterminator="\n")
    print(f"\n{'='*60}")
    print(f"Timing: {timing_path}")
    for t in timings:
        print(f"  {t['stem']}: {t['pages']} pages, {t['seconds']}s"
              f" ({t['s_per_page']} s/page)")


if __name__ == "__main__":
    main()