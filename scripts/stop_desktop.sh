#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
mkdir -p runtime/logs
exec >> runtime/logs/web_launcher.log 2>&1
printf '\n========== %(%F %T)T desktop stop ==========\n' -1

if command -v notify-send >/dev/null 2>&1; then
  notify-send "摘钩机器人控制台" "正在停止后台并清理所管理的模块..." >/dev/null 2>&1 || true
fi

if "$(pwd)/scripts/stop_dev.sh"; then
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "摘钩机器人控制台" "后台已停止" >/dev/null 2>&1 || true
  fi
else
  if command -v notify-send >/dev/null 2>&1; then
    notify-send "摘钩机器人控制台" "停止未完全成功，请检查后台日志" >/dev/null 2>&1 || true
  fi
  exit 1
fi
