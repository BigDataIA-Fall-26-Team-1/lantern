#!/usr/bin/env bash
# Run on the server after copying the code over from a clean checkout (see README "Deployment").
set -euo pipefail
cd "$(dirname "$0")/.."
.venv/bin/pip install -r app/requirements-app.txt
.venv/bin/dvc pull data/export data/rendered
sudo systemctl restart lantern-api lantern-ui
echo "Deployed commit $(git rev-parse --short HEAD) ($(git rev-parse --abbrev-ref HEAD))"
