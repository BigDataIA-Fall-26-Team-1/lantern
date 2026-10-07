"""
Part 6 - Measure the three export formats and cut the same page slice in each,
for the LLM question test.

Usage (repo root):
    python -m src.format_stats
    python -m src.format_stats --stem AAPL_10K_20250927 --pages 31-34

Writes:
    reports/format_stats.csv                      bytes, characters, approx tokens (chars / 4)
                                                  per filing and format, plus the slice
    reports/format_test/{stem}_p{a}-{b}.jsonl     the slice as JSONL records
    reports/format_test/{stem}_p{a}-{b}.md        the slice as Markdown (cut from the exported .md)
    reports/format_test/{stem}_p{a}-{b}.txt       the slice as plain text (same rules as the export)
"""

from __future__ import annotations

import argparse
import csv
import json
import re
from pathlib import Path

import yaml

from src.export import to_text

COMMENT = re.compile(r"^<!-- \S+ p(\d+) \S+ -->$")


def measure(text: str) -> tuple[int, int, int]:
    data = text.encode("utf-8")
    return len(data), len(text), round(len(text) / 4)


def slice_markdown(md: str, lo: int, hi: int) -> str:
    """Keep the blocks on pages lo..hi: each provenance comment and what follows it.
    A section heading with no comment of its own is kept only if the next block is kept."""
    chunks = md.split("\n\n")
    out, pending, keep = [chunks[0]], [], False
    for c in chunks[1:]:
        m = COMMENT.match(c.strip())
        if m:
            keep = lo <= int(m.group(1)) <= hi
            if keep:
                out += pending
            pending = []
            if keep:
                out.append(c)
        elif c.startswith("## ") and not keep:
            pending = [c]
        elif keep:
            out.append(c)
    return "\n\n".join(out).rstrip("\n") + "\n"


def main() -> None:
    ap = argparse.ArgumentParser(description="Format sizes and the page slice for Part 6")
    ap.add_argument("--export", default="data/export")
    ap.add_argument("--stem", default="AAPL_10K_20250927")
    ap.add_argument("--pages", default="31-34", help="inclusive page range for the slice, e.g. 31-34")
    ap.add_argument("--params", default="params.yaml")
    ap.add_argument("--out", default="reports/format_test")
    ap.add_argument("--stats", default="reports/format_stats.csv")
    a = ap.parse_args()
    exp, out = Path(a.export), Path(a.out)
    lo, hi = (int(x) for x in a.pages.split("-"))
    ep = yaml.safe_load(open(a.params, encoding="utf-8"))["export"]
    footer, footer_line = re.compile(ep["footer_pattern"]), re.compile(ep["footer_line_pattern"])

    rows = []
    for jp in sorted(exp.glob("*.jsonl")):
        stem = jp.stem
        for fmt in ("jsonl", "md", "txt"):
            p = exp / f"{stem}.{fmt}"
            if p.exists():
                b, ch, tok = measure(p.read_text(encoding="utf-8"))
                rows.append({"stem": stem, "scope": "full document", "format": fmt,
                             "bytes": b, "chars": ch, "approx_tokens": tok})

    # the same slice in all three formats
    recs = [json.loads(l) for l in (exp / f"{a.stem}.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    part = [r for r in recs if lo <= r["page"] <= hi]
    if not part:
        raise SystemExit(f"no records on pages {lo}-{hi} of {a.stem}")
    out.mkdir(parents=True, exist_ok=True)
    name = f"{a.stem}_p{lo}-{hi}"
    texts = {
        "jsonl": "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in part),
        "md": slice_markdown((exp / f"{a.stem}.md").read_text(encoding="utf-8"), lo, hi),
        # "Item"-only fragments of a rejoined heading are left out, as in the full export
        "txt": to_text(part, footer, footer_line,
                       {r["block_id"] for r in part if re.fullmatch(r"\s*item\s*", r["text"] or "", re.I)}),
    }
    for fmt, text in texts.items():
        (out / f"{name}.{fmt}").write_text(text, encoding="utf-8", newline="\n")
        b, ch, tok = measure(text)
        rows.append({"stem": a.stem, "scope": f"pages {lo}-{hi}", "format": fmt,
                     "bytes": b, "chars": ch, "approx_tokens": tok})

    Path(a.stats).parent.mkdir(parents=True, exist_ok=True)
    with open(a.stats, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]), lineterminator="\n")
        w.writeheader()
        w.writerows(rows)

    print(f"{'stem':<20} {'scope':<15} {'format':<6} {'bytes':>10} {'chars':>10} {'~tokens':>9}  vs txt")
    base = {(r["stem"], r["scope"]): r["approx_tokens"] for r in rows if r["format"] == "txt"}
    for r in rows:
        ratio = r["approx_tokens"] / base[(r["stem"], r["scope"])] if base.get((r["stem"], r["scope"])) else 0
        print(f"{r['stem']:<20} {r['scope']:<15} {r['format']:<6} {r['bytes']:>10,} {r['chars']:>10,} "
              f"{r['approx_tokens']:>9,}  {ratio:.1f}x")
    print(f"\nslice files: {out}/{name}.jsonl / .md / .txt  ({len(part)} records)")
    print(f"stats: {a.stats}")


if __name__ == "__main__":
    main()
