import pandas as pd
import streamlit as st

from lantern_api import get, page_with_boxes

st.set_page_config(page_title="Explorer", layout="wide")
st.title("Document Explorer")

filings = get("/filings") or []
if not filings:
    st.warning("No filings found.")
    st.stop()
by_stem = {f["stem"]: f for f in filings}

stem = st.sidebar.selectbox("Filing", list(by_stem))
page = int(st.sidebar.number_input("Page", 1, max(by_stem[stem]["n_pages"], 1), 1))

recs = get(f"/filings/{stem}/pages/{page}/records") or []
types = sorted({r.get("block_type") for r in recs if r.get("block_type")})
show = st.sidebar.multiselect("Block types", types, default=types)
recs = [r for r in recs if r.get("block_type") in show]
pick = st.sidebar.selectbox("Highlight block", ["(none)"] + [r.get("block_id") for r in recs])
highlight = None if pick == "(none)" else pick

left, right = st.columns([3, 2])
img = page_with_boxes(stem, page, recs, highlight)
if img is None:
    left.warning("No rendered PDF page available.")
else:
    left.image(img)
    left.caption("Box colors: Text blue, Title red, List green, Table orange, "
                 "Figure purple, Footnote brown. Selected block: thick red.")

right.subheader(f"{len(recs)} records on page {page}")
keys = ["block_id", "block_type", "section", "text", "extractor", "ocr", "ocr_conf"]
df = pd.DataFrame([{k: r.get(k) for k in keys} for r in recs])
if not df.empty:
    df["text"] = df["text"].fillna("").astype(str).str.slice(0, 120)
    right.dataframe(df.astype(str), hide_index=True)
if highlight:
    right.subheader("JSONL record")
    right.json(next(r for r in recs if r.get("block_id") == highlight))
