import pandas as pd
import streamlit as st

from lantern_api import get

st.set_page_config(page_title="Tables", layout="wide")
st.title("Tables")
st.caption("Each table as extracted: normalized values (parentheses → negatives, dashes → 0, "
           "scale noted) next to the raw cell strings from the PDF.")


def frame(rows, cols):
    try:
        return pd.DataFrame(rows, columns=cols)
    except Exception:
        return pd.DataFrame(rows)


stems = [f["stem"] for f in (get("/filings") or [])]
pick_col1, pick_col2 = st.columns([1, 3])
stem = pick_col1.selectbox("Filing", stems)
tbls = get(f"/filings/{stem}/tables") or []
if not tbls:
    st.info("No table records for this filing.")
    st.stop()


def table_label(i):
    x = tbls[i]
    return (f"Page {x['record']['page']} · {x.get('context') or '(no heading on page)'}"
            f"  [{x['record']['block_id']}]")


i = pick_col2.selectbox("Table", range(len(tbls)), format_func=table_label,
                         help="Tables are labelled by page and the heading above them.")
r, ctx = tbls[i]["record"], tbls[i].get("context")
t = r["table"]
st.subheader(ctx or f"Table on page {r['page']}")
scale = t.get("scale")
if isinstance(scale, dict):
    scale_txt = " · ".join(f"{k.replace('_', '-')} ×{v:,.0f}" for k, v in scale.items())
else:
    scale_txt = str(scale)
st.write(f"Page {r['page']} · extractor: {r.get('extractor')} {r.get('extractor_version', '')}  \n"
         f"Scale applied: {scale_txt}")


def fmt(v):
    if isinstance(v, bool) or v is None:
        return "" if v is None else str(v)
    if isinstance(v, (int, float)):
        if v != v:  # NaN
            return ""
        return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:g}"
    return str(v)


norm = frame(t.get("rows"), t.get("columns"))
raw = frame(t.get("raw_cells"), t.get("columns"))
c1, c2 = st.columns(2)
c1.subheader("Normalized values (full units)")
c1.dataframe(norm.map(fmt), hide_index=True)
c2.subheader("Raw cell strings")
c2.dataframe(raw.fillna("").astype(str), hide_index=True)
st.download_button("Download normalized CSV", norm.to_csv(index=False),
                   file_name=f"{stem}_{r['block_id']}.csv", mime="text/csv")
