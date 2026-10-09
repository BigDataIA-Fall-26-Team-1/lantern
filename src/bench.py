"""
Part 10 - Cost and throughput benchmarking

Usage:
    python src/bench.py --machine                        # write machine.json only
    python src/bench.py --stage parse_pdfplumber          # benchmark one stage
    python src/bench.py --stage layout --run 2            # second run of a model stage
    python src/bench.py --stage tables --pages 32-36      # only some pages
    python src/bench.py --summarize                       # Appendix C tables from the CSVs

Outputs:
    data/bench/machine.json       hardware and package versions
    data/bench/{stage}.csv        one row per page per run
    data/bench/summary.csv        per-stage p50/p95/peak RSS/failures (from --summarize)
    data/bench/cold_start.csv     model load / first page / warm median per run (from --summarize)

Benchmarks reuse the pipeline's own functions (extract_page, run_ocr,
extract_best, detect, build_converter, build_records ...) so timings reflect
the real code. Nothing is written into DVC stage outputs (data/parsed,
data/tables, ...), only to data/bench/.

Each stage CSV is appended to, so re-running a stage adds rows. Delete the
stage's CSV first if you want a clean re-run.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psutil
import yaml


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def load_params(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_machine_id() -> str:
    """Short machine identifier for the CSV."""
    return platform.node() or "unknown"


def parse_pages(spec: str | None) -> set[int] | None:
    """'1,32-34' -> {1, 32, 33, 34}. None means every page."""
    if not spec:
        return None
    pages: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            a, b = part.split("-")
            pages.update(range(int(a), int(b) + 1))
        elif part:
            pages.add(int(part))
    return pages


def add_src_to_path() -> None:
    """For modules that import their neighbours directly (layout.py does `from tables import ...`)."""
    src_dir = str(Path(__file__).resolve().parent)
    if src_dir not in sys.path:
        sys.path.insert(0, src_dir)


def add_repo_root_to_path() -> None:
    """For modules imported as src.<name> (team convention: python -m src.<name>)."""
    repo_root = str(Path(__file__).resolve().parents[1])
    if repo_root not in sys.path:
        sys.path.insert(0, repo_root)


# ---------------------------------------------------------------------------
# machine info
# ---------------------------------------------------------------------------

def collect_machine_info() -> dict:
    """Gather hardware specs, OS, Python, and key package versions."""
    import subprocess

    cpu = platform.processor() or "unknown"
    # On Windows, platform.processor() returns a raw family string;
    # always prefer the friendly name from CIM
    if platform.system() == "Windows":
        try:
            out = subprocess.check_output(
                ["powershell", "-Command", "(Get-CimInstance Win32_Processor).Name"],
                text=True).strip()
            if out:
                cpu = out
        except Exception:
            pass
    elif platform.system() == "Darwin":
        try:
            out = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True).strip()
            if out:
                cpu = out
        except Exception:
            pass

    mem = psutil.virtual_memory()

    gpu = "none"
    try:
        out = subprocess.check_output(["nvidia-smi", "--query-gpu=name",
                                       "--format=csv,noheader"], text=True).strip()
        if out:
            gpu = out
    except Exception:
        pass

    from importlib.metadata import version, PackageNotFoundError
    pkgs = {}
    for name in ["pdfplumber", "pytesseract", "camelot-py", "layoutparser",
                 "docling", "psutil", "torch", "huggingface-hub"]:
        try:
            pkgs[name] = version(name)
        except PackageNotFoundError:
            pkgs[name] = "not installed"

    try:
        import pytesseract
        cmd = os.environ.get("TESSERACT_CMD")
        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        pkgs["tesseract-binary"] = pytesseract.get_tesseract_version().public
    except Exception:
        pkgs["tesseract-binary"] = "unknown"

    return {
        "cpu": cpu,
        "cores_physical": psutil.cpu_count(logical=False),
        "cores_logical": psutil.cpu_count(logical=True),
        "ram_gb": round(mem.total / (1024 ** 3), 1),
        "gpu": gpu,
        "os": f"{platform.system()} {platform.version()}",
        "python": platform.python_version(),
        "packages": pkgs,
        "date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
    }


def write_machine_info(out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    info = collect_machine_info()
    path = out_dir / "machine.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(info, f, indent=2)
    print(f"[INFO] Machine info -> {path}")
    for k, v in info.items():
        if k != "packages":
            print(f"       {k}: {v}")
    print("       packages:")
    for k, v in info["packages"].items():
        print(f"         {k}: {v}")


# ---------------------------------------------------------------------------
# CSV harness
# ---------------------------------------------------------------------------

CSV_FIELDS = ["stage", "stem", "page", "run", "phase",
              "seconds", "rss_mb", "rss_delta_mb", "output_size",
              "status", "machine_id"]


def open_csv(out_dir: Path, stage: str):
    """Open (or create) the CSV for a stage and return (file, writer)."""
    path = out_dir / f"{stage}.csv"
    is_new = not path.exists() or path.stat().st_size == 0
    f = open(path, "a", encoding="utf-8", newline="")
    w = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
    if is_new:
        w.writeheader()
    return f, w


def bench_one(writer, stage: str, stem: str, page: int, run: int,
              phase: str, fn) -> str:
    """Time a single call, record RSS, catch errors, write one CSV row."""
    proc = psutil.Process(os.getpid())
    rss0 = proc.memory_info().rss
    t0 = time.perf_counter()
    status, output_size = "ok", 0
    try:
        result = fn()
        if result is None:
            status = "empty"
        elif isinstance(result, str):
            output_size = len(result)
            if not result.strip():
                status = "empty"
        elif isinstance(result, (list, dict)):
            output_size = len(result)
            if output_size == 0:
                status = "empty"
        else:
            output_size = len(str(result))
    except Exception as e:
        status = f"error:{type(e).__name__}: {e}"
    dt = time.perf_counter() - t0
    rss = proc.memory_info().rss

    writer.writerow({
        "stage": stage,
        "stem": stem,
        "page": page,
        "run": run,
        "phase": phase,
        "seconds": round(dt, 4),
        "rss_mb": rss // (1024 * 1024),
        "rss_delta_mb": (rss - rss0) // (1024 * 1024),
        "output_size": output_size,
        "status": status,
        "machine_id": get_machine_id(),
    })
    tag = f"  p{page:04d}" if page else "  "
    print(f"{tag} {phase:<16} {dt:7.3f}s  rss={rss // (1024 * 1024)}MB  {status}")
    return status


# ---------------------------------------------------------------------------
# stage: parse_pdfplumber
# ---------------------------------------------------------------------------

def bench_parse_pdfplumber(pdf_path: Path, stem: str, run: int, out_dir: Path,
                           params: dict, pages: set[int] | None) -> None:
    """Time extract_page() on every page of the PDF."""
    add_src_to_path()
    from parse_text import extract_page
    import pdfplumber

    text_params = params["text"]
    f, w = open_csv(out_dir, "parse_pdfplumber")
    done = 0
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            n = page.page_number
            if pages and n not in pages:
                continue
            bench_one(w, "parse_pdfplumber", stem, n, run, "warm",
                      lambda p=page: extract_page(p, text_params)[0])
            done += 1
    f.close()
    print(f"[INFO] parse_pdfplumber: {done} pages benchmarked")


# ---------------------------------------------------------------------------
# stage: ocr_tesseract (forced on every page)
# ---------------------------------------------------------------------------

def bench_ocr_tesseract(pdf_path: Path, stem: str, run: int, out_dir: Path,
                        params: dict, pages: set[int] | None) -> None:
    """Time run_ocr() forced on every page (simulates a scanned filing).
    Tesseract runs as a separate program, so its memory is NOT in this
    process's RSS (resource.getrusage(RUSAGE_CHILDREN) is not available on Windows)."""
    add_src_to_path()
    from parse_text import run_ocr, check_tesseract
    import pdfplumber

    check_tesseract()
    ocr_params = params["ocr"]
    dpi = ocr_params["dpi"]
    tess_config = ocr_params["tesseract_config"]
    f, w = open_csv(out_dir, "ocr_tesseract")
    done = 0
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            n = page.page_number
            if pages and n not in pages:
                continue
            bench_one(w, "ocr_tesseract", stem, n, run, "warm",
                      lambda p=page: run_ocr(p, dpi, tess_config)[0])
            done += 1
    f.close()
    print(f"[INFO] ocr_tesseract: {done} pages benchmarked (forced OCR at {dpi} DPI)")


# ---------------------------------------------------------------------------
# stage: tables
# ---------------------------------------------------------------------------

def bench_tables(pdf_path: Path, stem: str, run: int, out_dir: Path,
                 params: dict, pages: set[int] | None) -> None:
    """Time extract_best() on each statement page found by scan_statement_pages()."""
    add_src_to_path()
    from tables import scan_statement_pages, extract_best

    tp = params["tables"]
    f, w = open_csv(out_dir, "tables")

    hits = scan_statement_pages(pdf_path, tp)
    if pages:
        hits = [h for h in hits if h["page"] in pages]
    print(f"[INFO] Found {len(hits)} statement pages: "
          f"{[(h['page'], h['statement']) for h in hits]}")

    for hit in hits:
        bench_one(w, "tables", stem, hit["page"], run, "warm",
                  lambda h=hit: extract_best(pdf_path, h, tp))
    f.close()
    print(f"[INFO] tables: {len(hits)} statement pages benchmarked")


# ---------------------------------------------------------------------------
# stage: layout (model-based, cold vs warm)
# ---------------------------------------------------------------------------

def bench_layout(pdf_path: Path, stem: str, run: int, out_dir: Path,
                 params: dict, pages: set[int] | None) -> None:
    """Time load_model() (cold) then detect() per page.
    model_load = loading the EfficientDet weights; cold_first_page = first
    page processed; warm = every page after it."""
    add_src_to_path()
    from layout import load_model, detect
    import pdfplumber

    lp_params = params["layout"]
    dpi = lp_params["dpi"]
    f, w = open_csv(out_dir, "layout")

    holder = {}

    def load():
        holder["model"] = load_model(lp_params)
        return holder["model"]

    bench_one(w, "layout", stem, 0, run, "model_load", load)
    model = holder["model"]

    done = 0
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            n = page.page_number
            if pages and n not in pages:
                continue
            phase = "cold_first_page" if done == 0 else "warm"
            bench_one(w, "layout", stem, n, run, phase,
                      lambda p=page: detect(model, p, dpi)[0])
            done += 1
    f.close()
    print(f"[INFO] layout: {done} pages benchmarked (run {run})")


# ---------------------------------------------------------------------------
# stage: parse_docling (model-based, cold vs warm)
# ---------------------------------------------------------------------------

def bench_parse_docling(pdf_path: Path, stem: str, run: int, out_dir: Path,
                        params: dict, pages: set[int] | None) -> None:
    """Docling, one page at a time.
    model_load      = building the converter AND loading its models
                      (Docling loads models lazily, so initialize_pipeline forces it)
    cold_first_page = first page converted
    warm            = every other page, models already in memory
    """
    # Running `python src/bench.py` puts src/ first on sys.path, so Docling's own
    # `import docling_parse` would find src/docling_parse.py instead of the installed
    # C++ package. Remove src/ and use the repo root, then import the real function.
    src_dir = str(Path(__file__).resolve().parent)
    while src_dir in sys.path:
        sys.path.remove(src_dir)
    add_repo_root_to_path()

    from docling.datamodel.base_models import InputFormat
    import pdfplumber
    from src.docling_parse import build_converter   # the pipeline's own function

    docling_params = params.get("docling", {})
    f, w = open_csv(out_dir, "parse_docling")

    holder = {}

    def load():
        conv = build_converter(docling_params)
        conv.initialize_pipeline(InputFormat.PDF)    # force the models to load now
        holder["conv"] = conv
        return conv

    bench_one(w, "parse_docling", stem, 0, run, "model_load", load)
    conv = holder["conv"]

    with pdfplumber.open(pdf_path) as pdf:
        n_pages = len(pdf.pages)

    done = 0
    for n in range(1, n_pages + 1):
        if pages and n not in pages:
            continue
        phase = "cold_first_page" if done == 0 else "warm"
        bench_one(w, "parse_docling", stem, n, run, phase,
                  lambda pg=n: conv.convert(str(pdf_path),
                                            page_range=(pg, pg)).document.export_to_markdown())
        done += 1
    f.close()
    print(f"[INFO] parse_docling: {done} pages benchmarked (run {run})")


# ---------------------------------------------------------------------------
# stage: export
# ---------------------------------------------------------------------------

def bench_export(pdf_path: Path, stem: str, run: int, out_dir: Path,
                 params: dict, pages: set[int] | None) -> None:
    """Time the export stage (build_records + to_markdown + to_text) for one filing.
    Export works on the whole filing at once, so one row is written (page 0);
    --summarize divides it by the filing's page count."""
    import re
    # export.py imports `src.managed`, `src.schema`, `src.tables`, so it needs the
    # repo root on sys.path and must be imported as src.export (team convention)
    add_repo_root_to_path()
    from src.export import (load_manifest, load_jsonl, build_records, dei_facts,
                            sha256_of, to_markdown, to_text)
    from src.managed.fallback import Fallback

    rendered_dir = pdf_path.parent
    manifest = load_manifest(rendered_dir / "manifest.csv")
    if stem not in manifest:
        print(f"[ERROR] {stem} not in manifest")
        return

    man = manifest[stem]
    blocks_path = Path("data/layout") / f"{stem}.blocks.jsonl"
    if not blocks_path.exists():
        print(f"[ERROR] {blocks_path} not found; run the layout stage first (dvc pull)")
        return

    blocks = load_jsonl(blocks_path)
    pdf_rel = pdf_path.as_posix()
    sha = sha256_of(pdf_path)
    fiscal = dei_facts(Path("data/raw"), man["accession"])
    footer = re.compile(params["export"]["footer_pattern"])
    footer_line = re.compile(params["export"]["footer_line_pattern"])

    def do_export():
        managed = Fallback(params["managed"], "data/managed",
                           accept_score=params["tables"]["accept_score"])
        records, headings, fragments = build_records(
            stem, blocks, man, fiscal, pdf_rel, sha,
            Path("data/parsed"), params, managed)
        md, _ = to_markdown(records, headings, footer, footer_line, fragments)
        txt = to_text(records, footer, footer_line, fragments)
        return md + txt

    f, w = open_csv(out_dir, "export")
    bench_one(w, "export", stem, 0, run, "warm", do_export)
    f.close()
    print(f"[INFO] export: benchmarked for {stem}")


# ---------------------------------------------------------------------------
# summarize (Appendix C tables)
# ---------------------------------------------------------------------------

STAGES = ["parse_pdfplumber", "ocr_tesseract", "tables",
          "layout", "parse_docling", "export"]
MODEL_STAGES = {"layout", "parse_docling"}

STAGE_NOTES = {
    "parse_pdfplumber": "text layer + word boxes",
    "ocr_tesseract": "forced OCR on every page; Tesseract memory not in Python RSS",
    "tables": "statement pages only (Camelot hybrid)",
    "layout": "warm pages, runs pooled; see cold-start table",
    "parse_docling": "warm pages, runs pooled; see cold-start table",
    "export": "whole filing; seconds / pages in filing",
}


def percentile(values: list[float], q: float) -> float | None:
    """Linear-interpolation percentile (q in 0..1)."""
    if not values:
        return None
    s = sorted(values)
    k = (len(s) - 1) * q
    lo = math.floor(k)
    hi = min(lo + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def fmt(v, nd=3) -> str:
    return "" if v is None else f"{v:.{nd}f}"


def summarize(out_dir: Path, pdf_path: Path) -> None:
    import pdfplumber
    with pdfplumber.open(pdf_path) as pdf:
        n_pages_filing = len(pdf.pages)

    summary_rows, cold_rows = [], []
    for stage in STAGES:
        path = out_dir / f"{stage}.csv"
        if not path.exists():
            print(f"[WARN] {path} missing, skipped")
            continue
        with open(path, encoding="utf-8", newline="") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue

        failures = [r for r in rows if r["status"] != "ok"]
        peak_rss = max(int(r["rss_mb"]) for r in rows)
        runs = sorted({int(r["run"]) for r in rows})

        if stage == "export":
            secs = [float(r["seconds"]) / n_pages_filing for r in rows]
            n_pages = n_pages_filing
        else:
            page_rows = [r for r in rows if int(r["page"]) > 0
                         and r["phase"] in ("warm", "cold_first_page")]
            use = [r for r in page_rows if r["phase"] == "warm"] if stage in MODEL_STAGES else page_rows
            secs = [float(r["seconds"]) for r in use]
            n_pages = len({int(r["page"]) for r in page_rows})

        summary_rows.append({
            "stage": stage,
            "pages": n_pages,
            "runs": ",".join(map(str, runs)),
            "n_timings": len(secs),
            "s_per_page_p50": round(percentile(secs, 0.50), 4) if secs else "",
            "s_per_page_p95": round(percentile(secs, 0.95), 4) if secs else "",
            "s_per_page_mean": round(sum(secs) / len(secs), 4) if secs else "",
            "peak_rss_mb": peak_rss,
            "failures": len(failures),
            "notes": STAGE_NOTES.get(stage, ""),
        })

        if stage in MODEL_STAGES:
            for run in runs:
                rr = [r for r in rows if int(r["run"]) == run]
                load = [float(r["seconds"]) for r in rr if r["phase"] == "model_load"]
                first = [float(r["seconds"]) for r in rr if r["phase"] == "cold_first_page"]
                warm = [float(r["seconds"]) for r in rr if r["phase"] == "warm"]
                cold_rows.append({
                    "stage": stage,
                    "run": run,
                    "model_load_s": round(load[0], 3) if load else "",
                    "cold_first_page_s": round(first[0], 3) if first else "",
                    "warm_p50_s": round(percentile(warm, 0.5), 3) if warm else "",
                    "warm_p95_s": round(percentile(warm, 0.95), 3) if warm else "",
                    "peak_rss_mb": max(int(r["rss_mb"]) for r in rr),
                })

    # write CSVs so every number in the report traces to a file
    with open(out_dir / "summary.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()), lineterminator="\n")
        w.writeheader()
        w.writerows(summary_rows)
    if cold_rows:
        with open(out_dir / "cold_start.csv", "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(cold_rows[0].keys()), lineterminator="\n")
            w.writeheader()
            w.writerows(cold_rows)

    # print Markdown tables ready to paste into reports/benchmarks.md
    print("\n## Per-stage throughput\n")
    print("| Stage | Pages | s/page p50 | s/page p95 | Peak RSS MB | Failures | Notes |")
    print("|---|---|---|---|---|---|---|")
    for r in summary_rows:
        print(f"| {r['stage']} | {r['pages']} | {fmt(r['s_per_page_p50'] or None)} | "
              f"{fmt(r['s_per_page_p95'] or None)} | {r['peak_rss_mb']} | {r['failures']} | {r['notes']} |")

    if cold_rows:
        print("\n## Cold vs warm (model stages)\n")
        print("| Stage | Run | Model load s | First page s | Warm p50 s | Warm p95 s | Peak RSS MB |")
        print("|---|---|---|---|---|---|---|")
        for r in cold_rows:
            print(f"| {r['stage']} | {r['run']} | {r['model_load_s']} | {r['cold_first_page_s']} | "
                  f"{r['warm_p50_s']} | {r['warm_p95_s']} | {r['peak_rss_mb']} |")

    print(f"\n[INFO] -> {out_dir / 'summary.csv'}"
          + (f", {out_dir / 'cold_start.csv'}" if cold_rows else ""))


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Part 10: cost and throughput benchmarks")
    ap.add_argument("--machine", action="store_true", help="Write machine.json and exit")
    ap.add_argument("--stage", type=str, default=None, choices=STAGES, help="Benchmark one stage")
    ap.add_argument("--summarize", action="store_true",
                    help="Read the CSVs, print the Appendix C tables, write summary.csv")
    ap.add_argument("--pdf", default="data/rendered/AAPL_10K_20250927.pdf",
                    help="PDF to benchmark (default: FY2025 filing)")
    ap.add_argument("--pages", default=None, help="Only these pages, e.g. 1,32-36 (default: all)")
    ap.add_argument("--run", type=int, default=1, help="Run number (1 or 2, for cold/warm)")
    ap.add_argument("--out", default="data/bench", help="Output folder (default: data/bench)")
    ap.add_argument("--params", default="params.yaml")
    args = ap.parse_args()

    out_dir = Path(args.out)

    if args.machine:
        write_machine_info(out_dir)
        return

    if args.summarize:
        summarize(out_dir, Path(args.pdf))
        return

    if args.stage:
        params = load_params(args.params)
        pdf_path = Path(args.pdf)
        stem = pdf_path.stem
        out_dir.mkdir(parents=True, exist_ok=True)
        print(f"[INFO] Benchmarking stage '{args.stage}' on {pdf_path.name}, run {args.run}")

        dispatch = {
            "parse_pdfplumber": bench_parse_pdfplumber,
            "ocr_tesseract": bench_ocr_tesseract,
            "tables": bench_tables,
            "layout": bench_layout,
            "parse_docling": bench_parse_docling,
            "export": bench_export,
        }
        dispatch[args.stage](pdf_path, stem, args.run, out_dir, params, parse_pages(args.pages))
        print(f"[INFO] Results -> {out_dir / (args.stage + '.csv')}")
        return

    ap.print_help()


if __name__ == "__main__":
    main()