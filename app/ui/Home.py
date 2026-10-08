import pandas as pd
import streamlit as st

from lantern_api import get

st.set_page_config(page_title="LANTERN", layout="wide")
st.title("Project LANTERN")
st.caption("Read-only viewer over the DVC pipeline outputs: data/export, data/rendered and reports/.")

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

st.subheader("Records by block type")
bt = pd.DataFrame({f["stem"]: f["block_types"] for f in filings}).fillna(0).astype(int)
st.bar_chart(bt)

st.subheader("Manifest")
st.dataframe(pd.DataFrame([f.get("manifest", {}) for f in filings]).astype(str))

st.subheader("Links")
for name, url in LINKS.items():
    st.write(f"{name}: {url}")
