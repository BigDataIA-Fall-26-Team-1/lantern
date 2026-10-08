"""
Part 7 - Cache of managed-service responses, keyed by a stable page hash.

The key is sha256(source PDF sha256 | page | provider | api | features). It never uses
the bytes of an extracted single-page PDF (pypdfium2 does not write those identically),
so the same page gets the same key on every run and every machine after `dvc pull`.

Cache file: {cache_dir}/{key}.json  =  {"_cache_meta": {...}, "response": {...raw API response...}}
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def feature_list(features) -> list[str]:
    """['LAYOUT', 'TABLES'] from a list, or a string like 'TABLES+LAYOUT' / 'TABLES, LAYOUT'."""
    if isinstance(features, str):
        features = features.replace(",", "+").split("+")
    return sorted(f.strip().upper() for f in features if f and f.strip())


def cache_key(source_sha256: str, page: int, provider: str, api: str, features) -> str:
    parts = [source_sha256, f"p{int(page)}", provider, api, "+".join(feature_list(features))]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def cache_path(cache_dir: str | Path, key: str) -> Path:
    return Path(cache_dir) / f"{key}.json"


def read(cache_dir: str | Path, key: str) -> dict | None:
    """The cached entry, or None on a miss."""
    p = cache_path(cache_dir, key)
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def write(cache_dir: str | Path, key: str, meta: dict, response: dict) -> Path:
    p = cache_path(cache_dir, key)
    p.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps({"_cache_meta": meta, "response": response}, indent=1, ensure_ascii=False,
                      sort_keys=True, default=str) + "\n"
    p.write_text(text, encoding="utf-8", newline="\n")
    return p
