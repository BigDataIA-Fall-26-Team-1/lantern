"""
Part 7 - The managed service (AWS Textract) as an optional fallback for low-quality output.

Table fallback (called by src/export.py for every Table block):
    trigger: the Part 3 table status is best_below_threshold or no_valid_table, or its
             score is below tables.accept_score (the same rule Part 2 uses to accept a table)
    use:     the Textract table that overlaps the block's box (more than half of the smaller box)

Text fallback (called by src/parse_text.py for every page that went through Tesseract):
    trigger: Tesseract's mean confidence is below managed.ocr_conf_threshold, or OCR found no text
    use:     Textract's LINE text and WORD boxes for that page

Both look in the cache first (src/managed/cache.py). On a miss, Textract is called only when
managed.enabled is true. Every decision is logged: trigger, cache hit / miss / call, used or not.
"""

from __future__ import annotations

import csv
from pathlib import Path

from src.managed import textract
from src.managed.blocks import children, mean, overlap_ratio, page_size_of, table_grid, to_points, version_string

LOG_FIELDS = ["stem", "page", "block_id", "kind", "trigger", "cache", "used", "detail"]
LOW_STATUSES = ("best_below_threshold", "no_valid_table")


class Fallback:
    def __init__(self, mp: dict, cache_dir: str | Path, accept_score: float | None = None,
                 ocr_threshold: float | None = None):
        self.mp = mp
        self.cache_dir = Path(cache_dir)
        self.accept_score = accept_score
        self.ocr_threshold = ocr_threshold if ocr_threshold is not None else mp.get("ocr_conf_threshold")
        self.rows: list[dict] = []

    # ---- logging -----------------------------------------------------------
    def _log(self, stem, page, block_id, kind, trigger, cache, used, detail=""):
        self.rows.append({"stem": stem, "page": page, "block_id": block_id or "", "kind": kind,
                          "trigger": trigger, "cache": cache, "used": used, "detail": detail})

    def write_log(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=LOG_FIELDS, lineterminator="\n")
            w.writeheader()
            w.writerows(self.rows)

    def summary(self) -> str:
        used = sum(1 for r in self.rows if r["used"])
        hits = sum(1 for r in self.rows if r["cache"] == "hit")
        return f"{len(self.rows)} triggered, {hits} cache hit(s), {used} used"

    # ---- tables ------------------------------------------------------------
    def table_trigger(self, t: dict) -> str | None:
        status, score = t.get("status"), t.get("score")
        if status in LOW_STATUSES:
            return status if score is None else f"{status} (score {score})"
        if self.accept_score is not None and score is not None and score < self.accept_score:
            return f"score {score} < accept_score {self.accept_score}"
        return None

    def table(self, pdf_path, page: int, block_bbox: list[float], trigger: str, stem: str,
              block_id: str, sha256: str | None = None) -> dict | None:
        resp, status = textract.fetch(pdf_path, page, self.mp, self.cache_dir, reason=trigger, source_sha256=sha256)
        if resp is None:
            self._log(stem, page, block_id, "table", trigger, status, False)
            return None
        size = page_size_of(pdf_path, page)
        by_id = {b["Id"]: b for b in resp["Blocks"]}
        best, best_r = None, 0.0
        for t in (b for b in resp["Blocks"] if b["BlockType"] == "TABLE"):
            r = overlap_ratio(block_bbox, to_points(t, size))
            if r > best_r:
                best, best_r = t, r
        if best is None or best_r <= 0.5:
            self._log(stem, page, block_id, "table", trigger, status, False,
                      f"no Textract table overlaps the block (best overlap {best_r:.2f})")
            return None
        grid = table_grid(best, by_id)
        confs = [c["Confidence"] for c in children(best, by_id) if c["BlockType"] == "CELL" and "Confidence" in c]
        self._log(stem, page, block_id, "table", trigger, status, True,
                  f"Textract table {len(grid)}x{len(grid[0]) if grid else 0}, overlap {best_r:.2f}")
        return {"grid": grid, "ocr_conf": mean(confs), "extractor_version": version_string(resp, self.mp)}

    # ---- text --------------------------------------------------------------
    def text_trigger(self, mean_conf: float | None, text: str) -> str | None:
        if not text.strip():
            return "ocr_empty"
        if self.ocr_threshold is not None and (mean_conf is None or mean_conf < self.ocr_threshold):
            return f"ocr_conf {mean_conf} < {self.ocr_threshold}"
        return None

    def text(self, pdf_path, page: int, page_size: tuple[float, float], trigger: str, stem: str,
             sha256: str | None = None) -> dict | None:
        resp, status = textract.fetch(pdf_path, page, self.mp, self.cache_dir, reason=trigger, source_sha256=sha256)
        if resp is None:
            self._log(stem, page, None, "text", trigger, status, False)
            return None
        lines = [b for b in resp["Blocks"] if b["BlockType"] == "LINE"]
        words = [{"text": b.get("Text", ""), "bbox": to_points(b, page_size), "conf": round(b["Confidence"], 1)}
                 for b in resp["Blocks"] if b["BlockType"] == "WORD"]
        conf = mean([b["Confidence"] for b in lines if "Confidence" in b])
        self._log(stem, page, None, "text", trigger, status, True, f"{len(lines)} lines, mean conf {conf}")
        return {"text": "\n".join(b.get("Text", "") for b in lines), "words": words,
                "mean_conf": None if conf is None else round(conf, 1),
                "extractor_version": version_string(resp, self.mp)}
