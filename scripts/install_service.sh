#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/data/sinuo_project/robot_web_launcher}"
SERVICE_NAME="${SERVICE_NAME:-robot-web-launcher}"
SERVICE_FILE="${SERVICE_NAME}.service"
PYTHON_BIN="${PYTHON_BIN:-python3}"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-$(id -un)}}"

if [[ ! -d "${PROJECT_DIR}" ]]; then
  echo "Project directory does not exist: ${PROJECT_DIR}" >&2
  exit 1
fi

cd "${PROJECT_DIR}"

echo "[1/5] Creating Python virtual environment..."
"${PYTHON_BIN}" -m venv --system-site-packages .venv

echo "[2/5] Installing Python dependencies..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

echo "[3/5] Rendering systemd service..."
tmp_service="$(mktemp)"
sed \
  -e "s#^User=.*#User=${SERVICE_USER}#" \
  -e "s#/data/sinuo_project/robot_web_launcher#${PROJECT_DIR}#g" \
  "systemd/robot-web-launcher.service" > "${tmp_service}"

echo "[4/5] Installing systemd service..."
sudo cp "${tmp_service}" "/etc/systemd/system/${SERVICE_FILE}"
rm -f "${tmp_service}"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"

echo "[5/5] Starting service..."
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl status "${SERVICE_NAME}" --no-pager

server_output="$(.venv/bin/python scripts/runtime_config.py config/modules.yaml server)"
mapfile -t server_config <<< "${server_output}"
echo
echo "Done. Open http://<AGX_IP>:${server_config[1]}"
