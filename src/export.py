"""
Part 5 - Provenance-tagged export: one JSONL record per layout block, section
Markdown with provenance comments, and a plain-text baseline (Part 6).

Usage (DVC stage, from the repo root):
    python -m src.export

Inputs (all produced by earlier stages):
    data/layout/{stem}.blocks.jsonl   Part 3 blocks: type, bbox, reading order, routed text and tables
    data/parsed/{stem}_p{NNNN}.txt    Part 1 page text (used to read the table scale, as Part 2 does)
    data/rendered/manifest.csv        doc_id (accession), cik, ticker, form, company
    data/rendered/{stem}.pdf          source_path and sha256
    data/raw/.../unpacked/*.htm       dei:DocumentFiscalYearFocus / dei:DocumentFiscalPeriodFocus

Outputs:
    data/export/{stem}.jsonl          one validated record per block (src/schema.py)
    data/export/{stem}.md             sections in reading order, <!-- doc page block --> before every block
    data/export/{stem}.txt            plain text baseline, no structure, no provenance
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import pandas as pd
import yaml

from src.schema import SCHEMA_VERSION, record_keys, write_jsonl
from src.tables import normalize_df

# "Item 1.", "Item 1A.", "Item 7 -" ... at the start of a short heading line
ITEM = re.compile(r"^\s*item\s+(\d{1,2}[a-c]?)\s*[.:\u2013\u2014-]", re.IGNORECASE)
# a heading Part 3 split in two: an "Item"-only block next to "16. Form 10-K Summary"
ITEM_WORD = re.compile(r"^\s*item\s*$", re.IGNORECASE)
ITEM_REST = re.compile(r"^\s*(\d{1,2}[a-c]?)\s*[.:\u2013\u2014-]\s*\S", re.IGNORECASE)
TABLE_STATUSES_WITH_OBJECT = {"accepted", "best_below_threshold"}


# ---------------------------------------------------------------------------
# inputs
# ---------------------------------------------------------------------------

def load_params(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_manifest(path: Path) -> dict[str, dict]:
    with open(path, encoding="utf-8", newline="") as f:
        return {row["stem"]: row for row in csv.DictReader(f)}


def load_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def pkg(name: str) -> str:
    try:
        return f"{name} {version(name)}"
    except PackageNotFoundError:
        return f"{name} unknown"


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def dei_facts(raw_dir: Path, accession: str) -> tuple[int, str]:
    """Fiscal year and period from the filing's own iXBRL (dei facts)."""
    found: dict[str, str] = {}
    for htm in sorted(raw_dir.rglob(f"*{accession}*/unpacked/*.htm")):
        text = htm.read_text(encoding="utf-8", errors="replace")
        for name in ("DocumentFiscalYearFocus", "DocumentFiscalPeriodFocus"):
            if name in found:
                continue
            m = re.search(rf'<ix:nonNumeric[^>]*name="dei:{name}"[^>]*>(.*?)</ix:nonNumeric>',
                          text, re.DOTALL | re.IGNORECASE)
            if m:
                found[name] = re.sub(r"<[^>]+>", "", m.group(1)).strip()
        if len(found) == 2:
            break
    if len(found) < 2:
        raise ValueError(f"dei fiscal year/period not found for accession {accession} under {raw_dir}")
    return int(found["DocumentFiscalYearFocus"]), found["DocumentFiscalPeriodFocus"]


# ---------------------------------------------------------------------------
# tables
# ---------------------------------------------------------------------------

def py_value(v):
    """pandas/numpy cell -> str, float or None (JSON-safe)."""
    if v is None:
        return None
    if isinstance(v, str):
        return v if v.strip() else None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return str(v)
    return None if math.isnan(f) else f


def table_object(block: dict, page_text: str, tp: dict) -> dict | None:
    """Raw cells from the Part 3 block, normalized with Part 2's normalizer."""
    t = block.get("table") or {}
    if t.get("status") not in TABLE_STATUSES_WITH_OBJECT or not t.get("rows"):
        return None
    raw = [["" if c is None else str(c) for c in row] for row in t["rows"]]
    width = max(len(r) for r in raw)
    raw = [r + [""] * (width - len(r)) for r in raw]

    norm, scales = normalize_df(pd.DataFrame(raw), page_text, tp)
    kinds = list(norm["row_kind"]) if "row_kind" in norm.columns else None
    values = norm.drop(columns=["row_kind"]) if "row_kind" in norm.columns else norm
    rows = [[py_value(v) for v in row] for row in values.itertuples(index=False)]

    # column names from the header rows at the top (e.g. "September 27, 2025"): the leading
    # header rows with an EMPTY label cell. Label-only rows like "Net sales:" are not headers.
    top = 0
    while top < len(raw) and (kinds is None or kinds[top] == "header") and not raw[top][0].strip():
        top += 1
    columns = []
    for j in range(width):
        parts = [raw[i][j].strip() for i in range(top) if raw[i][j].strip()]
        columns.append(" ".join(parts) if parts else ("label" if j == 0 else f"col{j}"))

    money = scales.get("money_scale", scales.get("money", 1))
    shares = scales.get("shares_scale", scales.get("shares", 1))
    return {
        "columns": columns,
        "rows": rows,
        "raw_cells": raw,
        "scale": {"money": float(money), "shares": float(shares), "per_share": 1.0},
        "row_kinds": kinds,
        "method": t.get("method"),
        "status": t.get("status"),
        "source_csv": None,
    }


# ---------------------------------------------------------------------------
# records
# ---------------------------------------------------------------------------

def same_line(a: list[float], b: list[float]) -> bool:
    """True if two boxes share most of their vertical extent (same text line)."""
    overlap = min(a[3], b[3]) - max(a[1], b[1])
    return overlap > 0.5 * min(a[3] - a[1], b[3] - b[1])


def item_label(block: dict, max_chars: int, item_words: list[dict]) -> tuple[str, str, str | None] | None:
    """('Item 7', heading text, id of an 'Item' fragment block or None) if this block is an Item heading."""
    if block.get("type") not in ("Title", "Text"):
        return None                       # never from Table blocks (e.g. the table of contents)
    text = (block.get("text") or "").strip()
    if not text or len(text) > max_chars:
        return None
    m = ITEM.match(text)
    if m:
        return f"Item {m.group(1).upper()}", text.splitlines()[0].strip(), None
    m = ITEM_REST.match(text)
    if m:
        for w in item_words:              # "Item" on the same line, left of "16. ..."
            starts_first = w["bbox"][0] <= block["bbox"][0] + 5 and w["bbox"][2] < block["bbox"][2]
            if same_line(w["bbox"], block["bbox"]) and starts_first:
                return f"Item {m.group(1).upper()}", f"Item {text.splitlines()[0].strip()}", w["block_id"]
    return None


def build_records(stem, blocks, man, fiscal, pdf_rel, sha, parsed_dir, params):
    tp, ep = params["tables"], params["export"]
    fy, fp = fiscal
    versions = {k: pkg(k) for k in ("pdfplumber", "pytesseract", "camelot-py", "layoutparser")}
    page_text_cache: dict[int, str] = {}

    def page_text(n: int) -> str:
        if n not in page_text_cache:
            p = parsed_dir / f"{stem}_p{n:04d}.txt"
            page_text_cache[n] = p.read_text(encoding="utf-8") if p.exists() else ""
        return page_text_cache[n]

    item_words: dict[int, list[dict]] = {}
    for b in blocks:
        if b.get("type") in ("Title", "Text") and ITEM_WORD.match(b.get("text") or ""):
            item_words.setdefault(b["page"], []).append(b)

    records, headings, current_item, fragments = [], {}, None, {}
    for b in sorted(blocks, key=lambda x: (x["page"], x["order"])):
        if b.get("doc_id") and b["doc_id"] != man["accession"]:
            raise ValueError(f"{b['block_id']}: doc_id {b['doc_id']} != manifest accession {man['accession']}")
        hit = item_label(b, ep["item_heading_max_chars"], item_words.get(b["page"], []))
        if hit:
            current_item = hit[0]
            headings.setdefault(current_item, (hit[1], b["block_id"]))
            if hit[2]:
                fragments[hit[2]] = current_item
        section = current_item or b.get("section")

        btype = b["type"]
        text = b.get("text")
        table = table_object(b, page_text(b["page"]), tp) if btype == "Table" else None
        if btype in ("Text", "Title", "List", "Footnote") and text is None:
            text = ""
        ocr = bool(b.get("ocr"))
        if table is not None:
            extractor, ext_ver = table["method"] or "camelot", versions["camelot-py"]
        elif ocr:
            extractor, ext_ver = "tesseract", versions["pytesseract"]
        elif text:
            extractor, ext_ver = "pdfplumber", versions["pdfplumber"]
        else:
            extractor, ext_ver = "layoutparser", versions["layoutparser"]

        records.append({
            "schema": SCHEMA_VERSION,
            "doc_id": man["accession"],
            "company": man["company"],
            "cik": man["cik"],
            "ticker": man["ticker"],
            "form": man["form"],
            "fiscal_year": fy,
            "fiscal_period": fp,
            "page": b["page"],
            "section": section,
            "block_id": b["block_id"],
            "block_type": btype,
            "bbox": [round(float(v), 2) for v in b["bbox"]],
            "units": "pt",
            "origin": "top-left",
            "text": text,
            "table": table,
            "extractor": extractor,
            "extractor_version": ext_ver,
            "ocr": ocr,
            "ocr_conf": b.get("ocr_conf") if ocr else None,
            "source_path": pdf_rel,
            "sha256": sha,
            "detector": b.get("model"),
            "detector_score": b.get("score"),
            "figure_path": b.get("figure_path"),
        })
    for r in records:                      # an "Item" fragment belongs to the heading it completes
        if r["block_id"] in fragments:
            r["section"] = fragments[r["block_id"]]
    return records, headings, set(fragments)


# ---------------------------------------------------------------------------
# markdown + text
# ---------------------------------------------------------------------------

def md_cell(v) -> str:
    return str(v).replace("|", "\\|").replace("\n", " ").strip()


def md_table(raw: list[list[str]]) -> str:
    width = len(raw[0])
    lines = ["| " + " | ".join(md_cell(c) for c in raw[0]) + " |",
             "|" + "---|" * width]
    lines += ["| " + " | ".join(md_cell(c) for c in row) + " |" for row in raw[1:]]
    return "\n".join(lines)


def is_footer(rec: dict, pattern: re.Pattern, max_chars: int = 80) -> bool:
    """A whole block that is page furniture, e.g. 'Apple Inc. | 2025 Form 10-K | 29'.
    Only a single short line counts, so a content block that merely ENDS with a footer
    line is never dropped (that line is stripped by strip_footer_lines instead)."""
    t = (rec["text"] or "").strip()
    return (rec["table"] is None and bool(t) and "\n" not in t
            and len(t) <= max_chars and bool(pattern.search(t)))


def strip_footer_lines(text: str, line_pattern: re.Pattern) -> str:
    """Remove '... | 2025 Form 10-K | 29' lines merged into a bigger block (Markdown/TXT only;
    the JSONL keeps the block's full text). Bare numbers are content and stay."""
    return "\n".join(l for l in text.splitlines() if not line_pattern.search(l.strip()))


def to_markdown(records: list[dict], headings: dict, footer: re.Pattern, footer_line: re.Pattern,
                fragments: set = frozenset()) -> tuple[str, int]:
    first = records[0]
    out = [f"# {first['company']} {first['form']} FY{first['fiscal_year']} ({first['doc_id']})"]
    heading_ids = {bid for _, bid in headings.values()}
    current, skipped = object(), 0
    for r in records:
        if is_footer(r, footer):
            skipped += 1                       # page furniture: kept in the JSONL, left out here
            continue
        if r["block_id"] in fragments:         # the "Item" half of a split heading: already in the ## line
            continue
        src = f"<!-- {r['doc_id']} p{r['page']} {r['block_id']} -->"
        if r["section"] != current:
            current = r["section"]
            if r["block_id"] in heading_ids:   # the Item heading itself becomes the ## line
                out += [src, f"## {headings.get(r['section'], (r['text'].strip().splitlines()[0],))[0]}"]
                continue
            if current:
                out.append(f"## {headings.get(current, (current, None))[0]}")
        if r["block_id"] in heading_ids:
            out += [src, f"## {headings.get(r['section'], (r['text'].strip().splitlines()[0],))[0]}"]
        elif r["block_type"] == "Title":
            out += [src, f"### {r['text'].strip()}"]
        elif r["block_type"] == "Table" and r["table"]:
            out += [src, md_table(r["table"]["raw_cells"])]
        elif r["block_type"] == "Figure":
            fig = f"![Figure, page {r['page']}]({r['figure_path']})" if r["figure_path"] else "*[Figure]*"
            out += [src, fig] + ([r["text"].strip()] if r["text"] and r["text"].strip() else [])
        elif r["text"] and strip_footer_lines(r["text"], footer_line).strip():
            out += [src, strip_footer_lines(r["text"], footer_line).strip()]
    return "\n\n".join(out) + "\n", skipped


def to_text(records: list[dict], footer: re.Pattern, footer_line: re.Pattern,
            fragments: set = frozenset()) -> str:
    parts = []
    for r in records:
        if is_footer(r, footer) or r["block_id"] in fragments:
            continue
        if r["table"]:
            parts.append("\n".join(" ".join(c for c in row if c) for row in r["table"]["raw_cells"]))
        elif r["text"] and strip_footer_lines(r["text"], footer_line).strip():
            parts.append(strip_footer_lines(r["text"], footer_line).strip())
    return "\n\n".join(parts) + "\n"


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Provenance-tagged JSONL, section Markdown and TXT")
    ap.add_argument("--layout", default="data/layout")
    ap.add_argument("--parsed", default="data/parsed")
    ap.add_argument("--rendered", default="data/rendered")
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--output", default="data/export")
    ap.add_argument("--params", default="params.yaml")
    ap.add_argument("--manifest", default=None, help="default: <rendered>/manifest.csv")
    a = ap.parse_args()

    params = load_params(a.params)
    footer = re.compile(params["export"]["footer_pattern"])
    footer_line = re.compile(params["export"]["footer_line_pattern"])
    rendered, out_dir = Path(a.rendered), Path(a.output)
    manifest = load_manifest(Path(a.manifest) if a.manifest else rendered / "manifest.csv")
    out_dir.mkdir(parents=True, exist_ok=True)

    keys_seen = None
    for stem, man in sorted(manifest.items()):
        blocks_path = Path(a.layout) / f"{stem}.blocks.jsonl"
        if not blocks_path.exists():
            print(f"[WARN] {stem}: no {blocks_path}, skipped")
            continue
        blocks = load_jsonl(blocks_path)
        pdf_path = rendered / f"{stem}.pdf"
        pdf_rel = pdf_path.as_posix()
        fiscal = dei_facts(Path(a.raw), man["accession"])
        records, headings, fragments = build_records(stem, blocks, man, fiscal, pdf_rel,
                                                     sha256_of(pdf_path), Path(a.parsed), params)
        if len(records) != len(blocks):
            raise RuntimeError(f"{stem}: {len(records)} records for {len(blocks)} blocks")

        n = write_jsonl(records, out_dir / f"{stem}.jsonl")         # validates every record
        md, skipped = to_markdown(records, headings, footer, footer_line, fragments)
        (out_dir / f"{stem}.md").write_text(md, encoding="utf-8", newline="\n")
        (out_dir / f"{stem}.txt").write_text(to_text(records, footer, footer_line, fragments), encoding="utf-8", newline="\n")

        keys = record_keys()
        if keys_seen is not None and keys != keys_seen:
            raise RuntimeError("record keys differ between documents")
        keys_seen = keys
        n_tables = sum(1 for r in records if r["table"])
        print(f"[INFO] {stem}: FY{fiscal[0]} {fiscal[1]} | {n} records | {n_tables} table objects | "
              f"{len(headings)} Item sections | {skipped} footer blocks left out of the Markdown | "
              f"{len(fragments)} split Item heading(s) rejoined")
    print("[INFO] Export complete")


if __name__ == "__main__":
    main()