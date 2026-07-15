#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."

source_setup() {
  local setup_file="$1"
  if [[ -f "${setup_file}" ]]; then
    set +u
    source "${setup_file}"
    set -u
  fi
}

if [[ -f /opt/ros/humble/setup.bash ]]; then
  set +u
  source /opt/ros/humble/setup.bash
  set -u
fi

# The persistent rclpy topic monitor runs inside this backend process. It needs
# custom message packages in PYTHONPATH/AMENT_PREFIX_PATH too, not only base ROS.
# MID360 commonly publishes livox_interfaces/msg/CustomMsg, which lives in mid_ws.
source_setup /data/sinuo_project/mid_ws/install/setup.bash

# Optional colon-separated list for additional workspaces containing custom msgs.
# Example:
# ROBOT_LAUNCHER_EXTRA_SETUPS=/data/ws1/install/setup.bash:/data/ws2/install/setup.bash ./scripts/run_dev.sh
if [[ -n "${ROBOT_LAUNCHER_EXTRA_SETUPS:-}" ]]; then
  IFS=':' read -r -a extra_setups <<< "${ROBOT_LAUNCHER_EXTRA_SETUPS}"
  for setup_file in "${extra_setups[@]}"; do
    source_setup "${setup_file}"
  done
fi

export ROBOT_LAUNCHER_CONFIG="${ROBOT_LAUNCHER_CONFIG:-$(pwd)/config/modules.yaml}"
HOST="${ROBOT_LAUNCHER_HOST:-0.0.0.0}"
PORT="${ROBOT_LAUNCHER_PORT:-8080}"
RELOAD="${ROBOT_LAUNCHER_RELOAD:-0}"

args=(app.main:app --host "${HOST}" --port "${PORT}" --no-access-log)
if [[ "${RELOAD}" == "1" ]]; then
  args+=(--reload)
fi

exec python3 -m uvicorn "${args[@]}"
