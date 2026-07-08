#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
if [[ -f /opt/ros/humble/setup.bash ]]; then
  set +u
  source /opt/ros/humble/setup.bash
  set -u
fi
export ROBOT_LAUNCHER_CONFIG="${ROBOT_LAUNCHER_CONFIG:-$(pwd)/config/modules.yaml}"
exec python3 -m uvicorn app.main:app --host 0.0.0.0 --port 8080 --reload
