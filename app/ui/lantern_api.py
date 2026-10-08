"""Small client for the LANTERN API plus the bbox overlay."""
import io
import os

import requests
import streamlit as st
from PIL import Image, ImageDraw

API = os.environ.get("LANTERN_API", "http://127.0.0.1:8000")
DPI = 100  # page image resolution; bbox scale is DPI / 72
COLORS = {"Text": "#1f77b4", "Title": "#d62728", "List": "#2ca02c",
          "Table": "#ff7f0e", "Figure": "#9467bd", "Footnote": "#8c564b"}


@st.cache_data(ttl=300)
def get(endpoint, **params):
    r = requests.get(f"{API}{endpoint}", params=params, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


@st.cache_data(ttl=300)
def get_bytes(endpoint, **params):
    r = requests.get(f"{API}{endpoint}", params=params, timeout=60)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.content


def page_with_boxes(stem, page, records, highlight=None):
    png = get_bytes(f"/filings/{stem}/pages/{page}/image", dpi=DPI)
    if png is None:
        return None
    img = Image.open(io.BytesIO(png)).convert("RGB")
    draw = ImageDraw.Draw(img)
    k = DPI / 72  # bbox is in points, top-left origin
    for r in records:
        bbox = r.get("bbox")
        if not bbox or len(bbox) != 4:
            continue
        hit = highlight is not None and r.get("block_id") == highlight
        color = "#ff0000" if hit else COLORS.get(r.get("block_type"), "#7f7f7f")
        draw.rectangle([v * k for v in bbox], outline=color, width=5 if hit else 2)
    return img


def crop_around(img, bbox, pad_pt=20):
    k = DPI / 72
    x0, top, x1, bottom = bbox
    return img.crop((max(0, (x0 - pad_pt) * k), max(0, (top - pad_pt) * k),
                     min(img.width, (x1 + pad_pt) * k), min(img.height, (bottom + pad_pt) * k)))
