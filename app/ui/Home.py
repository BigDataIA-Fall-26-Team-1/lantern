import pandas as pd
import streamlit as st

from lantern_api import get

st.set_page_config(page_title="LANTERN", layout="wide")
st.title("Project LANTERN")

LINKS = {
    "Repository": "https://github.com/BigDataIA-Fall-26-Team-1/lantern",
    "Codelab": "TODO",
    "Demo video": "TODO",
}

try:
    get("/health")
except Exception as e:
    st.error(f"Backend not reachable: {e}")
    st.stop()

filings = get("/filings") or []
if not filings:
    st.warning("No exported filings found. Run dvc pull first.")
    st.stop()

cols = st.columns(len(filings))
for col, f in zip(cols, filings):
    m = f.get("manifest", {})
    col.subheader(f["stem"])
    col.write(f"{m.get('company', '')}  \n"
              f"Accession: {m.get('accession', '?')}  \n"
              f"Form: {m.get('form', '?')} · Period: {m.get('period', '?')}  \n"
              f"Renderer: {m.get('renderer_version', '?')} · {m.get('page_format', '?')}")
    col.metric("Pages", f["n_pages"])
    col.metric("Records", f["n_records"])

st.subheader("What was extracted")
st.caption("Each record is one block the layout model found on a page: "
           "Text = paragraphs, Title = headings, Table = tables (cells extracted into rows and columns), "
           "List = bullet lists, Figure = images.")

order = ["Text", "Title", "Table", "List", "Figure", "Footnote"]
names = {f["stem"]: f"{f['manifest'].get('form', '')} ({str(f['manifest'].get('period', ''))[:4]})"
         for f in filings}
bt = (pd.DataFrame({names[f["stem"]]: f["block_types"] for f in filings})
      .fillna(0).astype(int))
bt = bt.reindex([t for t in order if t in bt.index] + [t for t in bt.index if t not in order])
bt.loc["Total"] = bt.sum()
bt.index.name = "Block type"
st.dataframe(bt)

st.subheader("Manifest")
st.dataframe(pd.DataFrame([f.get("manifest", {}) for f in filings]).astype(str))

st.subheader("Links")
for name, url in LINKS.items():
    st.write(f"{name}: {url}")
