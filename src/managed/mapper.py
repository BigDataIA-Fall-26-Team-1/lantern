"""
Part 7 - Map a cached Textract AnalyzeDocument response into the Part 5 record schema.

    map_response(response, page=..., page_size=(w, h), doc=..., source_path=..., sha256=...,
                 meta=..., page_text=None, tp=params["tables"]) -> list[dict]

- One record per Textract LAYOUT block (title, text, list, header, footer, figure, ...),
  in Textract's reading order. Its text is the block's LINE children.
- One Table record per Textract TABLE block, built from its CELLs (RowIndex/ColumnIndex) and
  normalized with Part 2's normalizer through src.export.table_object, exactly like the
  traditional path, so both are scored the same way. A LAYOUT_TABLE that overlaps a TABLE is
  represented by that Table record; one that overlaps none keeps its lines as Text.
- Without LAYOUT blocks (TABLES only), each LINE becomes a Text record.
- Boxes: Textract gives fractions of the page (top-left origin); converted to PDF points.
- ocr is true with the mean LINE confidence: Textract reads the page image, not the PDF text layer.

CLI (writes {out}/{source stem}_p{NNNN}.jsonl for every cached response, validated):
    python -m src.managed.mapper
"""

from __future__ import annotations

import argparse
from pathlib import Path

import yaml

from src.export import dei_facts, load_manifest, table_object
from src.managed import cache
from src.managed.blocks import children, mean, overlap_ratio, page_size_of, table_grid, to_points
from src.schema import SCHEMA_VERSION, write_jsonl

# Textract layout type -> (schema block_type)
LAYOUT_TYPES = {
    "LAYOUT_TITLE": "Title",
    "LAYOUT_SECTION_HEADER": "Title",
    "LAYOUT_TEXT": "Text",
    "LAYOUT_KEY_VALUE": "Text",
    "LAYOUT_LIST": "List",
    "LAYOUT_HEADER": "Text",
    "LAYOUT_FOOTER": "Text",
    "LAYOUT_PAGE_NUMBER": "Text",
    "LAYOUT_FIGURE": "Figure",
    "LAYOUT_TABLE": "Table",
}


def line_text(block: dict, by_id: dict) -> tuple[str, list[float]]:
    """Text of a LAYOUT block's LINE children, and their confidences."""
    lines = [c for c in children(block, by_id) if c["BlockType"] == "LINE"]
    return "\n".join(l.get("Text", "") for l in lines), [l["Confidence"] for l in lines if "Confidence" in l]


def map_response(response: dict, *, page: int, page_size: tuple[float, float], doc: dict,
                 source_path: str, sha256: str, meta: dict, tp: dict, page_text: str | None = None) -> list[dict]:
    blocks = response["Blocks"]
    by_id = {b["Id"]: b for b in blocks}
    all_lines = [b for b in blocks if b["BlockType"] == "LINE"]
    page_text = page_text if page_text is not None else "\n".join(l.get("Text", "") for l in all_lines)
    features = "+".join(cache.feature_list(meta.get("features", [])))
    extractor_version = f"{meta.get('api', 'AnalyzeDocument')} model {meta.get('model_version') or response.get('AnalyzeDocumentModelVersion')} ({features})"
    base = {
        "schema": SCHEMA_VERSION, "doc_id": doc["doc_id"], "company": doc["company"], "cik": doc["cik"],
        "ticker": doc["ticker"], "form": doc["form"], "fiscal_year": doc["fiscal_year"],
        "fiscal_period": doc["fiscal_period"], "page": page, "section": None,
    }
    tail = {"source_path": source_path, "sha256": sha256}

    tables = []
    for t in (b for b in blocks if b["BlockType"] == "TABLE"):
        grid = table_grid(t, by_id)
        if grid:
            tables.append((t, grid, to_points(t, page_size)))

    items = []          # (order, record-without-block_id)
    layouts = [b for b in blocks if b["BlockType"].startswith("LAYOUT_")]
    used_tables = set()
    for order, lb in enumerate(layouts):
        btype = LAYOUT_TYPES.get(lb["BlockType"], "Text")
        bbox = to_points(lb, page_size)
        if btype == "Table":
            match = max(range(len(tables)), key=lambda i: overlap_ratio(bbox, tables[i][2]), default=None)
            if match is not None and overlap_ratio(bbox, tables[match][2]) > 0.5 and match not in used_tables:
                used_tables.add(match)
                items.append((order, ("table", match)))
                continue
            if match is not None and overlap_ratio(bbox, tables[match][2]) > 0.5:
                continue            # a second layout box over a table already emitted
            btype = "Text"          # a table region with no TABLE block: keep its lines as text
        text, confs = line_text(lb, by_id)
        items.append((order, ("block", lb, btype, bbox, text, confs)))
    for i in range(len(tables)):     # TABLE blocks no layout box pointed at
        if i not in used_tables:
            items.append((len(layouts) + tables[i][2][1] / 1e4, ("table", i)))
    if not layouts:                  # TABLES-only response: one Text record per LINE
        for order, l in enumerate(all_lines):
            items.append((order, ("block", l, "Text", to_points(l, page_size), l.get("Text", ""),
                                  [l["Confidence"]] if "Confidence" in l else [])))

    records = []
    for n, (_, item) in enumerate(sorted(items, key=lambda x: x[0]), 1):
        if item[0] == "table":
            t, grid, bbox = tables[item[1]]
            tobj = table_object({"table": {"rows": grid, "status": "accepted", "method": "aws-textract"}},
                                page_text, tp)
            if tobj is not None:
                tobj["status"] = "managed"
            confs = [c["Confidence"] for c in children(t, by_id) if c["BlockType"] == "CELL" and "Confidence" in c]
            rec = {**base, "block_id": f"p{page:04d}_b{n:03d}", "block_type": "Table", "bbox": bbox,
                   "units": "pt", "origin": "top-left", "text": None, "table": tobj,
                   "extractor": "aws-textract", "extractor_version": extractor_version,
                   "ocr": True, "ocr_conf": mean(confs), **tail,
                   "detector": "aws-textract TABLE", "detector_score": round(t.get("Confidence", 0) / 100, 4),
                   "figure_path": None}
        else:
            _, lb, btype, bbox, text, confs = item
            rec = {**base, "block_id": f"p{page:04d}_b{n:03d}", "block_type": btype, "bbox": bbox,
                   "units": "pt", "origin": "top-left", "text": text, "table": None,
                   "extractor": "aws-textract", "extractor_version": extractor_version,
                   "ocr": True, "ocr_conf": mean(confs), **tail,
                   "detector": f"aws-textract {lb['BlockType']}",
                   "detector_score": round(lb.get("Confidence", 0) / 100, 4) if "Confidence" in lb else None,
                   "figure_path": None}
        records.append(rec)
    return records


def main() -> None:
    ap = argparse.ArgumentParser(description="Map cached Textract responses into schema records")
    ap.add_argument("--cache", default="data/managed")
    ap.add_argument("--out", default="data/managed_records")
    ap.add_argument("--params", default="params.yaml")
    ap.add_argument("--rendered", default="data/rendered")
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--fixture-doc", default="AAPL_10K_20250927",
                    help="filing the test fixtures were made from (their pages belong to its doc_id)")
    a = ap.parse_args()

    params = yaml.safe_load(open(a.params, encoding="utf-8"))
    manifest = load_manifest(Path(a.rendered) / "manifest.csv")
    fiscal_cache: dict[str, tuple[int, str]] = {}
    out = Path(a.out)
    entries = sorted(Path(a.cache).glob("*.json"))
    if not entries:
        raise SystemExit(f"no cached responses in {a.cache}")

    for p in entries:
        e = cache.read(a.cache, p.stem)
        meta, resp = e["_cache_meta"], e["response"]
        src = Path(meta["source"])
        stem = src.stem if src.stem in manifest else a.fixture_doc
        man = manifest[stem]
        if stem not in fiscal_cache:
            fiscal_cache[stem] = dei_facts(Path(a.raw), man["accession"])
        fy, fp = fiscal_cache[stem]
        doc = {"doc_id": man["accession"], "company": man["company"], "cik": man["cik"], "ticker": man["ticker"],
               "form": man["form"], "fiscal_year": fy, "fiscal_period": fp}
        recs = map_response(resp, page=meta["page"], page_size=page_size_of(src, meta["page"]), doc=doc,
                            source_path=src.as_posix(), sha256=meta["source_sha256"], meta=meta,
                            tp=params["tables"])
        dest = out / f"{src.stem}_p{meta['page']:04d}.jsonl"
        n = write_jsonl(recs, dest)
        tabs = [r for r in recs if r["table"]]
        kinds = {}
        for r in recs:
            kinds[r["block_type"]] = kinds.get(r["block_type"], 0) + 1
        shapes = [f"{len(r['table']['rows'])}x{len(r['table']['columns'])}" for r in tabs]
        print(f"{src.as_posix():<38} p{meta['page']:<3} {n:>3} records {kinds}  tables {shapes or '-'}  -> {dest}")


if __name__ == "__main__":
    main()