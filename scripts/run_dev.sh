#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ -f /opt/ros/humble/setup.bash ]]; then
  set +u
  source /opt/ros/humble/setup.bash
  set -u
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
