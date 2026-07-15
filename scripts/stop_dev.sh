#!/usr/bin/env bash
set -euo pipefail

PORT="${ROBOT_LAUNCHER_PORT:-8080}"
PATTERN="uvicorn app.main:app"

echo "Stopping Robot Web Launcher dev server on port ${PORT}..."

mapfile -t pids < <(pgrep -f "${PATTERN}" || true)
if (( ${#pids[@]} > 0 )); then
  for pid in "${pids[@]}"; do
    if [[ "${pid}" != "$$" ]]; then
      kill "${pid}" 2>/dev/null || true
    fi
  done
  sleep 1
fi

mapfile -t remaining < <(pgrep -f "${PATTERN}" || true)
if (( ${#remaining[@]} > 0 )); then
  echo "Some uvicorn processes are still alive, sending SIGKILL..."
  for pid in "${remaining[@]}"; do
    if [[ "${pid}" != "$$" ]]; then
      kill -9 "${pid}" 2>/dev/null || true
    fi
  done
fi

if command -v ss >/dev/null 2>&1; then
  if ss -ltn "( sport = :${PORT} )" | grep -q ":${PORT}"; then
    echo "Warning: port ${PORT} is still in use. Check with: sudo ss -ltnp | grep :${PORT}"
    exit 1
  fi
fi

echo "Stopped."
