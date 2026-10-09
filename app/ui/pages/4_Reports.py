import io
import json
import re
from pathlib import Path

import pandas as pd
import streamlit as st

from lantern_api import get, get_bytes

st.set_page_config(page_title="Reports", layout="wide")
st.title("Reports")
st.caption("The written deliverables and evidence files from each Part of the pipeline.")

# Known report files: (section, readable title, what it is)
KNOWN = {
    "tables_method.md": ("Part 2 · Tables", "Table extraction method",
                         "Bake-off of Camelot and pdfplumber on statement pages, and how the "
                         "hybrid extractor chooses a method for each table."),
    "layout_audit.md": ("Part 3 · Layout", "Layout detector audit",
                        "Correct, missed and wrong-type blocks per class on the audited pages."),
    "docling_comparison.md": ("Part 4 · Docling", "Docling vs traditional pipeline",
                              "Reading order, table structure, footnotes, provenance and throughput "
                              "compared, with the recommended primary path and fallback."),
    "format_decision.md": ("Part 6 · Formats", "Storage format decision",
                           "Which format is the source of truth and which feeds Case Study 2, and why."),
    "format_stats.csv": ("Part 6 · Formats", "Format sizes and token counts",
                         "Bytes, characters and approximate tokens (characters / 4) for JSONL, "
                         "Markdown and TXT exports."),
    "build_vs_buy.md": ("Part 7 · Build vs buy", "Build vs buy recommendation",
                        "AWS Textract compared with the open-source output, with pricing and "
                        "data-handling questions."),
    "eval.md": ("Part 9 · Evaluation", "Evaluation report",
                "Text and table accuracy for both parsing paths, and the regression tests."),
    "ground_truth_conventions.md": ("Part 9 · Evaluation", "Ground truth conventions",
                                    "How the ground-truth pages and tables were transcribed: "
                                    "dashes, negatives, footnote markers and line breaks."),
    "metrics.json": ("Part 9 · Evaluation", "Pipeline metrics",
                     "The metrics file written by the evaluate stage (what dvc metrics show reads)."),
    "benchmarks.md": ("Part 10 · Benchmarks", "Cost and throughput benchmarks",
                      "Time and memory per page by stage, and cost per 1,000 pages."),
    "xbrl.md": ("Part 11 · XBRL", "XBRL validation",
                "Extracted statement values checked against the filing's own XBRL facts."),
}
# Folders of supporting files: (prefix, section, title prefix, what it is)
PREFIXES = [
    ("layout/", "Part 3 · Layout", "Layout QA overlay",
     "Detected layout blocks drawn on a page image."),
    ("managed/", "Part 7 · Build vs buy", "Textract comparison data",
     "Evidence files behind the build vs buy report."),
    ("plots/", "Part 9 · Evaluation", "Plot", "Chart produced by the evaluation."),
]


def pretty(stem):
    return stem.replace("_", " ").replace("-", " ").strip().capitalize()


def describe(path):
    if path in KNOWN:
        return KNOWN[path]
    for prefix, section, title, desc in PREFIXES:
        if path.startswith(prefix):
            return section, f"{title}: {pretty(Path(path).stem)}", desc
    return "Other files", pretty(Path(path).stem), ""


def section_order(section):
    m = re.search(r"Part (\d+)", section)
    return int(m.group(1)) if m else 99


files = get("/reports") or []
if not files:
    st.info("No reports found.")
    st.stop()

entries = [(p, *describe(p)) for p in files]
sections = sorted({e[1] for e in entries}, key=section_order)
section = st.sidebar.selectbox("Section", sections)
items = [e for e in entries if e[1] == section]
i = st.sidebar.selectbox("Report", range(len(items)), format_func=lambda k: items[k][2])
path, _, title, desc = items[i]

st.subheader(title)
if desc:
    st.write(desc)
st.caption(f"Source file: reports/{path}")
st.divider()

data = get_bytes("/reports/file", path=path)
ext = Path(path).suffix.lower()
if data is None:
    st.warning("File not available.")
elif ext == ".md":
    st.markdown(data.decode("utf-8"))
elif ext == ".csv":
    st.dataframe(pd.read_csv(io.BytesIO(data)).astype(str), hide_index=True)
elif ext == ".json":
    st.json(json.loads(data))
elif ext == ".png":
    st.image(data)
