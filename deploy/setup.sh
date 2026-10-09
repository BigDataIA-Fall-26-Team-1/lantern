#!/usr/bin/env bash
# One-time setup of the LANTERN app on Ubuntu 24.04 (EC2). Run from ~/lantern.
set -euo pipefail
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv
python3.11 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r app/requirements-app.txt
.venv/bin/dvc pull data/export data/rendered   # public https remote, no credentials
sudo cp deploy/lantern-api.service deploy/lantern-ui.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now lantern-api lantern-ui
