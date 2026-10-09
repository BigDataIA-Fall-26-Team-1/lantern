"""
Part 11 - XBRL extraction and validation

Usage (DVC stage):
    python src/xbrl.py

Reads every filing's inline XBRL with Arelle, then checks each number in the extracted
statement tables against the filing's own tagged facts, for both table paths:
    traditional  data/tables/{stem}_p{NNNN}_{statement}.norm.csv  (Part 2, already scaled)
    docling      data/docling/{stem}_pdf_p{NNNN}_t*.csv            (Part 4, scaled here with Part 2's normalizer)

Outputs:
    {out}/facts.csv        every numeric fact: concept, value, period, unit, decimals, dimensions
    {out}/comparison.csv   one row per extracted number and path: concept, XBRL value, status
    {out}/summary.json     match rate per path and statement

Label -> concept mapping, in order (the method is recorded for every line):
    1. curated     config/label_map.yaml (dimensional lines such as Products/Services,
                   and labels printed twice such as "Marketable securities")
    2. linkbase    the filing's own label linkbase (all label roles)
    3. fuzzy       difflib match against the same labels (cutoff in params.yaml)

Status of each number:
    match           equal within the tolerance implied by the fact's decimals
    match_negated   equal in size, printed with a negated label (e.g. an outflow in parentheses)
    sign            same size, opposite sign
    scale_xN        off by exactly a factor of N (1e3, 1e6, ...)
    mismatch        different value
    xbrl_missing    concept found, but no fact for that period and dimensions
    pdf_missing     a line the other path extracted and validated, missing from this path
    unmapped        no concept found for the label

Arelle reports a period ending September 27 as midnight on September 28, so one day is
subtracted from every end date before matching.
"""

from __future__ import annotations

import argparse
import csv
import difflib
import json
import re
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path

import pandas as pd
import yaml

from evaluate import norm_label
from tables import clean_df, normalize_df

YEAR = re.compile(r"\b(19|20)\d{2}\b")
DURATION_STATEMENTS = {"income", "comprehensive_income", "cash_flows", "equity"}
SCALES = (1e3, 1e6, 1e9, 1e-3, 1e-6, 1e-9)


# ---------------------------------------------------------------------------
# 1. facts from inline XBRL (Arelle)
# ---------------------------------------------------------------------------

def find_ixbrl(unpacked: Path) -> Path | None:
    """The main iXBRL document is the .htm that contains an ix:header."""
    for p in sorted(unpacked.glob("*.htm")):
        with open(p, encoding="utf-8", errors="ignore") as f:
            if "ix:header" in f.read(400_000):
                return p
    return None


def load_facts(htm: Path) -> tuple[list[dict], dict, dict[str, str]]:
    """
    Load one filing with Arelle. Returns
      facts:   numeric facts as dicts
      labels:  concept -> [(label role, label text)] from the filing's label linkbase
      periods: concept -> 'instant' or 'duration'
    """
    from arelle import Cntlr, XbrlConst

    cntlr = Cntlr.Cntlr(logFileName="logToBuffer")
    model = cntlr.modelManager.load(str(htm))
    if model is None:
        raise SystemExit(f"[ERROR] Arelle could not load {htm}")

    facts, labels, periods = [], {}, {}
    seen = set()
    label_rels = model.relationshipSet(XbrlConst.conceptLabel)
    for f in model.facts:
        if not getattr(f, "isNumeric", False) or f.isNil or f.context is None:
            continue
        c = f.context
        concept = str(f.qname)
        end = c.endDatetime - timedelta(days=1) if c.endDatetime else None
        start = c.startDatetime if not c.isInstantPeriod else None
        dims = {}
        for dim_qn, dv in c.qnameDims.items():
            member = getattr(dv, "memberQname", None)
            dims[str(dim_qn)] = str(member) if member is not None else str(getattr(dv, "typedMember", ""))
        try:
            value = float(f.xValue)
        except (TypeError, ValueError):
            continue
        key = (concept, json.dumps(dims, sort_keys=True), start, end, f.unitID, value)
        if key in seen:                       # the same fact tagged again elsewhere in the filing
            continue
        seen.add(key)
        facts.append({"concept": concept, "value": value, "unit": f.unitID,
                      "decimals": f.decimals, "instant": bool(c.isInstantPeriod),
                      "start": start.date().isoformat() if start else "",
                      "end": end.date().isoformat() if end else "",
                      "dims": json.dumps(dims, sort_keys=True)})
        if concept not in labels and f.concept is not None:
            labels[concept] = [(rel.toModelObject.role or "", rel.toModelObject.text or "")
                               for rel in label_rels.fromModelObject(f.concept)]
            periods[concept] = f.concept.periodType or ""
    cntlr.close()
    return facts, labels, periods


def fiscal_year_ends(facts: list[dict]) -> dict[str, str]:
    """year -> fiscal year end date, from the one-year (about 52 or 53 week) durations."""
    ends: Counter = Counter()
    for f in facts:
        if f["instant"] or not f["start"] or f["dims"] != "{}":
            continue
        days = (pd.Timestamp(f["end"]) - pd.Timestamp(f["start"])).days
        if 350 <= days <= 380:
            ends[f["end"]] += 1
    out = {}
    for end, _ in ends.most_common():
        out.setdefault(end[:4], end)
    return out


def index_facts(facts: list[dict]) -> dict[tuple, dict]:
    """(concept, dims, end, instant) -> fact; durations must be about one year."""
    idx = {}
    for f in facts:
        if not f["instant"]:
            days = (pd.Timestamp(f["end"]) - pd.Timestamp(f["start"])).days if f["start"] else 0
            if not 350 <= days <= 380:
                continue
        idx[(f["concept"], f["dims"], f["end"], f["instant"])] = f
    return idx


# ---------------------------------------------------------------------------
# 2. label -> concept
# ---------------------------------------------------------------------------

class LabelMapper:
    """
    Maps a printed label to a concept. Ambiguity is resolved by period type first:
    a balance sheet line is a balance at a date (instant), a line in a flow statement
    is a change over the year (duration). The same text, e.g. "Accounts payable",
    is a balance on the balance sheet and a change in the cash flow statement.
    """

    def __init__(self, labels: dict, periods: dict, curated: dict, fuzzy_cutoff: float):
        self.curated = curated
        self.periods = periods
        self.cutoff = fuzzy_cutoff
        self.by_label: dict[str, set] = defaultdict(set)
        self.negated: set[tuple[str, str]] = set()   # label text used with a negated label role
        for concept, roles in labels.items():
            for role, text in roles:
                lab = norm_label(text)
                if lab:
                    self.by_label[lab].add(concept)
                    if "negated" in role.lower():
                        self.negated.add((lab, concept))

    def _pick(self, hits: set, statement: str) -> list[str]:
        want = "instant" if statement == "balance" else "duration"
        same = [c for c in hits if self.periods.get(c) == want]
        return same if same else sorted(hits)

    def map(self, statement: str, label: str, occurrence: int) -> dict:
        lab = norm_label(label)
        cur = (self.curated.get(statement) or {})
        entry = cur.get(f"{lab}|{occurrence}") or cur.get(lab)
        if entry:
            if not isinstance(entry, dict):
                entry = {"concept": entry}
            concepts = entry["concept"] if isinstance(entry["concept"], list) else [entry["concept"]]
            return {"concepts": concepts, "dims": json.dumps(entry.get("dims", {}), sort_keys=True),
                    "negatable": entry.get("negate", any((lab, c) in self.negated for c in concepts)),
                    "method": "curated"}
        hits = self.by_label.get(lab, set())
        method = "linkbase"
        if not hits:
            close = difflib.get_close_matches(lab, list(self.by_label), n=1, cutoff=self.cutoff)
            if close:
                lab, hits, method = close[0], self.by_label[close[0]], f"fuzzy:{close[0]}"
        picked = self._pick(hits, statement) if hits else []
        if len(picked) == 1:
            c = picked[0]
            return {"concepts": [c], "dims": "{}", "negatable": (lab, c) in self.negated, "method": method}
        if len(picked) > 1:
            return {"concepts": [], "dims": "{}", "negatable": False,
                    "method": "ambiguous:" + "|".join(picked)}
        return {"concepts": [], "dims": "{}", "negatable": False, "method": "none"}


# ---------------------------------------------------------------------------
# 3. extracted tables -> (label, occurrence, year, value)
# ---------------------------------------------------------------------------

def cells_from_norm(rows: list[list]) -> list[tuple[str, int, str, float]]:
    """
    rows: a normalized grid with row_kind in column 0 and the label in column 1
    (the .norm.csv format of Part 2). Years come from the header rows.
    """
    col_year = {}
    for row in rows:
        if str(row[0]) == "header":
            for j, cell in enumerate(row[2:], start=2):
                m = list(YEAR.finditer(str(cell)))
                if m:
                    col_year[j] = m[-1].group(0)
    seen: Counter = Counter()
    out = []
    for row in rows:
        if str(row[0]) == "header" or len(row) < 3:
            continue
        label = str(row[1]).strip()
        if not label:
            continue
        lab = norm_label(label)
        seen[lab] += 1
        for j, year in col_year.items():
            if j < len(row):
                try:
                    out.append((label, seen[lab], year, float(row[j])))
                except (TypeError, ValueError):
                    pass
    return out


def traditional_cells(path: Path) -> list:
    with open(path, encoding="utf-8", newline="") as f:
        return cells_from_norm(list(csv.reader(f)))


def docling_cells(paths: list[Path], page_text: str, tp: dict) -> list:
    """Docling tables are raw; scale them with Part 2's normalizer and the page caption."""
    out = []
    for p in paths:
        with open(p, encoding="utf-8", newline="") as f:
            grid = list(csv.reader(f))
        if not grid:
            continue
        width = max(len(r) for r in grid)
        df = clean_df(pd.DataFrame([r + [""] * (width - len(r)) for r in grid]))
        if df.empty or df.shape[1] < 2:
            continue
        norm, _ = normalize_df(df, page_text, tp)
        out += cells_from_norm([[str(v) for v in r] for r in norm.values.tolist()])
    return out


# ---------------------------------------------------------------------------
# 4. compare
# ---------------------------------------------------------------------------

def tolerance(decimals) -> float:
    try:
        return 0.5 * 10 ** (-int(decimals))
    except (TypeError, ValueError):          # 'INF' or missing: exact
        return 1e-9


def classify(value: float, fact: dict, negatable: bool) -> tuple[str, float]:
    """
    Plain comparison first. Only if that fails and the line is printed with a negated
    label (e.g. cash outflows shown in parentheses while XBRL stores a positive amount)
    is the opposite sign accepted as a match.
    """
    x = fact["value"]
    tol = tolerance(fact["decimals"])
    if abs(value - x) <= tol:
        return "match", value - x
    if abs(value + x) <= tol:
        return ("match_negated" if negatable else "sign"), value + x
    for k in SCALES:
        if x and abs(value - x * k) <= max(tol * k, tol):
            return f"scale_x{k:g}", value - x
    return "mismatch", value - x


def prior_year_end(year: str, fy_end: dict, facts_by_concept: dict, concept: str) -> str:
    prev = str(int(year) - 1)
    if prev in fy_end:
        return fy_end[prev]
    ends = sorted(f["end"] for f in facts_by_concept.get(concept, []) if f["instant"] and f["end"][:4] == prev)
    return ends[-1] if ends else ""


def compare(cells, statement, stem, page, path_name, mapper, idx, fy_end, facts_by_concept) -> list[dict]:
    rows = []
    for label, occ, year, value in cells:
        m = mapper.map(statement, label, occ)
        base = {"stem": stem, "path": path_name, "page": page, "statement": statement,
                "label": label, "occurrence": occ, "year": year, "value": value,
                "concept": "", "dims": m["dims"], "map_method": m["method"],
                "period_end": "", "xbrl_value": "", "diff": ""}
        if not m["concepts"]:
            rows.append({**base, "status": "unmapped"})
            continue
        fact, concept, end = None, m["concepts"][0], ""
        for c in m["concepts"]:                      # several = concept changed between filings
            instant = mapper.periods.get(c) == "instant"
            if instant and "beginning" in label.lower():
                end = prior_year_end(year, fy_end, facts_by_concept, c)
            else:
                end = fy_end.get(year, "")
            fact = idx.get((c, m["dims"], end, instant))
            if fact:
                concept = c
                break
        base.update({"concept": concept, "period_end": end})
        if fact is None:
            rows.append({**base, "status": "xbrl_missing"})
            continue
        status, diff = classify(value, fact, m["negatable"])
        rows.append({**base, "status": status, "xbrl_value": fact["value"], "diff": diff})
    return rows


def add_pdf_missing(rows: list[dict], paths: list[str]) -> list[dict]:
    """
    Expected lines = every (filing, statement, concept, dimensions, period) that at least one
    path extracted and found in XBRL. A path that lacks such a line gets a pdf_missing row,
    so a lost row counts against that path's match rate.
    """
    found: dict[str, set] = defaultdict(set)
    example: dict[tuple, dict] = {}
    for r in rows:
        if r["concept"] and r["status"] not in ("unmapped", "xbrl_missing"):
            key = (r["stem"], r["statement"], r["concept"], r["dims"], r["period_end"])
            found[r["path"]].add(key)
            example.setdefault(key, r)
    missing = []
    for path_name in paths:
        for key in sorted(set(example) - found[path_name]):
            r = example[key]
            missing.append({**r, "path": path_name, "value": "", "diff": "",
                            "map_method": f"found by {r['path']} only", "status": "pdf_missing"})
    return rows + missing


def summarize(rows: list[dict]) -> dict:
    out: dict = defaultdict(dict)
    groups = defaultdict(list)
    for r in rows:
        groups[(r["path"], r["statement"])].append(r)
        groups[(r["path"], "ALL")].append(r)
    for (path_name, statement), rs in sorted(groups.items()):
        n = len(rs)
        counts = Counter(r["status"] for r in rs)
        mapped = n - counts["unmapped"]
        out[path_name][statement] = {
            "numbers": n, "mapped": mapped, "match": counts["match"] + counts["match_negated"],
            "match_rate": round((counts["match"] + counts["match_negated"]) / n, 4) if n else None,
            "match_rate_of_mapped": (round((counts["match"] + counts["match_negated"]) / mapped, 4)
                                     if mapped else None),
            "status_counts": dict(counts)}
    return dict(out)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def write_csv(rows: list[dict], path: Path) -> None:
    if not rows:
        return
    fields = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def main() -> None:
    ap = argparse.ArgumentParser(description="XBRL extraction and validation (Part 11)")
    ap.add_argument("--raw", default="data/raw")
    ap.add_argument("--rendered", default="data/rendered")
    ap.add_argument("--tables", default="data/tables")
    ap.add_argument("--docling", default="data/docling")
    ap.add_argument("--parsed", default="data/parsed")
    ap.add_argument("--out", default="data/xbrl")
    ap.add_argument("--params", default="params.yaml")
    args = ap.parse_args()

    params = yaml.safe_load(open(args.params, encoding="utf-8"))
    xp, tp = params["xbrl"], params["tables"]
    curated = yaml.safe_load(open(xp["label_map"], encoding="utf-8")) or {}
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    with open(Path(args.rendered) / "manifest.csv", encoding="utf-8", newline="") as f:
        stems = {r["accession"]: r["stem"] for r in csv.DictReader(f)}
    with open(Path(args.tables) / "tables_log.csv", encoding="utf-8", newline="") as f:
        table_log = [r for r in csv.DictReader(f) if r.get("norm_csv")]

    all_facts, all_rows = [], []
    for unpacked in sorted(Path(args.raw).glob("sec-edgar-filings/*/*/*/unpacked")):
        accession = unpacked.parent.name
        stem = stems.get(accession)
        if not stem:
            continue
        htm = find_ixbrl(unpacked)
        if htm is None:
            print(f"[WARN] {stem}: no iXBRL document in {unpacked}")
            continue
        print(f"[INFO] {stem}: loading {htm.name} with Arelle")
        facts, labels, periods = load_facts(htm)
        fy_end = fiscal_year_ends(facts)
        idx = index_facts(facts)
        facts_by_concept = defaultdict(list)
        for f in facts:
            facts_by_concept[f["concept"]].append(f)
        mapper = LabelMapper(labels, periods, curated, xp["fuzzy_cutoff"])
        print(f"       {len(facts)} numeric facts; fiscal year ends {fy_end}")
        all_facts += [{"stem": stem, **f} for f in facts]

        for t in (r for r in table_log if r["stem"] == stem):
            statement, page = t["statement"], int(t["page"])
            if statement not in xp["statements"]:
                continue
            trad = traditional_cells(Path(args.tables) / t["norm_csv"])
            all_rows += compare(trad, statement, stem, page, "traditional", mapper, idx, fy_end,
                                facts_by_concept)

            dpaths = sorted(Path(args.docling).glob(f"{stem}_pdf_p{page:04d}_t*.csv"))
            text_path = Path(args.parsed) / f"{stem}_p{page:04d}.txt"
            page_text = text_path.read_text(encoding="utf-8") if text_path.exists() else ""
            doc = docling_cells(dpaths, page_text, tp)
            all_rows += compare(doc, statement, stem, page, "docling", mapper, idx, fy_end,
                                facts_by_concept)

    write_csv(all_facts, out / "facts.csv")
    all_rows = add_pdf_missing(all_rows, ["traditional", "docling"])
    write_csv(all_rows, out / "comparison.csv")
    summary = summarize(all_rows)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    for path_name, stmts in summary.items():
        for statement, s in stmts.items():
            print(f"  {path_name:<12} {statement:<22} {s['match']}/{s['numbers']} match "
                  f"({s['match_rate']}), {s['status_counts']}")
    print(f"[INFO] {len(all_facts)} facts, {len(all_rows)} compared numbers -> {out}")


if __name__ == "__main__":
    main()