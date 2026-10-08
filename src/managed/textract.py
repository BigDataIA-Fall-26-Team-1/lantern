"""
Part 7 - AWS Textract behind the cache.

    fetch(pdf_path, page, mp, cache_dir) -> (response or None, status)

- cache hit                       -> the cached response, no API call ("hit")
- cache miss, managed.enabled off -> None, no API call ("miss, API disabled")
- cache miss, managed.enabled on  -> one AnalyzeDocument call on that single page,
                                     saved to the cache, then returned ("called")

boto3 is imported only for a real call, so with managed.enabled: false the pipeline needs
no AWS package and no credentials. Credentials come from the standard AWS chain
(e.g. AWS_PROFILE=lantern-textract); nothing is read from the repo.
"""

from __future__ import annotations

import io
from datetime import datetime, timezone
from pathlib import Path

from src.managed import cache


def single_page_pdf(pdf_path: str | Path, page: int) -> bytes:
    """One page (1-based) as PDF bytes, for Textract's synchronous API. The bytes are not
    reproducible run to run, which is why the cache key never uses them."""
    import pypdfium2 as pdfium
    src = pdfium.PdfDocument(str(pdf_path))
    try:
        if not 1 <= page <= len(src):
            raise ValueError(f"{pdf_path}: page {page} outside 1..{len(src)}")
        out = pdfium.PdfDocument.new()
        out.import_pages(src, [page - 1])
        buf = io.BytesIO()
        out.save(buf)
        out.close()
        return buf.getvalue()
    finally:
        src.close()


def call_textract(page_bytes: bytes, mp: dict) -> dict:
    import boto3  # only here: never imported when the API is disabled
    client = boto3.client("textract", region_name=mp["region"])
    if mp["api"] != "AnalyzeDocument":
        raise ValueError(f"unsupported managed.api {mp['api']!r}")
    resp = client.analyze_document(Document={"Bytes": page_bytes}, FeatureTypes=list(mp["features"]))
    return resp


def fetch(pdf_path: str | Path, page: int, mp: dict, cache_dir: str | Path,
          reason: str = "", source_sha256: str | None = None) -> tuple[dict | None, str]:
    src_sha = source_sha256 or cache.sha256_file(pdf_path)
    key = cache.cache_key(src_sha, page, mp["provider"], mp["api"], mp["features"])
    entry = cache.read(cache_dir, key)
    if entry is not None:
        return entry["response"], "hit"
    if not mp.get("enabled", False):
        return None, "miss, API disabled"
    resp = call_textract(single_page_pdf(pdf_path, page), mp)
    meta = {
        "source": Path(pdf_path).as_posix(),
        "source_sha256": src_sha,
        "page": int(page),
        "provider": mp["provider"],
        "api": mp["api"],
        "features": cache.feature_list(mp["features"]),
        "region": mp["region"],
        "model_version": resp.get("AnalyzeDocumentModelVersion"),
        "requested_at": resp.get("ResponseMetadata", {}).get("HTTPHeaders", {}).get("date")
                        or datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "requested_by": "pipeline",
        "reason": reason,
    }
    cache.write(cache_dir, key, meta, resp)
    return resp, "called"
