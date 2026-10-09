"""
Part 9 - Drift signal between two pipeline versions

Compares the distribution of layout block lengths (words per block) produced by two
versions of the pipeline, e.g. Part 3 before and after text snapping.

Usage:
    python src/plot_drift.py --a /tmp/layout_nosnap --b data/layout \
        --labels "before text snapping" "after text snapping" \
        --out reports/plots/drift.png

Writes the plot and prints a small summary (also saved next to the plot as .json).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt


def block_lengths(folder: Path) -> list[int]:
    lengths = []
    for f in sorted(folder.glob("*.blocks.jsonl")):
        for line in f.read_text(encoding="utf-8").splitlines():
            if line.strip():
                lengths.append(len(json.loads(line).get("text", "").split()))
    return lengths


def summary(lengths: list[int]) -> dict:
    n = len(lengths)
    s = sorted(lengths)
    return {"blocks": n,
            "median_words": s[n // 2] if n else 0,
            "share_under_5_words": round(sum(1 for x in s if x < 5) / n, 4) if n else 0,
            "total_words": sum(s)}


def main() -> None:
    ap = argparse.ArgumentParser(description="Block-length drift between two pipeline versions")
    ap.add_argument("--a", required=True, help="layout output folder, version A")
    ap.add_argument("--b", required=True, help="layout output folder, version B")
    ap.add_argument("--labels", nargs=2, default=["version A", "version B"])
    ap.add_argument("--out", default="reports/plots/drift.png")
    args = ap.parse_args()

    la, lb = block_lengths(Path(args.a)), block_lengths(Path(args.b))
    sa, sb = summary(la), summary(lb)

    bins = [0, 1, 2, 3, 5, 10, 20, 50, 100, 200, 500, 2000]
    fig, ax = plt.subplots(figsize=(9, 4.5))
    ax.hist([la, lb], bins=bins, label=[f"{args.labels[0]} ({sa['blocks']} blocks)",
                                        f"{args.labels[1]} ({sb['blocks']} blocks)"])
    ax.set_xscale("log")
    ax.set_xlabel("words per block (log scale)")
    ax.set_ylabel("number of blocks")
    ax.set_title("Layout block length distribution, both filings")
    ax.legend()
    fig.tight_layout()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=150)
    result = {args.labels[0]: sa, args.labels[1]: sb}
    out.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    print(f"[INFO] plot -> {out}")


if __name__ == "__main__":
    main()