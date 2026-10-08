"""
Part 7 - One-time import of Textract responses requested earlier (by a teammate, from
their own AWS account) into this cache format. Makes NO API calls.

Usage (repo root, after `dvc pull`):
    python -m src.managed.import_responses --src ~/Downloads/textract_responses --requested-by Pranav

Each input file is a raw AnalyzeDocument response with a `_cache_meta` dict added at the
top level (source_sha256, page, features, ...). The source file is identified by its hash
among data/rendered/*.pdf and tests/fixtures/*.pdf; anything else is refused.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from src.managed import cache


def known_sources(candidates: list[Path]) -> dict[str, str]:
    return {cache.sha256_file(p): p.as_posix() for p in candidates if p.is_file()}


def main() -> None:
    ap = argparse.ArgumentParser(description="Import earlier Textract responses into data/managed (no API calls)")
    ap.add_argument("--src", required=True, help="folder with the received JSON responses")
    ap.add_argument("--cache", default="data/managed")
    ap.add_argument("--params", default="params.yaml")
    ap.add_argument("--rendered", default="data/rendered")
    ap.add_argument("--fixtures", default="tests/fixtures")
    ap.add_argument("--requested-by", required=True, help="who sent the pages (recorded in the metadata)")
    a = ap.parse_args()

    mp = yaml.safe_load(open(a.params, encoding="utf-8"))["managed"]
    sources = known_sources(sorted(Path(a.rendered).glob("*.pdf")) + sorted(Path(a.fixtures).glob("*.pdf")))
    files = sorted(Path(a.src).expanduser().glob("*.json"))
    if not files:
        raise SystemExit(f"no JSON files in {a.src}")

    print(f"{'source':<32} {'page':>4}  {'features':<14} {'requested_at':<31} status")
    for f in files:
        d = json.loads(f.read_text(encoding="utf-8"))
        old = d.pop("_cache_meta", None)
        if not old or "Blocks" not in d:
            raise SystemExit(f"{f.name}: not a raw response with _cache_meta")
        src = sources.get(old.get("source_sha256"))
        if src is None:
            raise SystemExit(f"{f.name}: source hash {str(old.get('source_sha256'))[:12]} matches no official PDF")
        if d.get("DocumentMetadata", {}).get("Pages") != 1:
            raise SystemExit(f"{f.name}: expected a one-page response")
        raw = old.get("features")
        api_part = None
        if isinstance(raw, str) and "|" in raw:          # e.g. "AnalyzeDocument|TABLES+LAYOUT"
            api_part, raw = raw.rsplit("|", 1)
        api_part = api_part or old.get("processor")
        if api_part and api_part.strip().lower() != mp["api"].lower():
            raise SystemExit(f"{f.name}: API {api_part!r} differs from params managed.api {mp['api']!r}")
        features = cache.feature_list(raw)
        if features != cache.feature_list(mp["features"]):
            raise SystemExit(f"{f.name}: features {old.get('features')!r} differ from params "
                             f"managed.features {mp['features']}")

        meta = {
            "source": src,
            "source_sha256": old["source_sha256"],
            "page": int(old["page"]),
            "provider": mp["provider"],
            "api": mp["api"],
            "features": features,
            "region": mp["region"],
            "model_version": d.get("AnalyzeDocumentModelVersion"),
            "requested_at": d.get("ResponseMetadata", {}).get("HTTPHeaders", {}).get("date"),
            "requested_by": a.requested_by,
            "reason": old.get("reason"),
            "imported_from": f.name,
        }
        key = cache.cache_key(meta["source_sha256"], meta["page"], meta["provider"], meta["api"], features)
        existing = cache.read(a.cache, key)
        if existing is not None:
            status = "already imported" if existing["response"] == d else "CONFLICT (different response, kept old)"
        else:
            cache.write(a.cache, key, meta, d)
            status = f"imported -> {key[:12]}"
        print(f"{src:<32} {meta['page']:>4}  {'+'.join(features):<14} {str(meta['requested_at']):<31} {status}")
    print(f"\n{len(list(Path(a.cache).glob('*.json')))} cached response(s) in {a.cache}")


if __name__ == "__main__":
    main()