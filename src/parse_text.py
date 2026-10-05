"""
Part 1 - Per-page text extraction with pdfplumber, OCR trigger and
Tesseract OCR fallback

Usage (DVC stage):
    python src/parse_text.py

Standalone / CI:
    python src/parse_text.py --input tests/fixtures --output data/parsed

Outputs:
    {output}/{stem}_p{NNNN}.txt     one text file per page
    {output}/{stem}.words.jsonl     word boxes in PDF points, top-left origin
    {output}/ocr_log.csv            one row per page: the OCR decision

Needs the Tesseract binary. If it is not on PATH (common on Windows), set
the TESSERACT_CMD environment variable to the full path of tesseract.exe.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
from pathlib import Path

import pdfplumber
import pytesseract
import yaml
from pytesseract import Output

# pdfplumber tolerances (gap in points that still counts as one word / line)
X_TOLERANCE = 1.5
Y_TOLERANCE = 3

# Tesseract settings: LSTM engine, one uniform block (good for statements)
TESS_CONFIG = "--oem 1 --psm 6"

# Junk tokens: unmapped glyphs like (cid:42), Unicode replacement chars,
# or control characters. Normal symbols such as $ or em dashes are NOT junk.
JUNK = re.compile(r"\(cid:\d+\)|\ufffd|[\x00-\x08\x0b\x0c\x0e-\x1f]")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def load_params(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_manifest(path: Path) -> dict[str, str]:
    """Map stem -> accession from manifest.csv. Empty if there is no manifest
    (e.g. tests/fixtures in CI), in which case doc_id falls back to the stem."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as f:
        return {row["stem"]: row["accession"] for row in csv.DictReader(f)}


def find_pdfs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(input_path.glob("*.pdf"))


def extract_page(page) -> tuple[str, list[dict]]:
    """Text and word boxes from the PDF text layer."""
    text = page.extract_text(x_tolerance=X_TOLERANCE, y_tolerance=Y_TOLERANCE) or ""
    words = [
        {
            "text": w["text"],
            "bbox": [round(w["x0"], 2), round(w["top"], 2),
                     round(w["x1"], 2), round(w["bottom"], 2)],
        }
        for w in page.extract_words(x_tolerance=X_TOLERANCE, y_tolerance=Y_TOLERANCE)
    ]
    return text, words


def image_coverage(page) -> float:
    """Share of the page area covered by images (clipped to the page)."""
    page_area = float(page.width * page.height)
    covered = 0.0
    for im in page.images:
        x0, x1 = max(im["x0"], 0), min(im["x1"], page.width)
        top, bottom = max(im["top"], 0), min(im["bottom"], page.height)
        if x1 > x0 and bottom > top:
            covered += (x1 - x0) * (bottom - top)
    return min(covered / page_area, 1.0) if page_area else 0.0


def ocr_decision(text: str, page, ocr_params: dict) -> dict:
    """
    Combine three signals. OCR is triggered if ANY of them fires.
    Returns the measured signals and the reasons that fired.
    """
    n_chars = len(text.strip())
    tokens = text.split()
    junk = sum(1 for t in tokens if JUNK.search(t))
    junk_ratio = junk / len(tokens) if tokens else 0.0
    img_cov = image_coverage(page)

    reasons = []
    if n_chars < ocr_params["min_chars"]:
        reasons.append("low_chars")
    if junk_ratio > ocr_params["junk_ratio"]:
        reasons.append("junk_text")
    if img_cov > ocr_params["image_coverage"]:
        reasons.append("image_coverage")

    return {
        "n_chars": n_chars,
        "junk_ratio": round(junk_ratio, 3),
        "image_coverage": round(img_cov, 3),
        "triggered": bool(reasons),
        "reason": ";".join(reasons) if reasons else "none",
    }


def run_ocr(page, dpi: int) -> tuple[str, list[dict], float | None]:
    """
    OCR one page with Tesseract.

    Tesseract works in pixels of the rendered image, so every box is
    converted back to PDF points: pt = px * 72 / dpi (top-left origin).
    Returns (text, words, mean_confidence).
    """
    img = page.to_image(resolution=dpi).original  # PIL image
    d = pytesseract.image_to_data(img, config=TESS_CONFIG, output_type=Output.DICT)
    k = 72.0 / dpi

    words: list[dict] = []
    lines: dict[tuple, list[str]] = {}
    for i, t in enumerate(d["text"]):
        conf = float(d["conf"][i])
        if not t.strip() or conf < 0:
            continue
        x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
        words.append({
            "text": t,
            "bbox": [round(x * k, 2), round(y * k, 2),
                     round((x + w) * k, 2), round((y + h) * k, 2)],
            "conf": round(conf, 1),
        })
        key = (d["block_num"][i], d["par_num"][i], d["line_num"][i])
        lines.setdefault(key, []).append(t)

    text = "\n".join(" ".join(ws) for ws in lines.values())
    mean_conf = (round(sum(w["conf"] for w in words) / len(words), 1)
                 if words else None)
    return text, words, mean_conf


def check_tesseract() -> None:
    """Fail early with a clear message if Tesseract cannot be found."""
    cmd = os.environ.get("TESSERACT_CMD")
    if cmd:
        pytesseract.pytesseract.tesseract_cmd = cmd
    try:
        pytesseract.get_tesseract_version()
    except pytesseract.TesseractNotFoundError as exc:
        raise SystemExit(
            "[ERROR] Tesseract not found. Install it and put it on PATH, or set "
            "TESSERACT_CMD to the full path of tesseract.exe"
        ) from exc


def parse_pdf(pdf_path: Path, out_dir: Path, doc_id: str,
              ocr_params: dict, log_rows: list[dict]) -> int:
    """Write per-page .txt files and one words.jsonl; add one log row per
    page. Pages that trigger the OCR rule are re-read with Tesseract."""
    stem = pdf_path.stem
    words_path = out_dir / f"{stem}.words.jsonl"

    with pdfplumber.open(pdf_path) as pdf, \
            open(words_path, "w", encoding="utf-8", newline="\n") as wf:
        for page in pdf.pages:
            n = page.page_number  # 1-based
            text, words = extract_page(page)
            decision = ocr_decision(text, page, ocr_params)

            engine, mean_conf, used_ocr = "pdfplumber", None, False
            for w in words:
                w["conf"] = None

            if decision["triggered"]:
                ocr_text, ocr_words, mean_conf = run_ocr(page, ocr_params["dpi"])
                engine, used_ocr = "tesseract", True
                text, words = ocr_text, ocr_words
                if not text.strip():
                    print(f"[WARN] {stem} p{n:04d}: OCR returned no text")

            print(f"       p{n:04d} chars={decision['n_chars']} "
                  f"junk={decision['junk_ratio']} img={decision['image_coverage']} "
                  f"-> OCR={used_ocr} ({decision['reason']})"
                  + (f" conf={mean_conf}" if used_ocr else ""))

            log_rows.append({
                "doc_id": doc_id,
                "stem": stem,
                "page": n,
                "triggered": used_ocr,
                "reason": decision["reason"],
                "engine": engine,
                "mean_conf": "" if mean_conf is None else mean_conf,
                "n_chars": decision["n_chars"],
                "junk_ratio": decision["junk_ratio"],
                "image_coverage": decision["image_coverage"],
            })

            (out_dir / f"{stem}_p{n:04d}.txt").write_text(
                text, encoding="utf-8", newline="\n")

            for w in words:
                rec = {
                    "doc_id": doc_id,
                    "stem": stem,
                    "page": n,
                    "text": w["text"],
                    "bbox": w["bbox"],
                    "units": "pt",
                    "origin": "top-left",
                    "source": engine,
                    "ocr": used_ocr,
                    "conf": w["conf"],
                }
                wf.write(json.dumps(rec, ensure_ascii=False) + "\n")

        return len(pdf.pages)


def write_ocr_log(log_rows: list[dict], path: Path) -> None:
    fields = ["doc_id", "stem", "page", "triggered", "reason", "engine",
              "mean_conf", "n_chars", "junk_ratio", "image_coverage"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(log_rows)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Per-page text + word boxes")
    parser.add_argument("--input", default="data/rendered",
                        help="Folder of PDFs, or one PDF (default: data/rendered)")
    parser.add_argument("--output", default="data/parsed",
                        help="Output folder (default: data/parsed)")
    parser.add_argument("--params", default="params.yaml")
    parser.add_argument("--manifest", default=None,
                        help="manifest.csv (default: <input>/manifest.csv)")
    args = parser.parse_args()

    ocr_params = load_params(args.params)["ocr"]
    check_tesseract()
    input_path = Path(args.input)
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = Path(args.manifest) if args.manifest else (
        input_path / "manifest.csv" if input_path.is_dir()
        else input_path.parent / "manifest.csv")
    manifest = load_manifest(manifest_path)

    pdfs = find_pdfs(input_path)
    if not pdfs:
        print(f"[ERROR] No PDFs found in {input_path}")
        return

    log_rows: list[dict] = []
    for pdf_path in pdfs:
        doc_id = manifest.get(pdf_path.stem, pdf_path.stem)
        print(f"[INFO] {pdf_path.name} (doc_id={doc_id})")
        n_pages = parse_pdf(pdf_path, out_dir, doc_id, ocr_params, log_rows)
        print(f"[INFO] {pdf_path.name}: {n_pages} pages")

    write_ocr_log(log_rows, out_dir / "ocr_log.csv")
    n_ocr = sum(1 for r in log_rows if r["triggered"])
    print(f"[INFO] OCR used on {n_ocr} of {len(log_rows)} pages -> ocr_log.csv")
    print("[INFO] Text extraction complete")


if __name__ == "__main__":
    main()