#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

export ROBOT_LAUNCHER_CONFIG="${ROBOT_LAUNCHER_CONFIG:-$(pwd)/config/modules.yaml}"
PYTHON_BIN="${ROBOT_LAUNCHER_PYTHON:-python3}"

source_setup() {
  local setup_file="$1"
  if [[ -f "${setup_file}" ]]; then
    set +u
    source "${setup_file}"
    set -u
  fi
}

server_output="$("${PYTHON_BIN}" scripts/runtime_config.py "${ROBOT_LAUNCHER_CONFIG}" server)"
mapfile -t server_config <<< "${server_output}"
setup_output="$("${PYTHON_BIN}" scripts/runtime_config.py "${ROBOT_LAUNCHER_CONFIG}" setups)"
mapfile -t configured_setups <<< "${setup_output}"

for setup_file in "${configured_setups[@]}"; do
  source_setup "${setup_file}"
done

# Optional colon-separated list for additional workspaces containing custom msgs.
# Example:
# ROBOT_LAUNCHER_EXTRA_SETUPS=/data/ws1/install/setup.bash:/data/ws2/install/setup.bash ./scripts/run_dev.sh
if [[ -n "${ROBOT_LAUNCHER_EXTRA_SETUPS:-}" ]]; then
  IFS=':' read -r -a extra_setups <<< "${ROBOT_LAUNCHER_EXTRA_SETUPS}"
  for setup_file in "${extra_setups[@]}"; do
    source_setup "${setup_file}"
  done
fi

HOST="${ROBOT_LAUNCHER_HOST:-${server_config[0]}}"
PORT="${ROBOT_LAUNCHER_PORT:-${server_config[1]}}"
RELOAD="${ROBOT_LAUNCHER_RELOAD:-0}"
PID_FILE="${ROBOT_LAUNCHER_PID_FILE:-$(pwd)/runtime/web_launcher.pid}"

args=(app.main:app --host "${HOST}" --port "${PORT}" --no-access-log)
if [[ "${RELOAD}" == "1" ]]; then
  args+=(--reload)
fi

mkdir -p "$(dirname "${PID_FILE}")"
printf '%s\n' "$$" > "${PID_FILE}"
exec "${PYTHON_BIN}" -m uvicorn "${args[@]}"
