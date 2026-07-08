#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/data/sinuo_project/robot_web_launcher}"
SERVICE_NAME="${SERVICE_NAME:-robot-web-launcher}"

cd "${PROJECT_DIR}"

echo "[1/4] Pulling latest code..."
git pull --ff-only

echo "[2/4] Updating dependencies..."
.venv/bin/python -m pip install -r requirements.txt

echo "[3/4] Validating config..."
.venv/bin/python - <<'PY'
from app.config import load_config
c = load_config("config/modules.yaml")
print(f"Loaded {len(c.modules)} modules:")
for key in c.modules:
    print(f"  - {key}")
PY

echo "[4/4] Restarting service..."
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl status "${SERVICE_NAME}" --no-pager

