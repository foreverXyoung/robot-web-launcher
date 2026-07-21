#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

CONFIG_PATH="${ROBOT_LAUNCHER_CONFIG:-$(pwd)/config/modules.yaml}"
PYTHON_BIN="${ROBOT_LAUNCHER_PYTHON:-python3}"
PID_FILE="${ROBOT_LAUNCHER_PID_FILE:-$(pwd)/runtime/web_launcher.pid}"
LOCK_FILE="${ROBOT_LAUNCHER_LOCK_FILE:-$(pwd)/runtime/web_launcher.lock}"
LOG_FILE="${ROBOT_LAUNCHER_DESKTOP_LOG:-$(pwd)/runtime/logs/web_launcher.log}"
START_WAIT_SEC="${ROBOT_LAUNCHER_START_WAIT_SEC:-90}"

mkdir -p "$(dirname "${PID_FILE}")" "$(dirname "${LOG_FILE}")"
exec >> "${LOG_FILE}" 2>&1
printf '\n========== %(%F %T)T desktop start ==========\n' -1
if command -v flock >/dev/null 2>&1; then
  exec 9>"${LOCK_FILE}"
  flock 9
fi

server_output="$("${PYTHON_BIN}" scripts/runtime_config.py "${CONFIG_PATH}" server)"
mapfile -t server_config <<< "${server_output}"
HOST="${ROBOT_LAUNCHER_HOST:-${server_config[0]}}"
PORT="${ROBOT_LAUNCHER_PORT:-${server_config[1]}}"

if [[ "${HOST}" == "0.0.0.0" || "${HOST}" == "::" ]]; then
  browser_host="127.0.0.1"
else
  browser_host="${HOST}"
fi
if [[ "${browser_host}" == *:* ]]; then
  URL="http://[${browser_host}]:${PORT}"
else
  URL="http://${browser_host}:${PORT}"
fi

notify() {
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "拉风机器人控制台" "$1" >/dev/null 2>&1 || true
  fi
}

is_launcher_pid() {
  local pid="$1"
  [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" 2>/dev/null || return 1
  [[ -r "/proc/${pid}/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -q "uvicorn app.main:app"
}

find_launcher_pid() {
  local pid=""
  if [[ -f "${PID_FILE}" ]]; then
    read -r pid < "${PID_FILE}" || true
    if is_launcher_pid "${pid}"; then
      printf '%s\n' "${pid}"
      return 0
    fi
    rm -f "${PID_FILE}"
  fi

  pid="$(pgrep -f '[u]vicorn app\.main:app' | head -n 1 || true)"
  if [[ -n "${pid}" ]] && is_launcher_pid "${pid}"; then
    printf '%s\n' "${pid}"
    return 0
  fi
  return 1
}

open_console() {
  if command -v xdg-open >/dev/null 2>&1; then
    nohup xdg-open "${URL}" 9>&- >/dev/null 2>&1 < /dev/null &
  else
    notify "控制台地址：${URL}"
  fi
}

if launcher_pid="$(find_launcher_pid)"; then
  notify "后台已运行，正在打开网页"
  open_console
  exit 0
fi

nohup "$(pwd)/scripts/run_dev.sh" 9>&- >> "${LOG_FILE}" 2>&1 < /dev/null &
launcher_pid=$!
printf '%s\n' "${launcher_pid}" > "${PID_FILE}"

ready=0
if [[ ! "${START_WAIT_SEC}" =~ ^[0-9]+$ ]]; then
  START_WAIT_SEC=90
fi
deadline=$((SECONDS + START_WAIT_SEC))
while (( SECONDS < deadline )); do
  if ! kill -0 "${launcher_pid}" 2>/dev/null; then
    break
  fi
  if command -v curl >/dev/null 2>&1; then
    if curl --silent --fail --max-time 1 "${URL}/api/modules" >/dev/null; then
      ready=1
      break
    fi
  elif (echo >/dev/tcp/"${browser_host}"/"${PORT}") >/dev/null 2>&1; then
    ready=1
    break
  fi
  sleep 0.25
done

if [[ "${ready}" != "1" ]]; then
  if kill -0 "${launcher_pid}" 2>/dev/null; then
    kill -TERM "${launcher_pid}" 2>/dev/null || true
    for _ in 1 2 3 4 5 6 7 8 9 10; do
      kill -0 "${launcher_pid}" 2>/dev/null || break
      sleep 0.5
    done
    if kill -0 "${launcher_pid}" 2>/dev/null; then
      kill -KILL "${launcher_pid}" 2>/dev/null || true
    fi
  fi
  rm -f "${PID_FILE}"
  notify "启动失败，请查看 ${LOG_FILE}"
  exit 1
fi

notify "后台已启动，正在打开网页"
open_console
