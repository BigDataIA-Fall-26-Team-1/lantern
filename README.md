# Project LANTERN

## Project Summary

TODO

## Architecture Diagram

TODO

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