"""
Part 7 - Small helpers for reading Textract's block graph (shared by the mapper and the fallback).
Textract boxes are fractions of the page with a top-left origin; to_points converts them to PDF points.
"""

from __future__ import annotations

from pathlib import Path


def children(block: dict, by_id: dict, kind: str = "CHILD") -> list[dict]:
    out = []
    for rel in block.get("Relationships", []):
        if rel["Type"] == kind:
            out += [by_id[i] for i in rel["Ids"] if i in by_id]
    return out


def to_points(block: dict, page_size: tuple[float, float]) -> list[float]:
    """Textract BoundingBox (fractions, top-left origin) -> [x0, top, x1, bottom] in points."""
    w, h = page_size
    bb = block["Geometry"]["BoundingBox"]
    x0 = min(max(bb["Left"], 0.0), 1.0) * w
    top = min(max(bb["Top"], 0.0), 1.0) * h
    x1 = min(max(bb["Left"] + bb["Width"], 0.0), 1.0) * w
    bottom = min(max(bb["Top"] + bb["Height"], 0.0), 1.0) * h
    return [round(x0, 2), round(top, 2), round(x1, 2), round(bottom, 2)]


def overlap_ratio(a: list[float], b: list[float]) -> float:
    """Intersection area / area of the smaller box."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    small = min((a[2] - a[0]) * (a[3] - a[1]), (b[2] - b[0]) * (b[3] - b[1]))
    return (ix * iy) / small if small > 0 else 0.0


def cell_text(cell: dict, by_id: dict) -> str:
    words = []
    for c in children(cell, by_id):
        if c["BlockType"] == "WORD":
            words.append(c.get("Text", ""))
        elif c["BlockType"] == "SELECTION_ELEMENT" and c.get("SelectionStatus") == "SELECTED":
            words.append("[X]")
    return " ".join(words)


def table_grid(table: dict, by_id: dict) -> list[list[str]]:
    """A TABLE block as a grid of cell strings, from its CELLs' RowIndex / ColumnIndex (1-based)."""
    cells = [c for c in children(table, by_id) if c["BlockType"] == "CELL"]
    if not cells:
        return []
    n_rows = max(c["RowIndex"] for c in cells)
    n_cols = max(c["ColumnIndex"] for c in cells)
    grid = [[""] * n_cols for _ in range(n_rows)]
    for c in cells:
        grid[c["RowIndex"] - 1][c["ColumnIndex"] - 1] = cell_text(c, by_id)
    return grid


def mean(xs: list[float]) -> float | None:
    return round(sum(xs) / len(xs), 2) if xs else None


def version_string(response: dict, mp: dict) -> str:
    from src.managed.cache import feature_list
    return (f"{mp.get('api', 'AnalyzeDocument')} model {response.get('AnalyzeDocumentModelVersion')} "
            f"({'+'.join(feature_list(mp.get('features', [])))})")


def page_size_of(pdf_path: str | Path, page: int) -> tuple[float, float]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf_path))
    try:
        w, h = doc[page - 1].get_size()
        return float(w), float(h)
    finally:
        doc.close()
