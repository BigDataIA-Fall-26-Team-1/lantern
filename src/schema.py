"""
Part 5 - Record schema (brief Appendix B), validated on every write.

Every record written to data/export/{stem}.jsonl goes through Record, so a
missing or malformed field fails loudly at the export stage instead of
silently reaching Case Study 2.

Usage:
    from src.schema import Record, write_jsonl, record_keys
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SCHEMA_VERSION = "lantern/1.0"

BlockType = Literal["Text", "Title", "List", "Table", "Figure", "Footnote"]

ACCESSION = re.compile(r"^\d{10}-\d{2}-\d{6}$")    # NNNNNNNNNN-YY-NNNNNN
CIK = re.compile(r"^\d{10}$")                      # zero-padded to 10 digits
BLOCK_ID = re.compile(r"^p\d{4}_b\d{3,}$")         # p0045_b003
FISCAL_PERIOD = re.compile(r"^(FY|Q[1-4]|H[12])$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


class TableObject(BaseModel):
    """A table as structured data: normalized rows next to the raw cell strings."""

    model_config = ConfigDict(extra="forbid")

    columns: list[str]                                  # header text per column
    rows: list[list[Union[str, float, None]]]           # label + normalized values
    raw_cells: list[list[str]]                          # the cells exactly as extracted
    scale: dict[str, float]                             # e.g. {"money": 1e6, "shares": 1e3, "per_share": 1}
    row_kinds: Optional[list[str]] = None               # header / money / shares / per_share
    method: Optional[str] = None                        # e.g. camelot-stream
    status: Optional[str] = None                        # accepted / best_below_threshold (Part 2 decision)
    source_csv: Optional[str] = None                    # Part 2 file the table came from

    @model_validator(mode="after")
    def _shape(self) -> "TableObject":
        width = len(self.columns)
        bad = [i for i, r in enumerate(self.rows) if len(r) != width]
        if bad:
            raise ValueError(f"rows {bad[:5]} do not have {width} cells (one per column)")
        if self.row_kinds is not None and len(self.row_kinds) != len(self.rows):
            raise ValueError("row_kinds must have one entry per row")
        return self


class Record(BaseModel):
    """One block of one filing, with enough provenance to find it on the page."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    # 'schema' would shadow a BaseModel attribute, so the field is schema_ and is
    # written to JSON as "schema" (dump with by_alias=True).
    schema_: str = Field(SCHEMA_VERSION, alias="schema")
    doc_id: str
    company: str
    cik: str
    ticker: str
    form: str
    fiscal_year: int
    fiscal_period: str
    page: int = Field(ge=1)
    section: Optional[str] = None
    block_id: str
    block_type: BlockType
    bbox: list[float]
    units: Literal["pt"] = "pt"
    origin: Literal["top-left"] = "top-left"
    text: Optional[str] = None
    table: Optional[TableObject] = None
    extractor: str
    extractor_version: str
    ocr: bool = False
    ocr_conf: Optional[float] = None
    source_path: str
    sha256: str
    # extra provenance beyond Appendix B's minimum (always present, may be null)
    detector: Optional[str] = None                      # LayoutParser model that found the block
    detector_score: Optional[float] = None              # its confidence, 0-1
    figure_path: Optional[str] = None                   # crop in data/figures for Figure blocks

    @field_validator("doc_id")
    @classmethod
    def _doc_id(cls, v: str) -> str:
        if not ACCESSION.match(v):
            raise ValueError(f"doc_id must be an accession number NNNNNNNNNN-YY-NNNNNN, got {v!r}")
        return v

    @field_validator("cik")
    @classmethod
    def _cik(cls, v: str) -> str:
        if not CIK.match(v):
            raise ValueError(f"cik must be 10 digits, zero-padded, got {v!r}")
        return v

    @field_validator("fiscal_year")
    @classmethod
    def _year(cls, v: int) -> int:
        if not 1990 <= v <= 2100:
            raise ValueError(f"fiscal_year out of range: {v}")
        return v

    @field_validator("fiscal_period")
    @classmethod
    def _period(cls, v: str) -> str:
        if not FISCAL_PERIOD.match(v):
            raise ValueError(f"fiscal_period must be FY, Q1-Q4 or H1/H2, got {v!r}")
        return v

    @field_validator("block_id")
    @classmethod
    def _block_id(cls, v: str) -> str:
        if not BLOCK_ID.match(v):
            raise ValueError(f"block_id must look like p0045_b003, got {v!r}")
        return v

    @field_validator("bbox")
    @classmethod
    def _bbox(cls, v: list[float]) -> list[float]:
        if len(v) != 4:
            raise ValueError(f"bbox must be [x0, top, x1, bottom], got {len(v)} numbers")
        x0, top, x1, bottom = v
        if not (x0 < x1 and top < bottom):
            raise ValueError(f"bbox must have x0 < x1 and top < bottom, got {v}")
        if min(v) < -1:
            raise ValueError(f"bbox has negative coordinates: {v}")
        return v

    @field_validator("source_path", "figure_path")
    @classmethod
    def _relative_path(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        if "\\" in v or v.startswith("/") or re.match(r"^[A-Za-z]:", v):
            raise ValueError(f"paths must be relative with forward slashes, got {v!r}")
        return v

    @field_validator("detector_score")
    @classmethod
    def _score(cls, v: Optional[float]) -> Optional[float]:
        if v is not None and not 0 <= v <= 1:
            raise ValueError(f"detector_score must be 0-1, got {v}")
        return v

    @field_validator("sha256")
    @classmethod
    def _sha(cls, v: str) -> str:
        if not SHA256.match(v):
            raise ValueError("sha256 must be 64 lowercase hex characters")
        return v

    @model_validator(mode="after")
    def _content(self) -> "Record":
        if self.ocr_conf is not None and not self.ocr:
            raise ValueError("ocr_conf is only allowed when ocr is true")
        if self.ocr_conf is not None and not 0 <= self.ocr_conf <= 100:
            raise ValueError(f"ocr_conf must be 0-100, got {self.ocr_conf}")
        if self.block_type in ("Text", "Title", "List", "Footnote") and self.text is None:
            raise ValueError(f"{self.block_type} block {self.block_id} needs text (may be an empty string)")
        if self.block_type != "Table" and self.table is not None:
            raise ValueError(f"only Table blocks may carry a table object ({self.block_id} is {self.block_type})")
        return self


def record_keys() -> list[str]:
    """The exact JSON keys every record has, in order (aliases applied)."""
    return [f.alias or name for name, f in Record.model_fields.items()]


def write_jsonl(records: list[dict], path: Path) -> int:
    """Validate every record, then write them as JSONL (UTF-8, LF). Returns the count.
    Nothing is written if any record fails validation."""
    lines = [Record.model_validate(r).model_dump_json(by_alias=True) for r in records]
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for line in lines:
            f.write(line + "\n")
    return len(lines)