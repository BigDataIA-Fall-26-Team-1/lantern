# Project LANTERN

## Project Summary

TODO

## Deployed app

**URL:** http://52.15.107.141:8501 (Streamlit UI; the FastAPI backend runs on the same server, bound to localhost only)

The app is a read-only viewer over the pipeline outputs. It runs no pipeline stages and calls no paid APIs.

| Page | What it shows |
|---|---|
| Home | The two filings from `manifest.csv`, page and record counts, records by block type |
| Explorer | Any rendered page with every block's bbox drawn on it, and the page's JSONL records |
| Trace | Search a value or phrase and follow it to the page, the bbox, the JSONL record and the Markdown line with its provenance comment |
| Tables | Each extracted table: normalized values next to the raw cell strings, with extractor and scale |
| Reports | Every file in `reports/`, grouped by Part |

**Architecture:** one EC2 instance (Ubuntu 24.04, us-east-2, same region as the DVC remote). systemd runs two services: `lantern-api` (FastAPI, `app/api/main.py`, 127.0.0.1:8000) and `lantern-ui` (Streamlit, `app/ui/`, port 8501). No load balancer or NAT gateway. The security group allows port 8501 from anywhere and SSH from one IP.

**Data:** the server runs `dvc pull data/export data/rendered` from the public read-only DVC remote, so it holds no AWS or GitHub credentials.

**Deploying:** the org disables GitHub deploy keys, so code is copied from a clean local checkout with `rsync` (excluding `.venv/`, `data/`, the DVC cache and `.env`).
1. First time, on the server: `bash deploy/setup.sh` (installs Python 3.11, the app packages from `app/requirements-app.txt`, pulls the data, starts both services).
2. Updates: check out the branch or tag locally, re-run the rsync, then on the server run `bash deploy/update.sh`. It prints the deployed commit.

**Running locally:**
    pip install -r app/requirements-app.txt
    uvicorn app.api.main:app --port 8000
    LANTERN_API=http://127.0.0.1:8000 streamlit run app/ui/Home.py

## Architecture Diagram

<img src="docs/images/architecture.png" width="600">

## Reproduction Steps

git clone <your-repo> lantern && cd lantern
git checkout submission
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
dvc pull
dvc repro
dvc metrics show
pytest -q


## DVC Remote Access

The default remote is `public`, a read-only https URL, so `dvc pull` works
without any credentials.
 
To push data you need write access: get the AWS key from the team, then run
`dvc remote default --local store` and set the key with
`dvc remote modify --local store access_key_id ...` and
`dvc remote modify --local store secret_access_key ...`.
These settings go into `.dvc/config.local`, which is never committed.

## Expected Run Times

TODO

## Codelab

TODO

## Demo Video

TODO

## Part-to-Code Map

| Part | Code | Reports |
|------|------|---------|
| P0 | src/download.py, src/render.py | — |
| P1 | src/parse_text.py | — |
| P2 | src/tables.py | reports/tables_method.md |
| P3 | src/layout.py | reports/layout_audit.md |
| P4 | src/docling_parse.py | reports/docling_comparison.md |
| P5 | src/schema.py, src/export.py | — |
| P6 | — | reports/format_decision.md |
| P7 | src/managed/ | reports/build_vs_buy.md |
| P8 | dvc.yaml, .github/workflows/smoke.yml | — |
| P9 | src/evaluate.py | reports/eval.md, reports/metrics.json |
| P10 | src/bench.py | reports/benchmarks.md |
| P11 | src/xbrl.py | reports/xbrl.md |

## Generative AI Use Declaration

TODO

## Attestation

WE ATTEST THAT WE HAVEN'T USED ANY OTHER STUDENTS' WORK IN OUR ASSIGNMENT AND ABIDE BY THE POLICIES LISTED IN THE STUDENT HANDBOOK.

- Member 1: __%
- Member 2: __%
- Member 3: __%
