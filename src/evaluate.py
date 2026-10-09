"""
Part 9 - Evaluation against hand-made ground truth

Usage (DVC stage):
    python src/evaluate.py

Fixtures only (CI / tests):
    python src/evaluate.py --gt tests/fixtures/gt --parsed /tmp/parsed --tables /tmp/tables \
        --layout none --docling none --out /tmp/eval

Ground truth (see docs/ground_truth_conventions.md):
    {gt}/pages.csv                                   stem,page,stratum[,transcriber,method]
    {gt}/text/{stem}_p{NNNN}.txt                     page text, as printed
    {gt}/tables/{stem}_p{NNNN}_{statement}.csv       row_label,column,value (as printed)
    {gt}/tables/{stem}_p{NNNN}_{statement}.keyer1.csv / .keyer2.csv   (optional, for agreement)

Predictions compared (each optional; a missing source is skipped):
    pdfplumber  {parsed}/{stem}_p{NNNN}.txt                      Part 1 text
    layout      {layout}/{stem}.blocks.jsonl                     Part 3 blocks in reading order
    docling     {docling}/{stem}_pdf_p{NNNN}.md                  Part 4 per-page Markdown
    tables      {tables}/{stem}_p{NNNN}_{statement}.raw.csv      Part 2 chosen table (traditional)
                {docling}/{stem}_pdf_p{NNNN}_t*.csv              Part 4 tables on that page

Optional fixture strata (for page types the filings do not have):
    --fixtures-gt tests/fixtures/gt --fixtures-parsed data/fixtures/parsed
    --fixtures-layout data/fixtures/layout --fixtures-docling data/fixtures/docling
    Only the strata in --fixture-strata (default: scanned, multicolumn) are scored, read
    straight from the fixture answer keys; the statement fixture is FY2025 p32, which the
    filing pages already cover. Off by default, so CI and the tests are unaffected.

Outputs:
    {out}/metrics.json      summary per path, per stratum, per table source (DVC metric)
    {out}/eval_pages.csv    one row per page and text source
    {out}/eval_tables.csv   one row per table and source

Normalization (applied to ground truth and predictions alike): Unicode NFKC, curly quotes
to straight, every dash variant to '-', '$' separated from the number, whitespace collapsed,
lowercase. Punctuation is KEPT: stripping it would hide sign errors such as (321) -> 321.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

from tables import to_number

YEAR = re.compile(r"\b(19|20)\d{2}\b")
NUMERIC = re.compile(r"\(?\$?\d[\d,]*\.?\d*\)?%?")
DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2015\u2212"), "-")
QUOTES = {ord("\u2018"): "'", ord("\u2019"): "'", ord("\u201c"): '"', ord("\u201d"): '"'}


# ---------------------------------------------------------------------------
# text normalization and error rates
# ---------------------------------------------------------------------------

def normalize_text(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").translate(DASHES).translate(QUOTES)
    s = s.replace("$", " $ ")
    return " ".join(s.lower().split())


def _edit_distance(ref: list, hyp: list) -> int:
    """Levenshtein distance between two token lists (fallback when jiwer is missing)."""
    prev = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        cur = [i] + [0] * len(hyp)
        for j, h in enumerate(hyp, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (r != h))
        prev = cur
    return prev[-1]


def wer_cer(ref: str, hyp: str) -> tuple[float, float]:
    """Word and character error rate of hyp against ref (both already normalized)."""
    if not ref:
        return (0.0, 0.0) if not hyp else (1.0, 1.0)
    try:
        import jiwer
        return float(jiwer.wer(ref, hyp or " ")), float(jiwer.cer(ref, hyp or " "))
    except ImportError:
        w = _edit_distance(ref.split(), hyp.split()) / len(ref.split())
        c = _edit_distance(list(ref), list(hyp)) / len(ref)
        return w, c


def numeric_recall(ref: str, hyp: str) -> float | None:
    """Share of the page's numbers (as printed, sign included) found in the prediction."""
    r = Counter(NUMERIC.findall(ref))
    if not r:
        return None
    h = Counter(NUMERIC.findall(hyp))
    return sum(min(n, h[t]) for t, n in r.items()) / sum(r.values())


# ---------------------------------------------------------------------------
# reading predictions
# ---------------------------------------------------------------------------

def md_to_text(md: str) -> str:
    """Markdown from Docling to plain text: drop markup, keep the words and numbers."""
    out = []
    for line in md.splitlines():
        line = re.sub(r"<!--.*?-->", " ", line)
        if re.fullmatch(r"\s*\|?[\s:|-]+\|?\s*", line):      # table separator |---|---|
            continue
        line = re.sub(r"^\s*#+\s*", "", line)                  # headings
        line = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", line)      # images
        line = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", line)   # links -> text
        line = line.replace("|", " ").replace("**", "").replace("__", "")
        out.append(line)
    return "\n".join(out)


def read_text_sources(stem: str, page: int, dirs: dict) -> dict[str, str]:
    preds = {}
    if dirs.get("parsed"):
        p = Path(dirs["parsed"]) / f"{stem}_p{page:04d}.txt"
        if p.exists():
            preds["pdfplumber"] = p.read_text(encoding="utf-8")
    if dirs.get("layout"):
        p = Path(dirs["layout"]) / f"{stem}.blocks.jsonl"
        if p.exists():
            recs = [json.loads(line) for line in p.read_text(encoding="utf-8").splitlines() if line]
            recs = sorted((r for r in recs if r["page"] == page), key=lambda r: r["order"])
            if recs:
                preds["layout"] = "\n".join(r["text"] for r in recs)
    if dirs.get("docling"):
        p = Path(dirs["docling"]) / f"{stem}_pdf_p{page:04d}.md"
        if p.exists():
            preds["docling"] = md_to_text(p.read_text(encoding="utf-8"))
    return preds


# ---------------------------------------------------------------------------
# tables: everything becomes (label, year, occurrence) -> value
# ---------------------------------------------------------------------------

def norm_label(s: str) -> str:
    s = normalize_text(s).rstrip(":").strip()
    return re.sub(r"\s*\(\d\)$", "", s)          # trailing footnote marker such as (1)


def keyed(rows: list[tuple[str, str, str]]) -> dict[tuple, float]:
    """
    (label, year, value) rows in page order -> {(label, year, occurrence): number}.
    A label printed twice (e.g. 'Products' under Net sales and under Cost of sales)
    gets occurrence 1 and 2, so no section prefix is needed.
    """
    count: Counter = Counter()
    out = {}
    for label, year, value in rows:
        lab = norm_label(label)
        count[(lab, year)] += 1
        v = to_number(value)[0]
        if v is not None:
            out[(lab, year, count[(lab, year)])] = v
    return out


def read_gt_table(path: Path) -> list[tuple[str, str, str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return [(r["row_label"], str(r["column"]).strip(), r["value"]) for r in csv.DictReader(f)]


def grid_to_rows(grid: list[list[str]]) -> list[tuple[str, str, str]]:
    """
    An extracted table grid -> (label, year, value) rows.
    Year columns come from the first rows that contain a year; the label is the first
    cell of each row. Rows without a label or without values are skipped.
    """
    col_year: dict[int, str] = {}
    for row in grid[:6]:
        for j, cell in enumerate(row):
            m = list(YEAR.finditer(str(cell)))
            if m and j > 0:
                col_year[j] = m[-1].group(0)
    rows = []
    for row in grid:
        if not row:
            continue
        label = str(row[0]).strip()
        if not label or YEAR.fullmatch(label):
            continue
        for j, year in col_year.items():
            if j < len(row) and to_number(str(row[j]))[0] is not None and not YEAR.fullmatch(str(row[j]).strip()):
                rows.append((label, year, str(row[j])))
    return rows


def read_grid(path: Path) -> list[list[str]]:
    with open(path, encoding="utf-8", newline="") as f:
        return [row for row in csv.reader(f)]


def prf(gt: dict, pred: dict) -> dict:
    tp = sum(1 for k, v in pred.items() if k in gt and abs(gt[k] - v) < 1e-9)
    p = tp / len(pred) if pred else 0.0
    r = tp / len(gt) if gt else 0.0
    f = 2 * p * r / (p + r) if p + r else 0.0
    return {"precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4),
            "tp": tp, "cells_gt": len(gt), "cells_pred": len(pred)}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def score_text_pages(gt: Path, pages: list[dict], dirs: dict) -> list[dict]:
    """WER, CER and numeric recall for every listed page and every available text source."""
    rows = []
    for pg in pages:
        ref_path = gt / "text" / f"{pg['stem']}_p{pg['page']:04d}.txt"
        if not ref_path.exists():
            print(f"[WARN] no ground truth text for {ref_path}, skipped")
            continue
        ref = normalize_text(ref_path.read_text(encoding="utf-8"))
        for source, hyp_raw in read_text_sources(pg["stem"], pg["page"], dirs).items():
            hyp = normalize_text(hyp_raw)
            w, c = wer_cer(ref, hyp)
            rows.append({**pg, "source": source, "wer": round(w, 4), "cer": round(c, 4),
                         "numeric_recall": numeric_recall(ref, hyp),
                         "ref_words": len(ref.split()), "hyp_words": len(hyp.split())})
    return rows


def mean(xs: list[float]) -> float | None:
    xs = [x for x in xs if x is not None]
    return round(sum(xs) / len(xs), 4) if xs else None


def opt_dir(v: str | None) -> str | None:
    return None if v in (None, "", "none") else v


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate extraction against ground truth (Part 9)")
    ap.add_argument("--gt", default="data/ground_truth")
    ap.add_argument("--parsed", default="data/parsed")
    ap.add_argument("--layout", default="data/layout")
    ap.add_argument("--docling", default="data/docling")
    ap.add_argument("--tables", default="data/tables")
    ap.add_argument("--out", default="reports")
    ap.add_argument("--fixtures-gt", default="none", help="fixture answer keys (off by default)")
    ap.add_argument("--fixtures-parsed", default="none")
    ap.add_argument("--fixtures-layout", default="none")
    ap.add_argument("--fixtures-docling", default="none")
    ap.add_argument("--fixture-strata", default="scanned,multicolumn")
    args = ap.parse_args()

    gt = Path(args.gt)
    dirs = {k: opt_dir(getattr(args, k)) for k in ("parsed", "layout", "docling", "tables")}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with open(gt / "pages.csv", encoding="utf-8", newline="") as f:
        pages = [{"stem": r["stem"], "page": int(r["page"]), "stratum": r["stratum"]}
                 for r in csv.DictReader(f)]

    # ---- text
    page_rows = score_text_pages(gt, pages, dirs)
    fx_gt = opt_dir(args.fixtures_gt)
    if fx_gt:
        keep = {x.strip() for x in args.fixture_strata.split(",") if x.strip()}
        with open(Path(fx_gt) / "pages.csv", encoding="utf-8", newline="") as f:
            fx_pages = [{"stem": r["stem"], "page": int(r["page"]), "stratum": r["stratum"]}
                        for r in csv.DictReader(f) if r["stratum"] in keep]
        fx_dirs = {"parsed": opt_dir(args.fixtures_parsed), "layout": opt_dir(args.fixtures_layout),
                   "docling": opt_dir(args.fixtures_docling)}
        page_rows += [{**r, "source_set": "fixture"} for r in score_text_pages(Path(fx_gt), fx_pages, fx_dirs)]

    # ---- tables
    table_rows, agreement = [], []
    tdir = gt / "tables"
    for gt_path in sorted(tdir.glob("*.csv")) if tdir.exists() else []:
        if ".keyer" in gt_path.name:
            continue
        m = re.match(r"(.+)_p(\d{4})_(\w+)\.csv$", gt_path.name)
        if not m:
            continue
        stem, page, statement = m.group(1), int(m.group(2)), m.group(3)
        truth = keyed(read_gt_table(gt_path))
        base = {"stem": stem, "page": page, "statement": statement}

        k1, k2 = (tdir / f"{gt_path.stem}.keyer1.csv"), (tdir / f"{gt_path.stem}.keyer2.csv")
        if k1.exists() and k2.exists():
            agreement.append({**base, "source": "keyer1_vs_keyer2",
                              **prf(keyed(read_gt_table(k1)), keyed(read_gt_table(k2)))})

        if dirs["tables"]:
            p = Path(dirs["tables"]) / f"{stem}_p{page:04d}_{statement}.raw.csv"
            pred = keyed(grid_to_rows(read_grid(p))) if p.exists() else {}
            table_rows.append({**base, "source": "traditional", **prf(truth, pred)})
        if dirs["docling"]:
            rows = []
            for p in sorted(Path(dirs["docling"]).glob(f"{stem}_pdf_p{page:04d}_t*.csv")):
                rows += grid_to_rows(read_grid(p))
            table_rows.append({**base, "source": "docling", **prf(truth, keyed(rows))})

    # ---- summary
    metrics: dict = {"text": {}, "text_by_stratum": defaultdict(dict), "tables": {}}
    for source in sorted({r["source"] for r in page_rows}):
        rs = [r for r in page_rows if r["source"] == source]
        metrics["text"][source] = {"wer": mean([r["wer"] for r in rs]),
                                   "cer": mean([r["cer"] for r in rs]),
                                   "numeric_recall": mean([r["numeric_recall"] for r in rs]),
                                   "pages": len(rs)}
        for stratum in sorted({r["stratum"] for r in rs}):
            ss = [r for r in rs if r["stratum"] == stratum]
            metrics["text_by_stratum"][stratum][source] = {
                "wer": mean([r["wer"] for r in ss]), "cer": mean([r["cer"] for r in ss]),
                "pages": len(ss)}
    for source in sorted({r["source"] for r in table_rows}):
        rs = [r for r in table_rows if r["source"] == source]
        tp, g, pr = (sum(r[k] for r in rs) for k in ("tp", "cells_gt", "cells_pred"))
        p, rc = (tp / pr if pr else 0.0), (tp / g if g else 0.0)
        metrics["tables"][source] = {"precision": round(p, 4), "recall": round(rc, 4),
                                     "f1": round(2 * p * rc / (p + rc), 4) if p + rc else 0.0,
                                     "tables": len(rs), "cells_gt": g}
    if agreement:
        metrics["keyer_agreement_f1"] = mean([a["f1"] for a in agreement])
    metrics["text_by_stratum"] = dict(metrics["text_by_stratum"])

    (out / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    for name, rows in (("eval_pages.csv", page_rows), ("eval_tables.csv", table_rows + agreement)):
        if rows:
            fields = list(dict.fromkeys(k for r in rows for k in r))
            with open(out / name, "w", encoding="utf-8", newline="") as f:
                w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
                w.writeheader()
                w.writerows(rows)

    print(json.dumps(metrics, indent=2))
    print(f"[INFO] {len(page_rows)} page/source rows, {len(table_rows)} table/source rows -> {out}")


if __name__ == "__main__":
    main()