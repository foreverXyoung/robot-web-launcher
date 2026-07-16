#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
APPLICATIONS_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/applications"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
START_FILE="robot-web-launcher.desktop"
STOP_FILE="robot-web-launcher-stop.desktop"

remove_shortcuts() {
  rm -f "${APPLICATIONS_DIR}/${START_FILE}" "${APPLICATIONS_DIR}/${STOP_FILE}"
  if [[ -n "${DESKTOP_DIR}" && -d "${DESKTOP_DIR}" ]]; then
    rm -f "${DESKTOP_DIR}/${START_FILE}" "${DESKTOP_DIR}/${STOP_FILE}"
  fi
  echo "Desktop shortcuts removed."
}

if [[ "${1:-}" == "--remove" ]]; then
  remove_shortcuts
  exit 0
fi

mkdir -p "${APPLICATIONS_DIR}"
tmp_dir="$(mktemp -d)"
trap 'rm -rf "${tmp_dir}"' EXIT

cat > "${tmp_dir}/${START_FILE}" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Robot Web Launcher
Name[zh_CN]=摘钩机器人控制台
Comment=Start or open the robot module control console
Comment[zh_CN]=启动或打开机器人模块控制台
Exec=/usr/bin/env bash "${PROJECT_DIR}/scripts/start_desktop.sh"
Path=${PROJECT_DIR}
Icon=applications-system
Terminal=false
Categories=Utility;System;
StartupNotify=true
EOF

cat > "${tmp_dir}/${STOP_FILE}" <<EOF
[Desktop Entry]
Version=1.0
Type=Application
Name=Stop Robot Web Launcher
Name[zh_CN]=停止摘钩机器人控制台
Comment=Gracefully stop the launcher and its managed modules
Comment[zh_CN]=安全停止控制台及其管理的模块
Exec=/usr/bin/env bash "${PROJECT_DIR}/scripts/stop_desktop.sh"
Path=${PROJECT_DIR}
Icon=process-stop
Terminal=false
Categories=Utility;System;
StartupNotify=true
EOF

install -m 755 "${tmp_dir}/${START_FILE}" "${APPLICATIONS_DIR}/${START_FILE}"
install -m 755 "${tmp_dir}/${STOP_FILE}" "${APPLICATIONS_DIR}/${STOP_FILE}"

if [[ -n "${DESKTOP_DIR}" && -d "${DESKTOP_DIR}" ]]; then
  install -m 755 "${tmp_dir}/${START_FILE}" "${DESKTOP_DIR}/${START_FILE}"
  install -m 755 "${tmp_dir}/${STOP_FILE}" "${DESKTOP_DIR}/${STOP_FILE}"
  if command -v gio >/dev/null 2>&1; then
    gio set "${DESKTOP_DIR}/${START_FILE}" metadata::trusted true >/dev/null 2>&1 || true
    gio set "${DESKTOP_DIR}/${STOP_FILE}" metadata::trusted true >/dev/null 2>&1 || true
  fi
fi

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "${APPLICATIONS_DIR}" >/dev/null 2>&1 || true
fi

echo "Desktop shortcuts installed."
echo "Application menu: 摘钩机器人控制台 / 停止摘钩机器人控制台"
if [[ -n "${DESKTOP_DIR}" && -d "${DESKTOP_DIR}" ]]; then
  echo "Desktop directory: ${DESKTOP_DIR}"
fi
