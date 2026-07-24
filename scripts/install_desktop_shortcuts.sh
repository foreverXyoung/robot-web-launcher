#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")/.." && pwd -P)"
APPLICATIONS_DIR="${XDG_DATA_HOME:-${HOME}/.local/share}/applications"
DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"

START_FILE="robot-web-launcher.desktop"
STOP_FILE="robot-web-launcher-stop.desktop"
CONFIG_PATH="${PROJECT_DIR}/config/modules.yaml"
PID_FILE="${PROJECT_DIR}/runtime/web_launcher.pid"
LOCK_FILE="${PROJECT_DIR}/runtime/web_launcher.lock"
LOG_FILE="${PROJECT_DIR}/runtime/logs/web_launcher.log"
START_NAME="Robot Web Launcher"
START_NAME_ZH="拉风机器人控制台"
STOP_NAME="Stop Robot Web Launcher"
STOP_NAME_ZH="停止拉风机器人控制台"
COMMENT="Start or open the robot module control console"
COMMENT_ZH="启动或打开机器人模块控制台"
STOP_COMMENT="Gracefully stop the launcher and its managed modules"
STOP_COMMENT_ZH="安全停止控制台及其管理的模块"

if [[ "${1:-}" == "--arm" ]]; then
  START_FILE="robot-web-launcher-arm.desktop"
  STOP_FILE="robot-web-launcher-arm-stop.desktop"
  CONFIG_PATH="${PROJECT_DIR}/config/modules_arm.yaml"
  PID_FILE="${PROJECT_DIR}/runtime/web_launcher_arm.pid"
  LOCK_FILE="${PROJECT_DIR}/runtime/web_launcher_arm.lock"
  LOG_FILE="${PROJECT_DIR}/runtime/logs/web_launcher_arm.log"
  START_NAME="Robot Web Launcher Arm"
  START_NAME_ZH="机械臂机器人控制台"
  STOP_NAME="Stop Robot Web Launcher Arm"
  STOP_NAME_ZH="停止机械臂机器人控制台"
  COMMENT="Start or open the arm robot module control console"
  COMMENT_ZH="启动或打开机械臂机器人模块控制台"
  STOP_COMMENT="Gracefully stop the arm launcher and its managed modules"
  STOP_COMMENT_ZH="安全停止机械臂控制台及其管理的模块"
  shift
fi

remove_shortcuts() {
  rm -f "${APPLICATIONS_DIR}/${START_FILE}" "${APPLICATIONS_DIR}/${STOP_FILE}"
  if [[ -n "${DESKTOP_DIR}" && -d "${DESKTOP_DIR}" ]]; then
    rm -f "${DESKTOP_DIR}/${START_FILE}" "${DESKTOP_DIR}/${STOP_FILE}"
  fi
  echo "Desktop shortcuts removed: ${START_NAME_ZH} / ${STOP_NAME_ZH}"
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
Name=${START_NAME}
Name[zh_CN]=${START_NAME_ZH}
Comment=${COMMENT}
Comment[zh_CN]=${COMMENT_ZH}
Exec=/usr/bin/env ROBOT_LAUNCHER_CONFIG="${CONFIG_PATH}" ROBOT_LAUNCHER_PID_FILE="${PID_FILE}" ROBOT_LAUNCHER_LOCK_FILE="${LOCK_FILE}" ROBOT_LAUNCHER_DESKTOP_LOG="${LOG_FILE}" ROBOT_LAUNCHER_DESKTOP_TITLE="${START_NAME_ZH}" ROBOT_LAUNCHER_STRICT_PID_FILE=1 bash "${PROJECT_DIR}/scripts/start_desktop.sh"
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
Name=${STOP_NAME}
Name[zh_CN]=${STOP_NAME_ZH}
Comment=${STOP_COMMENT}
Comment[zh_CN]=${STOP_COMMENT_ZH}
Exec=/usr/bin/env ROBOT_LAUNCHER_CONFIG="${CONFIG_PATH}" ROBOT_LAUNCHER_PID_FILE="${PID_FILE}" ROBOT_LAUNCHER_LOCK_FILE="${LOCK_FILE}" ROBOT_LAUNCHER_DESKTOP_LOG="${LOG_FILE}" ROBOT_LAUNCHER_DESKTOP_TITLE="${STOP_NAME_ZH}" ROBOT_LAUNCHER_STRICT_PID_FILE=1 bash "${PROJECT_DIR}/scripts/stop_desktop.sh"
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

echo "Desktop shortcuts installed: ${START_NAME_ZH} / ${STOP_NAME_ZH}"
echo "Application menu: ${START_NAME_ZH} / ${STOP_NAME_ZH}"
if [[ -n "${DESKTOP_DIR}" && -d "${DESKTOP_DIR}" ]]; then
  echo "Desktop directory: ${DESKTOP_DIR}"
fi
