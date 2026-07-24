#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
CONFIG_PATH="${ROBOT_LAUNCHER_CONFIG:-$(pwd)/config/modules.yaml}"
PYTHON_BIN="${ROBOT_LAUNCHER_PYTHON:-python3}"
server_output="$("${PYTHON_BIN}" scripts/runtime_config.py "${CONFIG_PATH}" server)"
mapfile -t server_config <<< "${server_output}"
PORT="${ROBOT_LAUNCHER_PORT:-${server_config[1]}}"
PID_FILE="${ROBOT_LAUNCHER_PID_FILE:-$(pwd)/runtime/web_launcher.pid}"
LOCK_FILE="${ROBOT_LAUNCHER_LOCK_FILE:-$(pwd)/runtime/web_launcher.lock}"
STOP_WAIT_SEC="${ROBOT_LAUNCHER_STOP_WAIT_SEC:-90}"
STRICT_PID_FILE="${ROBOT_LAUNCHER_STRICT_PID_FILE:-0}"
PATTERN="uvicorn app.main:app"

mkdir -p "$(dirname "${PID_FILE}")"
if command -v flock >/dev/null 2>&1; then
  exec 9>"${LOCK_FILE}"
  flock 9
fi

echo "Stopping Robot Web Launcher dev server on port ${PORT}..."

is_launcher_pid() {
  local pid="$1"
  [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
  kill -0 "${pid}" 2>/dev/null || return 1
  [[ -r "/proc/${pid}/cmdline" ]] || return 1
  tr '\0' ' ' < "/proc/${pid}/cmdline" | grep -q "${PATTERN}"
}

declare -A seen=()
pids=()
if [[ -f "${PID_FILE}" ]]; then
  read -r saved_pid < "${PID_FILE}" || true
  if is_launcher_pid "${saved_pid:-}"; then
    pids+=("${saved_pid}")
    seen["${saved_pid}"]=1
  fi
fi

if [[ "${STRICT_PID_FILE}" != "1" ]]; then
  while IFS= read -r pid; do
    [[ -z "${pid}" || "${pid}" == "$$" ]] && continue
    if [[ -z "${seen[${pid}]:-}" ]] && is_launcher_pid "${pid}"; then
      pids+=("${pid}")
      seen["${pid}"]=1
    fi
  done < <(pgrep -f '[u]vicorn app\.main:app' || true)
fi

if (( ${#pids[@]} > 0 )); then
  for pid in "${pids[@]}"; do
    kill -TERM "${pid}" 2>/dev/null || true
  done

  if [[ ! "${STOP_WAIT_SEC}" =~ ^[0-9]+$ ]]; then
    STOP_WAIT_SEC=90
  fi
  deadline=$((SECONDS + STOP_WAIT_SEC))
  while (( SECONDS < deadline )); do
    alive=0
    for pid in "${pids[@]}"; do
      if kill -0 "${pid}" 2>/dev/null; then
        alive=1
        break
      fi
    done
    (( alive == 0 )) && break
    sleep 0.25
  done

  remaining=()
  for pid in "${pids[@]}"; do
    if kill -0 "${pid}" 2>/dev/null; then
      remaining+=("${pid}")
    fi
  done

  if (( ${#remaining[@]} > 0 )); then
    echo "Graceful shutdown timed out; sending SIGKILL to: ${remaining[*]}"
    for pid in "${remaining[@]}"; do
      kill -KILL "${pid}" 2>/dev/null || true
    done
    sleep 0.5
  fi
fi

rm -f "${PID_FILE}"

if command -v ss >/dev/null 2>&1; then
  if ss -ltn "( sport = :${PORT} )" | grep -q ":${PORT}"; then
    echo "Warning: port ${PORT} is still in use. Check with: sudo ss -ltnp | grep :${PORT}"
    exit 1
  fi
fi

echo "Stopped."
