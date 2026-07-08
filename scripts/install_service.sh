#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="${PROJECT_DIR:-/data/sinuo_project/robot_web_launcher}"
SERVICE_NAME="${SERVICE_NAME:-robot-web-launcher}"
SERVICE_FILE="${SERVICE_NAME}.service"
PYTHON_BIN="${PYTHON_BIN:-python3}"

if [[ ! -d "${PROJECT_DIR}" ]]; then
  echo "Project directory does not exist: ${PROJECT_DIR}" >&2
  exit 1
fi

cd "${PROJECT_DIR}"

echo "[1/5] Creating Python virtual environment..."
"${PYTHON_BIN}" -m venv .venv

echo "[2/5] Installing Python dependencies..."
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

echo "[3/5] Rendering systemd service..."
tmp_service="$(mktemp)"
sed \
  -e "s#WorkingDirectory=/data/sinuo_project/robot_web_launcher#WorkingDirectory=${PROJECT_DIR}#g" \
  -e "s#Environment=ROBOT_LAUNCHER_CONFIG=/data/sinuo_project/robot_web_launcher/config/modules.yaml#Environment=ROBOT_LAUNCHER_CONFIG=${PROJECT_DIR}/config/modules.yaml#g" \
  -e "s#ExecStart=/usr/bin/python3 -m uvicorn#ExecStart=${PROJECT_DIR}/.venv/bin/python -m uvicorn#g" \
  "systemd/robot-web-launcher.service" > "${tmp_service}"

echo "[4/5] Installing systemd service..."
sudo cp "${tmp_service}" "/etc/systemd/system/${SERVICE_FILE}"
rm -f "${tmp_service}"
sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"

echo "[5/5] Starting service..."
sudo systemctl restart "${SERVICE_NAME}"
sudo systemctl status "${SERVICE_NAME}" --no-pager

echo
echo "Done. Open http://<AGX_IP>:8080"

