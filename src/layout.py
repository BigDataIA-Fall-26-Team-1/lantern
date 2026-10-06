"""
Part 3 - Layout detection and routing with LayoutParser (EfficientDet, PubLayNet)

Usage (DVC stage):
    python src/layout.py

Quick test on a few pages, with QA overlays:
    python src/layout.py --input data/rendered/AAPL_10K_20250927.pdf --pages 1,32-34 \
        --output /tmp/layout --figures /tmp/figures --overlays /tmp/overlays

Outputs:
    {output}/{stem}.blocks.jsonl   one record per block, in reading order
    {output}/layout_log.csv        one row per page: what the model found and what was kept
    {figures}/{stem}_p{NNNN}_b{NNN}.png   cropped Figure blocks
    {overlays}/{stem}_p{NNNN}.png  optional QA images (boxes drawn on the page)

Every bbox is in PDF points, top-left origin, like Parts 1 and 2.

LayoutParser has not been updated since 2022. Fixes applied here:
  - weights from Hugging Face (the built-in Dropbox link is dead)
  - torch.load defaults to weights_only=True since PyTorch 2.6; this checkpoint needs False
  - the model is built directly with EfficientDetLayoutModel (AutoLayoutModel re-triggers the dead link)
  - the label map comes from LayoutParser's own catalog. PubLayNet classes are numbered 1-5
    for EfficientDet; a 0-based map shifts every label (Text read as Title, Table as Figure)
  - overlays are drawn with PIL directly (layoutparser.draw_box uses a Pillow API removed in 10.0)
"""

from __future__ import annotations

import argparse
import csv
import json
import time
from pathlib import Path

import numpy as np
import pdfplumber
import yaml
from PIL import ImageDraw

from tables import (NUM_TOKEN, bbox_top_left, clean_df, count_ruling_lines,
                    load_manifest, score_table)

TEXT_TYPES = {"Text", "Title", "List"}
COLORS = {"Text": (31, 119, 180), "Title": (214, 39, 40), "List": (44, 160, 44),
          "Table": (255, 127, 14), "Figure": (148, 103, 189), "Fallback": (127, 127, 127)}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def load_params(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def find_pdfs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(input_path.glob("*.pdf"))


def parse_pages(spec: str | None) -> set[int] | None:
    """'1,32-34' -> {1, 32, 33, 34}. None means every page."""
    if not spec:
        return None
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            pages.update(range(int(a), int(b) + 1))
        elif part:
            pages.add(int(part))
    return pages


def area(b: list[float]) -> float:
    return max(0.0, b[2] - b[0]) * max(0.0, b[3] - b[1])


def intersection(a: list[float], b: list[float]) -> float:
    return area([max(a[0], b[0]), max(a[1], b[1]), min(a[2], b[2]), min(a[3], b[3])])


def contains_point(b: list[float], x: float, y: float) -> bool:
    return b[0] <= x <= b[2] and b[1] <= y <= b[3]


def clip(b: list[float], w: float, h: float) -> list[float]:
    return [max(0.0, b[0]), max(0.0, b[1]), min(w, b[2]), min(h, b[3])]


# ---------------------------------------------------------------------------
# 1. model
# ---------------------------------------------------------------------------

def load_model(lp_params: dict):
    """Build the PubLayNet EfficientDet model with the fixes listed at the top."""
    import torch
    _orig_load = torch.load
    torch.load = lambda *a, **k: _orig_load(*a, **{**k, "weights_only": False})

    import layoutparser as lp
    from huggingface_hub import hf_hub_download
    from layoutparser.models.effdet.catalog import LABEL_MAP_CATALOG

    weights = hf_hub_download(lp_params["hf_repo"], lp_params["hf_file"])
    return lp.models.effdet.layoutmodel.EfficientDetLayoutModel(
        lp_params["model_name"], model_path=weights,
        label_map=LABEL_MAP_CATALOG["PubLayNet"])


def detect(model, page, dpi: int) -> tuple[list[dict], object]:
    """Run the model on one page. Boxes are converted from pixels to points."""
    pil = page.to_image(resolution=dpi).original.convert("RGB")
    k = 72.0 / dpi
    blocks = []
    for b in model.detect(np.array(pil)):
        x0, y0, x1, y1 = b.coordinates
        blocks.append({"type": str(b.type), "score": round(float(b.score), 3),
                       "bbox": clip([x0 * k, y0 * k, x1 * k, y1 * k],
                                    float(page.width), float(page.height)),
                       "source": "model"})
    return blocks, pil


# ---------------------------------------------------------------------------
# 2. clean up the model's boxes
# ---------------------------------------------------------------------------

def filter_blocks(blocks: list[dict], lp_params: dict) -> tuple[list[dict], dict]:
    """
    1. drop boxes below score_threshold
    2. drop a box mostly covered by higher-scoring boxes of the same type
       (e.g. one wide 'Table' spanning two tables that were already found)
    3. drop non-table boxes that sit mostly inside a kept Table that is at least
       as confident (row labels the model boxed separately). A low-confidence
       'Table' covering a whole page must not delete confident text blocks.
    """
    stats = {"raw": len(blocks)}
    kept_score = [b for b in blocks if b["score"] >= lp_params["score_threshold"]]
    stats["below_threshold"] = len(blocks) - len(kept_score)

    kept: list[dict] = []
    for b in sorted(kept_score, key=lambda b: -b["score"]):
        a = area(b["bbox"]) or 1.0
        covered = sum(intersection(b["bbox"], k["bbox"]) for k in kept if k["type"] == b["type"])
        if min(covered, a) / a >= lp_params["duplicate_cover_ratio"]:
            continue
        kept.append(b)
    stats["duplicates"] = len(kept_score) - len(kept)

    tables = [b for b in kept if b["type"] == "Table"]
    final = []
    for b in kept:
        if b["type"] != "Table":
            a = area(b["bbox"]) or 1.0
            inside = sum(intersection(b["bbox"], t["bbox"]) for t in tables
                         if t["score"] >= b["score"])
            if min(inside, a) / a >= lp_params["inside_table_ratio"]:
                continue
        final.append(b)
    stats["inside_table"] = len(kept) - len(final)
    return final, stats


# ---------------------------------------------------------------------------
# 3. assign words to blocks, and catch text the model missed
# ---------------------------------------------------------------------------

def assign_words(words: list[dict], blocks: list[dict]) -> list[dict]:
    """
    Each word goes to ONE block: the smallest block containing its centre.
    This avoids duplicated text where boxes overlap and avoids clipped letters
    where a box cuts through a word. Returns the words no block claimed.
    """
    for b in blocks:
        b["words"] = []
    leftover = []
    for w in words:
        cx, cy = (w["x0"] + w["x1"]) / 2, (w["top"] + w["bottom"]) / 2
        owners = [b for b in blocks if contains_point(b["bbox"], cx, cy)]
        if owners:
            min(owners, key=lambda b: area(b["bbox"]))["words"].append(w)
        else:
            leftover.append(w)
    return leftover


def snap_to_blocks(leftover: list[dict], blocks: list[dict], types: set[str],
                   snap_pt: float) -> list[dict]:
    """
    The model's boxes often stop a few points short of the text: table boxes
    miss the row labels on the left, paragraph boxes clip the first or last word
    of a line. A leftover word whose vertical centre is inside a block's rows and
    which sits within snap_pt of the block's side is given to that block, and the
    box grows to include it. Without this, clipped words became separate fallback
    blocks and broke sentences ('... the related notes' read out of order).
    Returns the words still unclaimed.
    """
    targets = [b for b in blocks if b["type"] in types]
    # distances are measured to the ORIGINAL boxes, so one snapped word cannot
    # widen a box and let the next word, further away, snap in as well
    original = {id(t): list(t["bbox"]) for t in targets}
    remaining = []
    for w in leftover:
        cy = (w["top"] + w["bottom"]) / 2
        best = None
        for t in targets:
            x0, top, x1, bottom = original[id(t)]
            if not top <= cy <= bottom:
                continue
            dist = x0 - w["x1"] if w["x1"] <= x0 else (w["x0"] - x1 if w["x0"] >= x1 else 0.0)
            if dist <= snap_pt and (best is None or dist < best[0]):
                best = (dist, t)
        if best:
            t = best[1]
            t["words"].append(w)
            t["bbox"] = [min(t["bbox"][0], w["x0"]), t["bbox"][1],
                         max(t["bbox"][2], w["x1"]), t["bbox"][3]]
        else:
            remaining.append(w)
    return remaining


def words_to_lines(words: list[dict], y_tol: float, word_gap: float) -> list[list[dict]]:
    """Group words into lines; a big horizontal gap starts a new line (another column)."""
    lines: list[list[dict]] = []
    for w in sorted(words, key=lambda w: (round(w["top"]), w["x0"])):
        for line in lines:
            last = line[-1]
            if abs(last["top"] - w["top"]) <= y_tol and 0 <= w["x0"] - last["x1"] <= word_gap:
                line.append(w)
                break
        else:
            lines.append([w])
    return lines


def lines_text(words: list[dict], y_tol: float, word_gap: float) -> str:
    lines = words_to_lines(words, y_tol, word_gap)
    lines.sort(key=lambda ln: (ln[0]["top"], ln[0]["x0"]))
    return "\n".join(" ".join(w["text"] for w in ln) for ln in lines)


def drop_empty_overlaps(blocks: list[dict], page, lp_params: dict,
                        ocr_dpi: int) -> tuple[list[dict], int]:
    """
    Blocks that received no words:
      - the PDF has text there: every word went to smaller overlapping blocks,
        so the block is a duplicate and is dropped. (OCR over real text produced
        garbage on the cover page.)
      - a Table over a region with no text layer at all (a blank area, shaded
        bands, or an image that was not downloaded): Camelot cannot read it and
        raised errors, so the region is OCR'd instead; if OCR finds nothing,
        the block is dropped.
    Text-type blocks with no text layer are kept and OCR'd later (scanned pages).
    """
    kept = []
    for b in blocks:
        if b["words"] or b["type"] not in TEXT_TYPES | {"Table"}:
            kept.append(b)
            continue
        box = clip(b["bbox"], float(page.width), float(page.height))
        if page.crop(box).chars:
            continue
        if b["type"] == "Table":
            text, conf = ocr_region(page, box, ocr_dpi, lp_params["tesseract_config"])
            if len(text.strip()) < lp_params["fallback_min_chars"]:
                continue
            b["ocr_result"] = (text, conf)
        kept.append(b)
    return kept, len(blocks) - len(kept)


def fallback_blocks(leftover: list[dict], lp_params: dict) -> list[dict]:
    """
    Words no detected block covered (missed paragraphs, footnotes, headers)
    become 'Text' blocks with source='fallback', so nothing on the page is lost.
    Consecutive lines merge into one block when they are close and overlap horizontally.
    """
    lines = words_to_lines(leftover, lp_params["fallback_y_tol_pt"], lp_params["fallback_word_gap_pt"])
    lines.sort(key=lambda ln: (min(w["top"] for w in ln), ln[0]["x0"]))
    groups: list[dict] = []
    for ln in lines:
        box = [min(w["x0"] for w in ln), min(w["top"] for w in ln),
               max(w["x1"] for w in ln), max(w["bottom"] for w in ln)]
        for g in groups:
            gb = g["bbox"]
            close = 0 <= box[1] - gb[3] <= lp_params["fallback_line_gap_pt"]
            overlap = min(gb[2], box[2]) > max(gb[0], box[0])
            if close and overlap:
                g["bbox"] = [min(gb[0], box[0]), gb[1], max(gb[2], box[2]), box[3]]
                g["words"] += ln
                break
        else:
            groups.append({"bbox": box, "words": list(ln)})
    out = []
    for g in groups:
        text = " ".join(w["text"] for w in g["words"])
        if len(text.strip()) >= lp_params["fallback_min_chars"]:
            out.append({"type": "Text", "score": None, "bbox": [round(v, 2) for v in g["bbox"]],
                        "source": "fallback", "words": g["words"]})
    return out


# ---------------------------------------------------------------------------
# 4. reading order and sections
# ---------------------------------------------------------------------------

def reading_order(blocks: list[dict], page_width: float, full_width_ratio: float) -> list[dict]:
    """
    Full-width blocks split the page into bands. Inside a band, blocks whose
    vertical extents overlap form a group; groups are read top to bottom, and
    inside a group the left column is read before the right. This handles
    two-column text (one tall group) and side-by-side tables (a group of two),
    while a heading below them still comes after both.
    """
    mid = page_width / 2
    ordered, band = [], []

    def flush():
        groups: list[list[dict]] = []
        bottom = None
        for b in sorted(band, key=lambda b: b["bbox"][1]):
            if groups and b["bbox"][1] < bottom:
                groups[-1].append(b)
                bottom = max(bottom, b["bbox"][3])
            else:
                groups.append([b])
                bottom = b["bbox"][3]
        for g in groups:
            left = [b for b in g if (b["bbox"][0] + b["bbox"][2]) / 2 < mid]
            right = [b for b in g if (b["bbox"][0] + b["bbox"][2]) / 2 >= mid]
            ordered.extend(sorted(left, key=lambda b: b["bbox"][1]))
            ordered.extend(sorted(right, key=lambda b: b["bbox"][1]))
        band.clear()

    for b in sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0])):
        if (b["bbox"][2] - b["bbox"][0]) >= full_width_ratio * page_width:
            flush()
            ordered.append(b)
        else:
            band.append(b)
    flush()
    return ordered


# ---------------------------------------------------------------------------
# 5. routing
# ---------------------------------------------------------------------------

def ocr_region(page, bbox: list[float], dpi: int, config: str) -> tuple[str, float | None]:
    """OCR one block when the PDF has no text layer there (scanned pages)."""
    import pytesseract
    from pytesseract import Output
    img = page.crop(bbox).to_image(resolution=dpi).original
    d = pytesseract.image_to_data(img, config=config, output_type=Output.DICT)
    words = [(t, float(c)) for t, c in zip(d["text"], d["conf"]) if t.strip() and float(c) >= 0]
    text = " ".join(t for t, _ in words)
    conf = round(sum(c for _, c in words) / len(words), 1) if words else None
    return text, conf


def extract_table_region(pdf_path: Path, page, block: dict, tp: dict, lp_params: dict) -> dict:
    """
    Run the Part 2 extractor on the table's padded region only.
    Camelot's table_areas use a bottom-left origin: 'x0,y_top,x1,y_bottom'.
    """
    import warnings
    import camelot

    h = float(page.height)
    pad = lp_params["pad_pt"]
    x0, top, x1, bottom = clip([block["bbox"][0] - pad, block["bbox"][1] - pad,
                                block["bbox"][2] + pad, block["bbox"][3] + pad],
                               float(page.width), h)
    area_str = f"{x0:.1f},{h - top:.1f},{x1:.1f},{h - bottom:.1f}"
    region = page.crop([x0, top, x1, bottom])
    ruled = count_ruling_lines(region, tp["rule_min_len_pt"]) >= tp["min_rulings"]
    order = tp["order_ruled"] if ruled else tp["order_borderless"]
    n_numeric = len(NUM_TOKEN.findall(region.extract_text() or ""))
    # tables found by layout can be small or narrow (a 2-column note table) and can
    # hold numbers in the label column (a table of contents), so their minimums are separate
    tpl = {**tp, "min_rows": lp_params["table_min_rows"], "min_cols": lp_params["table_min_cols"],
           "min_coverage": lp_params["table_min_coverage"]}
    region_box = [x0, top, x1, bottom]
    w = float(page.width)

    tried = []
    for flavor in order:
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                found = list(camelot.read_pdf(str(pdf_path), flavor=flavor,
                                              pages=str(page.page_number), table_areas=[area_str]))
        except Exception as exc:
            print(f"[WARN] p{page.page_number:04d} table camelot-{flavor} failed: {exc}")
            continue
        for t in found:
            # some flavors can ignore table_areas and return a table from elsewhere on
            # the page; only accept a table that actually lies inside this region
            tb = bbox_top_left(t, w, h)
            if tb and intersection(tb, region_box) / (area(tb) or 1.0) < lp_params["table_region_overlap"]:
                continue
            df = clean_df(t.df)
            tried.append({"flavor": flavor, "df": df, **score_table(t, df, tpl, n_numeric)})
        passing = [c for c in tried if c["flavor"] == flavor and c["valid_shape"]
                   and c["score"] >= tp["accept_score"]]
        if passing:
            best, status = max(passing, key=lambda c: (c["df"].size, c["score"])), "accepted"
            break
    else:
        valid = [c for c in tried if c["valid_shape"]]
        if not valid:
            return {"status": "no_valid_table", "order": order}
        best, status = max(valid, key=lambda c: (c["score"], c["df"].size)), "best_below_threshold"

    return {"status": status, "method": f"camelot-{best['flavor']}", "score": best["score"],
            "coverage": best["coverage"], "n_rows": best["n_rows"], "n_cols": best["n_cols"],
            "rows": best["df"].values.tolist(), "order": order}


# ---------------------------------------------------------------------------
# 6. overlays
# ---------------------------------------------------------------------------

def draw_overlay(pil, blocks: list[dict], dpi: int, path: Path) -> None:
    img = pil.copy()
    draw = ImageDraw.Draw(img)
    k = dpi / 72.0
    for b in blocks:
        color = COLORS["Fallback"] if b["source"] == "fallback" else COLORS.get(b["type"], (0, 0, 0))
        x0, y0, x1, y1 = (v * k for v in b["bbox"])
        draw.rectangle([x0, y0, x1, y1], outline=color, width=3)
        label = f"{b['order']}:{b['type'][:5]}" + ("" if b["score"] is None else f" {b['score']:.2f}")
        if b["source"] == "fallback":
            label = f"{b['order']}:fallback"
        draw.rectangle([x0, max(0, y0 - 14), x0 + 7 * len(label), y0], fill=color)
        draw.text((x0 + 2, max(0, y0 - 13)), label, fill=(255, 255, 255))
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

LOG_FIELDS = ["doc_id", "stem", "page", "raw", "below_threshold", "duplicates", "inside_table",
              "empty_overlap", "kept_model", "fallback", "ocr_blocks", "tables_ok", "tables_failed", "seconds"]


def process_pdf(pdf_path: Path, doc_id: str, model, params: dict, out_dir: Path,
                fig_dir: Path, overlay_dir: Path | None, pages: set[int] | None,
                overlay_pages: set[int] | None, log_rows: list[dict]) -> int:
    lpp, tp, ocr = params["layout"], params["tables"], params["ocr"]
    stem = pdf_path.stem
    section = None
    n_blocks = 0

    with pdfplumber.open(pdf_path) as pdf, \
            open(out_dir / f"{stem}.blocks.jsonl", "w", encoding="utf-8", newline="\n") as out:
        for page in pdf.pages:
            n = page.page_number
            if pages and n not in pages:
                continue
            t0 = time.perf_counter()

            raw, pil = detect(model, page, lpp["dpi"])
            blocks, stats = filter_blocks(raw, lpp)
            words = page.extract_words(x_tolerance=1.5, y_tolerance=3)
            leftover = assign_words(words, blocks)
            leftover = snap_to_blocks(leftover, blocks, {"Table"}, lpp["table_snap_pt"])
            # a smaller distance for text, so words from the next column are never pulled in
            leftover = snap_to_blocks(leftover, blocks, TEXT_TYPES, lpp["text_snap_pt"])
            blocks, stats["empty_overlap"] = drop_empty_overlaps(blocks, page, lpp, ocr["dpi"])
            fallback = fallback_blocks(leftover, lpp)
            blocks = reading_order(blocks + fallback, float(page.width), lpp["full_width_ratio"])

            n_ocr = tables_ok = tables_failed = 0
            for i, b in enumerate(blocks, 1):
                b["order"] = i
                rec = {"doc_id": doc_id, "stem": stem, "page": n, "block_id": f"p{n:04d}_b{i:03d}",
                       "order": i, "type": b["type"], "source": b["source"], "score": b["score"],
                       "bbox": [round(v, 2) for v in b["bbox"]], "units": "pt", "origin": "top-left",
                       "section": None, "text": "", "ocr": False, "ocr_conf": None,
                       "table": None, "figure_path": None, "model": lpp["model_name"]}

                text = lines_text(b["words"], lpp["fallback_y_tol_pt"], lpp["fallback_word_gap_pt"])
                if "ocr_result" in b:
                    text, conf = b["ocr_result"]
                    rec.update({"ocr": True, "ocr_conf": conf})
                    n_ocr += 1
                elif b["type"] in TEXT_TYPES and not text.strip():
                    text, conf = ocr_region(page, b["bbox"], ocr["dpi"], lpp["tesseract_config"])
                    rec.update({"ocr": True, "ocr_conf": conf})
                    n_ocr += 1
                rec["text"] = text

                if b["type"] == "Title" and text.strip():
                    section = " ".join(text.split())[:150]
                rec["section"] = section

                if b["type"] == "Table" and "ocr_result" in b:
                    # no text layer: Camelot cannot read it; the OCR text is kept
                    rec["table"] = {"status": "no_text_layer"}
                    tables_failed += 1
                elif b["type"] == "Table":
                    rec["table"] = extract_table_region(pdf_path, page, b, tp, lpp)
                    if rec["table"]["status"] == "no_valid_table":
                        tables_failed += 1
                    else:
                        tables_ok += 1
                elif b["type"] == "Figure":
                    fig_path = fig_dir / f"{stem}_p{n:04d}_b{i:03d}.png"
                    page.crop(b["bbox"]).to_image(resolution=lpp["dpi"]).original.save(fig_path)
                    rec["figure_path"] = fig_path.name

                out.write(json.dumps(rec, ensure_ascii=False) + "\n")

            if overlay_dir and (overlay_pages is None or n in overlay_pages):
                draw_overlay(pil, blocks, lpp["dpi"], overlay_dir / f"{stem}_p{n:04d}.png")

            secs = round(time.perf_counter() - t0, 2)
            log_rows.append({"doc_id": doc_id, "stem": stem, "page": n, **stats,
                             "kept_model": len(blocks) - len(fallback), "fallback": len(fallback),
                             "ocr_blocks": n_ocr, "tables_ok": tables_ok,
                             "tables_failed": tables_failed, "seconds": secs})
            print(f"       p{n:04d} raw={stats['raw']} kept={len(blocks) - len(fallback)} "
                  f"fallback={len(fallback)} tables={tables_ok}/{tables_ok + tables_failed} "
                  f"ocr={n_ocr} {secs}s")
            n_blocks += len(blocks)
    return n_blocks


def main() -> None:
    ap = argparse.ArgumentParser(description="Layout detection and routing (Part 3)")
    ap.add_argument("--input", default="data/rendered", help="Folder of PDFs, or one PDF")
    ap.add_argument("--output", default="data/layout")
    ap.add_argument("--figures", default="data/figures")
    ap.add_argument("--params", default="params.yaml")
    ap.add_argument("--manifest", default=None, help="manifest.csv (default: <input>/manifest.csv)")
    ap.add_argument("--pages", default=None, help="Only these pages, e.g. 1,32-34 (default: all)")
    ap.add_argument("--overlays", default=None, help="Folder for QA overlay images (optional)")
    ap.add_argument("--overlay-pages", default=None, help="Overlay only these pages (default: all processed)")
    args = ap.parse_args()

    params = load_params(args.params)
    input_path, out_dir, fig_dir = Path(args.input), Path(args.output), Path(args.figures)
    out_dir.mkdir(parents=True, exist_ok=True)
    fig_dir.mkdir(parents=True, exist_ok=True)
    overlay_dir = Path(args.overlays) if args.overlays else None

    manifest_path = Path(args.manifest) if args.manifest else (
        input_path / "manifest.csv" if input_path.is_dir() else input_path.parent / "manifest.csv")
    manifest = load_manifest(manifest_path)

    pdfs = find_pdfs(input_path)
    if not pdfs:
        print(f"[ERROR] No PDFs found in {input_path}")
        return

    model = load_model(params["layout"])
    log_rows: list[dict] = []
    for pdf_path in pdfs:
        doc_id = manifest.get(pdf_path.stem, pdf_path.stem)
        print(f"[INFO] {pdf_path.name} (doc_id={doc_id})")
        n = process_pdf(pdf_path, doc_id, model, params, out_dir, fig_dir, overlay_dir,
                        parse_pages(args.pages), parse_pages(args.overlay_pages), log_rows)
        print(f"[INFO] {pdf_path.name}: {n} blocks")

    with open(out_dir / "layout_log.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=LOG_FIELDS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(log_rows)
    print("[INFO] Layout complete")


if __name__ == "__main__":
    main()