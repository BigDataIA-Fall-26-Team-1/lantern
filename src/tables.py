"""
Part 2 - Table extraction: bake-off, hybrid extractor and normalizer

Usage (DVC stage):
    python src/tables.py

Standalone / CI:
    python src/tables.py --input tests/fixtures --output data/tables

Outputs:
    {output}/{stem}_p{NNNN}_{statement}.raw.csv    cells exactly as extracted (cleaned of empty rows/cols)
    {output}/{stem}_p{NNNN}_{statement}.norm.csv   same grid, numeric cells normalized and scaled
    {output}/tables_log.csv                        one row per statement page: method chosen and why
    {output}/bakeoff.csv                           every method on every statement page (evidence)

All bounding boxes are written in PDF points, top-left origin, to match
Part 1. Camelot reports bottom-left, so boxes are flipped here.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import time
import warnings
from pathlib import Path

import camelot
import pandas as pd
import pdfplumber
import yaml

# A numeric token: 1,234  (1,234)  $391,035  12.5  2025
NUM_TOKEN = re.compile(r"\(?\$?\d[\d,]*\.?\d*\)?")
YEAR = re.compile(r"^(19|20)\d{2}$")
# Footnote markers only at the END of a cell: (a)  *  [1]  and (1) right after a number.
# The lookbehind keeps a standalone "(5)" as negative 5.
FOOTNOTE = re.compile(r"(?<=[\d)])\s*\(\d\)$|\s*(\([a-z]\)|\*+|\[\d+\])$")
# hyphen, en dash, em dash, minus sign
DASHES = {"-", "\u2013", "\u2014", "\u2212"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def load_params(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_manifest(path: Path) -> dict[str, str]:
    """stem -> accession. Empty when there is no manifest (CI fixtures)."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as f:
        return {row["stem"]: row["accession"] for row in csv.DictReader(f)}


def find_pdfs(input_path: Path) -> list[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(input_path.glob("*.pdf"))


# ---------------------------------------------------------------------------
# 1. find statement pages
# ---------------------------------------------------------------------------

def count_ruling_lines(page, min_len_pt: float) -> int:
    """Horizontal rules at least min_len_pt long (lines or thin rectangles)."""
    n = sum(1 for ln in page.lines
            if abs(ln["top"] - ln["bottom"]) < 1 and (ln["x1"] - ln["x0"]) >= min_len_pt)
    n += sum(1 for r in page.rects
             if (r["bottom"] - r["top"]) <= 2 and (r["x1"] - r["x0"]) >= min_len_pt)
    return n


def scan_statement_pages(pdf_path: Path, tp: dict) -> list[dict]:
    """
    A page is a statement page when a statement title appears in its first
    few lines (so table-of-contents and note references do not count) and
    it carries enough numbers to be a real statement.
    """
    hits = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text() or ""
            head = "\n".join(text.splitlines()[: tp["title_top_lines"]]).upper()
            n_numeric = len(NUM_TOKEN.findall(text))
            if n_numeric < tp["min_numeric_tokens"]:
                continue
            for key, patterns in tp["statements"].items():
                if any(p.upper() in head for p in patterns):
                    hits.append({
                        "page": page.page_number,
                        "statement": key,
                        "height": float(page.height),
                        "width": float(page.width),
                        "text": text,
                        "ruling_lines": count_ruling_lines(page, tp["rule_min_len_pt"]),
                        "n_numeric": n_numeric,
                    })
                    break
    return hits


# ---------------------------------------------------------------------------
# 2. run methods and score them
# ---------------------------------------------------------------------------

def run_camelot(pdf_path: Path, page_no: int, flavor: str):
    """Returns (tables, seconds, error)."""
    t0 = time.perf_counter()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            tables = list(camelot.read_pdf(str(pdf_path), flavor=flavor, pages=str(page_no)))
        err = ""
    except Exception as exc:  # one failing flavor must not stop the run
        tables, err = [], f"{type(exc).__name__}: {exc}"
    return tables, round(time.perf_counter() - t0, 3), err


def clean_df(df: pd.DataFrame) -> pd.DataFrame:
    """Strip cells, drop empty rows and columns that only hold '$' or nothing."""
    df = df.map(lambda v: "" if v is None else str(v).replace("\n", " ").strip())
    df = df[[c for c in df.columns if not df[c].isin(["", "$"]).all()]]
    df = df[~(df == "").all(axis=1)].reset_index(drop=True)
    df.columns = range(df.shape[1])
    return df


LABEL_ENDS_IN_NUMBER = re.compile(r"\d[\d,]*\)?$")


def shape_metrics(df: pd.DataFrame, tp: dict, n_numeric: int) -> dict:
    """
    Checks that do not depend on the extractor, so every method is judged the same way.
      whitespace     % empty cells in the CLEANED table (what we actually keep)
      coverage       share of the page's numbers that landed in value cells
      label_merge    share of labelled rows whose label ends in a number,
                     e.g. 'Operating income 133,050' = columns shifted left
    """
    rows, cols = df.shape
    cells = df.size or 1
    whitespace = 100 * int((df == "").sum().sum()) / cells
    numeric_cells = sum(1 for j in range(1, cols) for v in df[j] if to_number(v)[0] is not None)
    coverage = numeric_cells / max(n_numeric, 1)
    labels = [str(v) for v in df[0] if str(v).strip()] if cols else []
    label_merge = (sum(1 for v in labels if LABEL_ENDS_IN_NUMBER.search(v)) / len(labels)
                   if labels else 1.0)
    valid = (rows >= tp["min_rows"] and cols >= tp["min_cols"]
             and coverage >= tp["min_coverage"] and label_merge <= tp["max_label_merge"])
    return {"n_rows": rows, "n_cols": cols, "whitespace": round(whitespace, 2),
            "coverage": round(coverage, 3), "label_merge": round(label_merge, 3),
            "valid_shape": valid}


def score_table(table, df: pd.DataFrame, tp: dict, n_numeric: int) -> dict:
    rep = table.parsing_report
    acc = float(rep.get("accuracy", 0) or 0)
    m = shape_metrics(df, tp, n_numeric)
    return {**m,
            "accuracy": round(acc, 2),
            "whitespace_raw": round(float(rep.get("whitespace", 0) or 0), 2),
            "score": round(acc - tp["whitespace_weight"] * m["whitespace"], 2)}


def bakeoff_page(pdf_path: Path, hit: dict, tp: dict, doc_id: str, stem: str) -> list[dict]:
    """Every method on one page. Reports the largest table each method found."""
    rows = []
    base = {"doc_id": doc_id, "stem": stem, "page": hit["page"], "statement": hit["statement"]}

    for flavor in tp["bakeoff_flavors"]:
        tables, secs, err = run_camelot(pdf_path, hit["page"], flavor)
        best = None
        for t in tables:
            sc = score_table(t, clean_df(t.df), tp, hit["n_numeric"])
            key = (sc["valid_shape"], sc["n_rows"] * sc["n_cols"])
            if best is None or key > (best["valid_shape"], best["n_rows"] * best["n_cols"]):
                best = sc
        rows.append({**base, "method": f"camelot-{flavor}", "n_tables": len(tables),
                     "seconds": secs, "error": err, **(best or {})})

    # pdfplumber text strategy (no accuracy report; empty-cell % stands in for whitespace)
    t0 = time.perf_counter()
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[hit["page"] - 1]
        found = page.extract_tables({"vertical_strategy": "text", "horizontal_strategy": "text"})
    secs = round(time.perf_counter() - t0, 3)
    best = None
    for grid in found:
        sc = {**shape_metrics(clean_df(pd.DataFrame(grid)), tp, hit["n_numeric"]),
              "accuracy": "", "whitespace_raw": "", "score": ""}
        key = (sc["valid_shape"], sc["n_rows"] * sc["n_cols"])
        if best is None or key > (best["valid_shape"], best["n_rows"] * best["n_cols"]):
            best = sc
    rows.append({**base, "method": "pdfplumber-text", "n_tables": len(found),
                 "seconds": secs, "error": "", **(best or {})})
    return rows


# ---------------------------------------------------------------------------
# 3. hybrid extractor
# ---------------------------------------------------------------------------

def extract_best(pdf_path: Path, hit: dict, tp: dict) -> dict:
    """
    Ruled pages try lattice first, borderless pages try stream first.
    The first flavor with a valid-shape table scoring >= accept_score wins.
    Otherwise the best valid table across all tried flavors is kept.
    """
    ruled = hit["ruling_lines"] >= tp["min_rulings"]
    order = tp["order_ruled"] if ruled else tp["order_borderless"]
    tried = []

    for flavor in order:
        tables, _, err = run_camelot(pdf_path, hit["page"], flavor)
        if err:
            print(f"[WARN] p{hit['page']:04d} camelot-{flavor} failed: {err}")
        for t in tables:
            df = clean_df(t.df)
            tried.append({"flavor": flavor, "table": t, "df": df,
                          **score_table(t, df, tp, hit["n_numeric"])})
        passing = [c for c in tried
                   if c["flavor"] == flavor and c["valid_shape"] and c["score"] >= tp["accept_score"]]
        if passing:
            best = max(passing, key=lambda c: (c["df"].size, c["score"]))
            return {**best, "decision": "accepted", "ruled": ruled, "order": order}

    valid = [c for c in tried if c["valid_shape"]]
    if valid:
        best = max(valid, key=lambda c: (c["score"], c["df"].size))
        return {**best, "decision": "best_below_threshold", "ruled": ruled, "order": order}
    return {"decision": "no_valid_table", "ruled": ruled, "order": order}


def bbox_top_left(table, page_width: float, page_height: float) -> list[float] | None:
    """
    Camelot bbox is (x1, y1, x2, y2) bottom-left. Flip to [x0, top, x1, bottom]
    top-left, and clamp to the page (stream can report a few points outside it).
    """
    b = getattr(table, "_bbox", None)
    if not b:
        return None
    x1, y1, x2, y2 = b
    x0, x1 = max(0.0, x1), min(page_width, x2)
    top, bottom = max(0.0, page_height - y2), min(page_height, page_height - y1)
    return [round(x0, 2), round(top, 2), round(x1, 2), round(bottom, 2)]


# ---------------------------------------------------------------------------
# 4. normalizer
# ---------------------------------------------------------------------------

def to_number(raw: str) -> tuple[float | None, str]:
    """
    '$ (1,234)' -> -1234 ; dash -> 0 ; '12.5%' -> 12.5 (pct) ; text -> None.
    Returns (value, unit) where unit is 'num' or 'pct'.

    Decisions:
      - Footnote markers are stripped only when they trail a value:
        '1,234(1)' -> 1234, '1,234 (a)' -> 1234, '1,234*' -> 1234.
      - A lone '(1)' is read as -1 (a negative number), not a footnote.
      - '(565' with a missing closing parenthesis is read as -565
        (pdfplumber dropped the ')' on the fixture).
      - Percentages are returned unscaled with unit 'pct': '12%' -> (12.0, 'pct').
    """
    s = (raw or "").strip()
    if not s:
        return None, "num"
    s = FOOTNOTE.sub("", s)
    s = s.replace("$", "").replace(",", "").replace(" ", "")
    if s in DASHES:
        return 0.0, "num"
    # '(565)' is negative; so is '(565' when an extractor drops the closing parenthesis
    neg = s.startswith("(")
    s = s.strip("()")
    if s[:1] in DASHES:
        neg, s = True, s[1:]
    unit = "pct" if s.endswith("%") else "num"
    s = s.rstrip("%")
    try:
        v = float(s)
    except ValueError:
        return None, "num"
    return (-v if neg else v), unit


def detect_scales(page_text: str, tp: dict) -> tuple[int, int]:
    """
    Read the caption, e.g. '(In millions, except number of shares, which are
    reflected in thousands, and per-share amounts)'.
    Returns (money_scale, shares_scale).
    """
    t = page_text.lower()
    words = tp["scale_words"]
    # the FIRST 'in <scale>' in the caption is the money scale
    m = re.search(r"\bin\s+(" + "|".join(words) + r")\b", t)
    base = int(words[m.group(1)]) if m else 1
    shares = base
    m = re.search(r"shares[^.)]*?\bin\s+(" + "|".join(words) + r")\b", t)
    if m:
        shares = int(words[m.group(1)])
    return base, shares


def classify_rows(df: pd.DataFrame, tp: dict) -> list[str]:
    """
    Label each row: header (years / no numbers), money, shares or per_share.
    A label-only row like 'Earnings per share:' sets the section for the
    rows under it (Basic, Diluted).
    """
    kinds, section = [], "money"
    for _, row in df.iterrows():
        label = str(row.iloc[0]).lower()
        cells = [str(v) for v in row.iloc[1:]]
        nums = [c for c in cells if to_number(c)[0] is not None]
        if not nums:
            if re.search(tp["shares_pattern"], label):
                section = "shares"
            elif re.search(tp["per_share_pattern"], label):
                section = "per_share"
            elif label:
                section = "money"
            kinds.append("header")
        elif all(YEAR.match(c.strip()) for c in nums) and not label.strip():
            kinds.append("header")  # the column-year row
        elif re.search(tp["shares_pattern"], label):
            kinds.append("shares")
        elif re.search(tp["per_share_pattern"], label):
            kinds.append("per_share")
        else:
            kinds.append(section)
    return kinds


def normalize_df(df: pd.DataFrame, page_text: str, tp: dict) -> tuple[pd.DataFrame, dict]:
    """Same grid as df; numeric cells replaced by scaled values. Column 0 (labels) untouched."""
    money_scale, shares_scale = detect_scales(page_text, tp)
    scale_for = {"money": money_scale, "shares": shares_scale, "per_share": 1}
    kinds = classify_rows(df, tp)
    out = df.copy().astype(object)

    for i, kind in enumerate(kinds):
        if kind == "header":
            continue
        for j in range(1, df.shape[1]):
            v, unit = to_number(df.iat[i, j])
            if v is None:
                continue
            val = v if unit == "pct" else v * scale_for[kind]
            out.iat[i, j] = int(val) if float(val).is_integer() else round(val, 6)

    out.insert(0, "row_kind", kinds)
    return out, {"money_scale": money_scale, "shares_scale": shares_scale}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

LOG_FIELDS = ["doc_id", "stem", "page", "statement", "decision", "method", "ruled",
              "ruling_lines", "order", "n_rows", "n_cols", "accuracy", "whitespace",
              "coverage", "label_merge", "score", "money_scale", "shares_scale", "bbox", "raw_csv", "norm_csv"]
BAKEOFF_FIELDS = ["doc_id", "stem", "page", "statement", "method", "n_tables", "n_rows",
                  "n_cols", "accuracy", "whitespace_raw", "whitespace", "coverage",
                  "label_merge", "score", "valid_shape", "seconds", "error"]


def write_csv(rows: list[dict], fields: list[str], path: Path) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Statement tables: bake-off + hybrid extractor")
    parser.add_argument("--input", default="data/rendered",
                        help="Folder of PDFs, or one PDF (default: data/rendered)")
    parser.add_argument("--output", default="data/tables",
                        help="Output folder (default: data/tables)")
    parser.add_argument("--params", default="params.yaml")
    parser.add_argument("--manifest", default=None,
                        help="manifest.csv (default: <input>/manifest.csv)")
    args = parser.parse_args()

    tp = load_params(args.params)["tables"]
    input_path, out_dir = Path(args.input), Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = Path(args.manifest) if args.manifest else (
        input_path / "manifest.csv" if input_path.is_dir()
        else input_path.parent / "manifest.csv")
    manifest = load_manifest(manifest_path)

    pdfs = find_pdfs(input_path)
    if not pdfs:
        print(f"[ERROR] No PDFs found in {input_path}")
        return

    log_rows, bakeoff_rows = [], []
    for pdf_path in pdfs:
        stem = pdf_path.stem
        doc_id = manifest.get(stem, stem)
        hits = scan_statement_pages(pdf_path, tp)
        print(f"[INFO] {pdf_path.name}: {len(hits)} statement page(s) "
              f"{[(h['page'], h['statement']) for h in hits]}")

        for hit in hits:
            if tp.get("run_bakeoff", True):
                bakeoff_rows += bakeoff_page(pdf_path, hit, tp, doc_id, stem)

            res = extract_best(pdf_path, hit, tp)
            row = {"doc_id": doc_id, "stem": stem, "page": hit["page"],
                   "statement": hit["statement"], "decision": res["decision"],
                   "ruled": res["ruled"], "ruling_lines": hit["ruling_lines"],
                   "order": ">".join(res["order"])}

            if res["decision"] != "no_valid_table":
                base = f"{stem}_p{hit['page']:04d}_{hit['statement']}"
                raw_path, norm_path = out_dir / f"{base}.raw.csv", out_dir / f"{base}.norm.csv"
                res["df"].to_csv(raw_path, index=False, header=False, encoding="utf-8",
                                 lineterminator="\n")
                norm, scales = normalize_df(res["df"], hit["text"], tp)
                norm.to_csv(norm_path, index=False, header=False, encoding="utf-8",
                            lineterminator="\n")
                row.update({
                    "method": f"camelot-{res['flavor']}",
                    "n_rows": res["n_rows"], "n_cols": res["n_cols"],
                    "accuracy": res["accuracy"], "whitespace": res["whitespace"],
                    "coverage": res["coverage"], "label_merge": res["label_merge"],
                    "score": res["score"], **scales,
                    "bbox": json.dumps(bbox_top_left(res["table"], hit["width"], hit["height"])),
                    "raw_csv": raw_path.name, "norm_csv": norm_path.name,
                })

            print(f"       p{hit['page']:04d} {hit['statement']:<22} ruled={res['ruled']} "
                  f"-> {row['decision']} {row.get('method', '')} "
                  f"{row.get('n_rows', '')}x{row.get('n_cols', '')} score={row.get('score', '')}")
            log_rows.append(row)

    write_csv(log_rows, LOG_FIELDS, out_dir / "tables_log.csv")
    if bakeoff_rows:
        write_csv(bakeoff_rows, BAKEOFF_FIELDS, out_dir / "bakeoff.csv")
    print(f"[INFO] {len(log_rows)} statement table(s) -> {out_dir}")
    print("[INFO] Table extraction complete")


if __name__ == "__main__":
    main()