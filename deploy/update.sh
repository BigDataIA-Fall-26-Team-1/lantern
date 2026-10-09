#!/usr/bin/env bash
# Move the deployed app to a branch or tag, e.g. bash deploy/update.sh app-ui | main | submission
set -euo pipefail
REF="${1:-main}"
cd "$(dirname "$0")/.."
git fetch --all --tags
git checkout "$REF"
if git show-ref --verify --quiet "refs/remotes/origin/$REF"; then
  git pull --ff-only origin "$REF"   # branches only; tags have nothing to pull
fi
.venv/bin/pip install -r app/requirements-app.txt
.venv/bin/dvc pull data/export data/rendered
sudo systemctl restart lantern-api lantern-ui
echo "Deployed $(git rev-parse --short HEAD) from $REF"
