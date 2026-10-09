import pandas as pd
import streamlit as st

from lantern_api import block_address, crop_around, get, page_with_boxes

st.set_page_config(page_title="Trace a fact", layout="wide")
st.title("Trace a fact")
st.caption("Search a value or phrase, then follow it to the page, the bbox, "
           "the JSONL record and the Markdown line with its provenance comment.")

stems = [f["stem"] for f in (get("/filings") or [])]
q = st.text_input("Search text and table cells", "net income")
scope = st.selectbox("Filing", ["(all)"] + stems)
params = {"q": q} if scope == "(all)" else {"q": q, "stem": scope}
hits = (get("/search", **params) or []) if q.strip() else []

st.write(f"{len(hits)} hits (max 50)")
if not hits:
    st.stop()
view = pd.DataFrame(hits).rename(columns={
    "stem": "Filing", "page": "Page", "block_type": "Type",
    "context": "Heading above the block", "snippet": "Where the match is"})
cols = ["Page", "Type", "Heading above the block", "Where the match is"]
if scope == "(all)":
    cols = ["Filing"] + cols
st.dataframe(view[cols].astype(str), hide_index=True)


def hit_label(i):
    x = hits[i]
    where = f"{x['stem']} · " if scope == "(all)" else ""
    return (f"{where}Page {x['page']} · {x['block_type']} · "
            f"{x.get('context') or '(no heading on page)'}")


h = hits[st.selectbox("Trace this hit", range(len(hits)), format_func=hit_label,
                      help="Each option is one block containing your search text, "
                           "labelled with the heading above it on that page.")]

t = get(f"/filings/{h['stem']}/records/{h['block_id']}")
rec = t["record"]
st.caption(f"Record address: {block_address(rec['block_id'])}. The same ID appears in the "
           "JSONL record and in the Markdown provenance comment below.")
page_recs = get(f"/filings/{h['stem']}/pages/{rec['page']}/records") or []
img = page_with_boxes(h["stem"], rec["page"], page_recs, rec["block_id"])

left, right = st.columns([3, 2])
left.subheader(f"1. Rendered page {rec['page']}, bbox highlighted")
if img is not None:
    left.image(crop_around(img, rec["bbox"]), caption="Zoomed to the block")
    left.image(img)
right.subheader("2. JSONL record")
right.json(rec)
right.subheader("3. Markdown with provenance")
right.code(t.get("markdown") or "Not found in section Markdown.", language="markdown")
right.subheader("4. XBRL check")
right.write("See Reports → xbrl.md for the Arelle fact and match status of each statement line.")
