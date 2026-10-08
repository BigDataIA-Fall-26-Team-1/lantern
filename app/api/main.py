"""Read-only API over LANTERN pipeline outputs. It runs no pipeline stages."""
import io
import json
from collections import Counter
from functools import lru_cache
from pathlib import Path

import pandas as pd
import pdfplumber
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, Response

ROOT = Path(__file__).resolve().parents[2]
EXPORT = ROOT / "data" / "export"
RENDERED = ROOT / "data" / "rendered"
REPORTS = ROOT / "reports"
REPORT_TYPES = {".md", ".csv", ".json", ".png"}

app = FastAPI(title="LANTERN API", version="1.0")


def _load_records():
    out = {}
    for p in sorted(EXPORT.glob("*.jsonl")):
        with open(p, encoding="utf-8") as f:
            out[p.stem] = [json.loads(line) for line in f if line.strip()]
    return out


RECORDS = _load_records()
MARKDOWN = {p.stem: p.read_text(encoding="utf-8") for p in sorted(EXPORT.glob("*.md"))}


def _records_for(stem):
    if stem not in RECORDS:
        raise HTTPException(404, f"unknown filing: {stem}")
    return RECORDS[stem]


@lru_cache(maxsize=None)
def _page_count(stem):
    pdf_path = RENDERED / f"{stem}.pdf"
    if not pdf_path.exists():
        return 0
    with pdfplumber.open(pdf_path) as pdf:
        return len(pdf.pages)


@lru_cache(maxsize=128)
def _page_png(stem, page, dpi):
    pdf_path = RENDERED / f"{stem}.pdf"
    if not pdf_path.exists():
        raise HTTPException(404, f"no rendered PDF for {stem}")
    with pdfplumber.open(pdf_path) as pdf:
        if not 1 <= page <= len(pdf.pages):
            raise HTTPException(404, "page out of range")
        img = pdf.pages[page - 1].to_image(resolution=dpi).original
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _snippet(hay, needle, width=80):
    i = hay.lower().find(needle)
    start = max(0, i - width)
    return ("..." if start else "") + hay[start:i + len(needle) + width] + "..."


@app.get("/health")
def health():
    return {"status": "ok", "filings": list(RECORDS)}


@app.get("/filings")
def filings():
    manifest = RENDERED / "manifest.csv"
    rows = (pd.read_csv(manifest, dtype=str).fillna("").to_dict("records")
            if manifest.exists() else [])
    by_stem = {r.get("stem"): r for r in rows}
    return [{
        "stem": stem,
        "manifest": by_stem.get(stem, {}),
        "n_pages": _page_count(stem),
        "n_records": len(recs),
        "block_types": dict(Counter(r.get("block_type") for r in recs)),
    } for stem, recs in RECORDS.items()]


@app.get("/filings/{stem}/pages/{page}/records")
def page_records(stem: str, page: int):
    return [r for r in _records_for(stem) if r.get("page") == page]


@app.get("/filings/{stem}/pages/{page}/image")
def page_image(stem: str, page: int, dpi: int = 100):
    dpi = max(50, min(dpi, 200))
    return Response(_page_png(stem, page, dpi), media_type="image/png")


@app.get("/filings/{stem}/tables")
def tables(stem: str):
    return [r for r in _records_for(stem)
            if r.get("block_type") == "Table" and r.get("table")]


@app.get("/filings/{stem}/records/{block_id}")
def trace(stem: str, block_id: str):
    rec = next((r for r in _records_for(stem) if r.get("block_id") == block_id), None)
    if rec is None:
        raise HTTPException(404, "record not found")
    comment = f"<!-- {rec['doc_id']} p{rec['page']} {block_id} -->"
    md, excerpt = MARKDOWN.get(stem, ""), None
    i = md.find(comment)
    if i != -1:
        rest = md[i + len(comment):].lstrip("\n")
        excerpt = comment + "\n\n" + rest.split("\n\n", 1)[0]
    return {"record": rec, "markdown": excerpt}


@app.get("/search")
def search(q: str, stem: str | None = None, limit: int = 50):
    needle = q.strip().lower()
    hits = []
    if not needle:
        return hits
    for s, recs in RECORDS.items():
        if stem and s != stem:
            continue
        for r in recs:
            hay = r.get("text") or ""
            if r.get("table"):
                hay += " " + json.dumps(r["table"], ensure_ascii=False)
            if needle in hay.lower():
                hits.append({"stem": s,
                             **{k: r.get(k) for k in
                                ("doc_id", "page", "block_id", "block_type", "section")},
                             "snippet": _snippet(hay, needle)})
                if len(hits) >= limit:
                    return hits
    return hits


@app.get("/reports")
def list_reports():
    if not REPORTS.exists():
        return []
    return sorted(str(p.relative_to(REPORTS).as_posix()) for p in REPORTS.rglob("*")
                  if p.is_file() and p.suffix.lower() in REPORT_TYPES)


@app.get("/reports/file")
def report_file(path: str):
    target = (REPORTS / path).resolve()
    if not target.is_relative_to(REPORTS.resolve()) or not target.is_file():
        raise HTTPException(404, "report not found")
    return FileResponse(target)
