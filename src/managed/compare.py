"""
Part 7 - Side-by-side comparison of AWS Textract with the open-source pipeline, on the pages
sent to Textract (all read from the cache: no API calls). Evidence for reports/build_vs_buy.md.

1. Statement tables (FY2025 p32, p34): every numeric cell of Textract's table vs Part 2's
   table (data/tables/*.norm.csv). Both are normalized by the same Part 2 normalizer, with the
   same page text for the scale, so differences are reading or structure differences.
   A cell is (row label, which occurrence of that label, position among the row's numbers, value).
2. Scanned fixture (tests/fixtures/scanned.pdf p1-3 = FY2025 p5-7 rasterized): Tesseract
   (Part 1's run_ocr, same dpi/config) and Textract, both scored against the born-digital
   text of FY2025 p5-7 (data/parsed): WER, CER and numeric-token accuracy, both as is and with
   typography normalized (curly quotes vs straight, dashes, (R)/TM/(C) signs), which separates
   misread words from characters printed in another form.
3. Below-threshold tables (p22, p47): Camelot's best attempt (Part 3 block) and Textract's
   table, written side by side for inspection, with shape and numeric-cell counts.

Usage (repo root):  python -m src.managed.compare
Writes reports/managed/: comparison.json, table_cells.csv, ocr_scores.csv, and the side-by-side CSVs.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import pandas as pd
import yaml

from src.export import table_object
from src.managed import cache
from src.managed.blocks import overlap_ratio, page_size_of, table_grid, to_points

NUM = re.compile(r"\(?-?\$?\d[\d,]*(?:\.\d+)?\)?")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def label_key(label) -> str:
    """Letters only, lower case: 'Cost ofsales:' and 'Cost of sales:' compare equal."""
    return re.sub(r"[^a-z]", "", str(label or "").lower())


def as_number(v) -> float | None:
    """A float for any numeric cell (Python, numpy or a numeric string), else None."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(str(v).replace(",", "").strip())
    except ValueError:
        return None
    return None if f != f else f          # NaN -> None


def cells(rows: list[list], kinds: list[str]) -> dict[tuple, float]:
    """{(label key, occurrence, k-th number in the row): value} for non-header rows."""
    out, seen = {}, {}
    for kind, row in zip(kinds, rows):
        if kind == "header" or not row:
            continue
        key = label_key(row[0])
        if not key:
            continue
        occ = seen.get(key, 0)
        seen[key] = occ + 1
        nums = [n for n in (as_number(v) for v in row[1:]) if n is not None]
        for k, v in enumerate(nums):
            out[(key, occ, k)] = round(v, 2)
    return out


def textract_response(cache_dir, pdf: Path, page: int, mp: dict) -> dict | None:
    key = cache.cache_key(cache.sha256_file(pdf), page, mp["provider"], mp["api"], mp["features"])
    e = cache.read(cache_dir, key)
    return e["response"] if e else None


def largest_table(resp: dict) -> list[list[str]]:
    by_id = {b["Id"]: b for b in resp["Blocks"]}
    grids = [table_grid(t, by_id) for t in resp["Blocks"] if t["BlockType"] == "TABLE"]
    return max(grids, key=len) if grids else []


def levenshtein(a: list, b: list) -> int:
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def norm_text(s: str) -> str:
    """Lower case and collapsed whitespace; punctuation kept (it carries signs like (1,234))."""
    return " ".join(s.lower().split())


# Typography only: the same character in another form, or a symbol with no word in it.
TYPOGRAPHY = str.maketrans({"\u2019": "'", "\u2018": "'", "\u201c": '"', "\u201d": '"',
                            "\u2013": "-", "\u2014": "-", "\u00ae": None, "\u2122": None, "\u00a9": None})


def typo_norm(s: str) -> str:
    """norm_text plus typography normalized: curly quotes -> straight, dashes -> '-', no (R) TM (C)."""
    return norm_text(s.translate(TYPOGRAPHY))


def text_scores(ref: str, hyp: str) -> dict:
    r, h = norm_text(ref), norm_text(hyp)
    rw, hw = r.split(), h.split()
    tr, th = typo_norm(ref), typo_norm(hyp)
    ref_nums = NUM.findall(ref)
    hyp_nums = set(NUM.findall(hyp))
    return {"ref_words": len(rw), "hyp_words": len(hw),
            "wer": round(levenshtein(rw, hw) / max(len(rw), 1), 4),
            "cer": round(levenshtein(list(r), list(h)) / max(len(r), 1), 4),
            "wer_typography_normalized": round(levenshtein(tr.split(), th.split()) / max(len(tr.split()), 1), 4),
            "cer_typography_normalized": round(levenshtein(list(tr), list(th)) / max(len(tr), 1), 4),
            "ref_numbers": len(ref_nums),
            "num_acc": round(sum(n in hyp_nums for n in ref_nums) / max(len(ref_nums), 1), 4)}


def write_rows(path: Path, rows: list[list]) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        csv.writer(f, lineterminator="\n").writerows(rows)


# ---------------------------------------------------------------------------
# the three comparisons
# ---------------------------------------------------------------------------

def compare_statements(a, mp, tp, out: Path) -> tuple[list[dict], list[dict]]:
    pdf = Path(a.rendered) / f"{a.stem}.pdf"
    summary, cell_rows = [], []
    for page in a.statement_pages:
        norm_files = sorted(Path(a.tables).glob(f"{a.stem}_p{page:04d}_*.norm.csv"))
        resp = textract_response(a.cache, pdf, page, mp)
        if not norm_files or resp is None:
            print(f"[WARN] p{page}: missing Part 2 table or cached Textract response, skipped")
            continue
        p2 = pd.read_csv(norm_files[0], header=None, dtype=object, keep_default_na=False)
        p2_kinds = p2.iloc[:, 0].tolist()
        p2_rows = [[r[1]] + list(r[2:]) for r in p2.itertuples(index=False)]
        page_text = (Path(a.parsed) / f"{a.stem}_p{page:04d}.txt").read_text(encoding="utf-8")
        tobj = table_object({"table": {"rows": largest_table(resp), "status": "accepted",
                                       "method": "aws-textract"}}, page_text, tp)
        c_p2, c_tx = cells(p2_rows, p2_kinds), cells(tobj["rows"], tobj["row_kinds"])
        both = [k for k in c_p2 if k in c_tx and c_p2[k] == c_tx[k]]
        for k in sorted(set(c_p2) | set(c_tx)):
            v2, vt = c_p2.get(k), c_tx.get(k)
            cell_rows.append({"page": page, "label": k[0], "occurrence": k[1], "position": k[2],
                              "part2": v2, "textract": vt,
                              "status": "match" if v2 == vt else ("part2_only" if vt is None else
                                                                   "textract_only" if v2 is None else "differ")})
        p = len(both) / len(c_tx) if c_tx else 0.0
        r = len(both) / len(c_p2) if c_p2 else 0.0
        summary.append({"page": page, "part2_table": norm_files[0].name, "part2_cells": len(c_p2),
                        "textract_cells": len(c_tx), "matching": len(both),
                        "agreement_precision": round(p, 4), "agreement_recall": round(r, 4),
                        "agreement_f1": round(2 * p * r / (p + r), 4) if p + r else 0.0,
                        "textract_shape": f"{len(tobj['rows'])}x{len(tobj['columns'])}",
                        "part2_shape": f"{len(p2_rows)}x{len(p2.columns) - 1}"})
        print(f"[INFO] p{page}: Part 2 {len(c_p2)} cells, Textract {len(c_tx)}, matching {len(both)}")
    return summary, cell_rows


def compare_ocr(a, mp, params, out: Path) -> list[dict]:
    import pdfplumber
    from src.parse_text import check_tesseract, run_ocr
    check_tesseract()
    scanned = Path(a.scanned)
    rows = []
    with pdfplumber.open(scanned) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            ref_page = a.scanned_from + i - 1
            ref = (Path(a.parsed) / f"{a.stem}_p{ref_page:04d}.txt").read_text(encoding="utf-8")
            tess, _, tess_conf = run_ocr(page, params["ocr"]["dpi"], params["ocr"]["tesseract_config"])
            resp = textract_response(a.cache, scanned, i, mp)
            engines = [("tesseract", tess, tess_conf)]
            if resp is not None:
                lines = [b for b in resp["Blocks"] if b["BlockType"] == "LINE"]
                conf = sum(b["Confidence"] for b in lines) / max(len(lines), 1)
                engines.append(("aws-textract", "\n".join(b.get("Text", "") for b in lines), round(conf, 1)))
            for engine, hyp, conf in engines:
                s = text_scores(ref, hyp)
                rows.append({"scanned_page": i, "reference": f"{a.stem} p{ref_page} (born-digital text)",
                             "engine": engine, "self_reported_conf": conf, **s})
                print(f"[INFO] scanned p{i} {engine:<12} WER {s['wer']:.3f}  CER {s['cer']:.3f}  "
                      f"| typography-normalized WER {s['wer_typography_normalized']:.3f}  "
                      f"CER {s['cer_typography_normalized']:.3f}  | numbers {s['num_acc']:.3f}  (conf {conf})")
    return rows


def compare_below_threshold(a, mp, out: Path) -> list[dict]:
    pdf = Path(a.rendered) / f"{a.stem}.pdf"
    blocks = [json.loads(l) for l in (Path(a.layout) / f"{a.stem}.blocks.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    rows = []
    for page in a.below_pages:
        resp = textract_response(a.cache, pdf, page, mp)
        if resp is None:
            continue
        size = page_size_of(pdf, page)
        by_id = {b["Id"]: b for b in resp["Blocks"]}
        tx_tables = [(to_points(t, size), table_grid(t, by_id)) for t in resp["Blocks"] if t["BlockType"] == "TABLE"]
        for b in blocks:
            t = b.get("table") or {}
            if b["page"] != page or b.get("type") != "Table" or t.get("status") not in ("best_below_threshold", "no_valid_table"):
                continue
            camelot = t.get("rows") or []
            best = max(tx_tables, key=lambda x: overlap_ratio(b["bbox"], x[0]), default=None)
            ov = overlap_ratio(b["bbox"], best[0]) if best else 0.0
            textract = best[1] if best and ov > 0.5 else []
            if camelot or textract:
                write_rows(out / f"{a.stem}_{b['block_id']}_camelot.csv", camelot)
                write_rows(out / f"{a.stem}_{b['block_id']}_textract.csv", textract)

            def stats(grid):
                flat = [c for r in grid for c in r]
                return (f"{len(grid)}x{max((len(r) for r in grid), default=0)}",
                        sum(1 for c in flat if NUM.fullmatch(str(c).replace(" ", "").replace("$", "") or "x")),
                        round(sum(1 for c in flat if not str(c).strip()) / max(len(flat), 1), 3))
            cs, ts = stats(camelot), stats(textract)
            rows.append({"page": page, "block_id": b["block_id"], "camelot_method": t.get("method"),
                         "camelot_score": t.get("score"), "camelot_status": t.get("status"),
                         "camelot_shape": cs[0], "camelot_numeric_cells": cs[1], "camelot_empty_share": cs[2],
                         "textract_overlap": round(ov, 2), "textract_shape": ts[0],
                         "textract_numeric_cells": ts[1], "textract_empty_share": ts[2]})
            print(f"[INFO] p{page} {b['block_id']}: Camelot {t.get('method')} {cs[0]} ({cs[1]} numbers, "
                  f"{cs[2]:.0%} empty) vs Textract {ts[0]} ({ts[1]} numbers, {ts[2]:.0%} empty)")
    return rows


def main() -> None:
    ap = argparse.ArgumentParser(description="Textract vs the open-source pipeline on the cached pages")
    ap.add_argument("--stem", default="AAPL_10K_20250927")
    ap.add_argument("--statement-pages", type=int, nargs="+", default=[32, 34])
    ap.add_argument("--below-pages", type=int, nargs="+", default=[22, 47])
    ap.add_argument("--scanned", default="tests/fixtures/scanned.pdf")
    ap.add_argument("--scanned-from", type=int, default=5, help="page of --stem that scanned page 1 was made from")
    ap.add_argument("--cache", default="data/managed")
    ap.add_argument("--tables", default="data/tables")
    ap.add_argument("--parsed", default="data/parsed")
    ap.add_argument("--layout", default="data/layout")
    ap.add_argument("--rendered", default="data/rendered")
    ap.add_argument("--params", default="params.yaml")
    ap.add_argument("--out", default="reports/managed")
    a = ap.parse_args()

    params = yaml.safe_load(open(a.params, encoding="utf-8"))
    mp, tp = params["managed"], params["tables"]
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    tables, cell_rows = compare_statements(a, mp, tp, out)
    ocr = compare_ocr(a, mp, params, out)
    below = compare_below_threshold(a, mp, out)

    for name, rows in (("table_cells.csv", cell_rows), ("ocr_scores.csv", ocr), ("below_threshold.csv", below)):
        if rows:
            with open(out / name, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
                w.writeheader()
                w.writerows(rows)
    (out / "comparison.json").write_text(json.dumps({"statement_tables": tables, "ocr": ocr,
                                                     "below_threshold": below}, indent=1) + "\n",
                                         encoding="utf-8", newline="\n")
    print(f"[INFO] wrote {out}/comparison.json, table_cells.csv, ocr_scores.csv, below_threshold.csv")


if __name__ == "__main__":
    main()
