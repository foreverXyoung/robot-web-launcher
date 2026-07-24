#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
LOG_FILE="${ROBOT_LAUNCHER_DESKTOP_LOG:-$(pwd)/runtime/logs/web_launcher.log}"
TITLE="${ROBOT_LAUNCHER_DESKTOP_TITLE:-拉风机器人控制台}"
mkdir -p "$(dirname "${LOG_FILE}")"
exec >> "${LOG_FILE}" 2>&1
printf '\n========== %(%F %T)T desktop stop ==========\n' -1

if command -v notify-send >/dev/null 2>&1; then
  notify-send "${TITLE}" "正在停止后台并清理所管理的模块..." >/dev/null 2>&1 || true
fi

if "$(pwd)/scripts/stop_dev.sh"; then
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "${TITLE}" "后台已停止" >/dev/null 2>&1 || true
  fi
else
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "${TITLE}" "停止未完全成功，请检查后台日志" >/dev/null 2>&1 || true
  fi
  exit 1
fi
