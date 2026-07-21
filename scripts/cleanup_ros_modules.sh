#!/usr/bin/env bash
set -euo pipefail

MODE="${1:---dry-run}"
if [[ "${MODE}" != "--dry-run" && "${MODE}" != "--kill" && "${MODE}" != "--force" ]]; then
  echo "Usage: $0 [--dry-run|--kill|--force]"
  exit 2
fi

patterns=(
  "ros2 launch livox_ros_driver2 msg_MID360_launch.py"
  "livox_ros_driver2_node"
  "ros2 launch hipnuc_imu imu_spec_msg.launch.py"
  "hipnuc_imu/lib/hipnuc"
  "hipnuc_imu/talker"
  "ros2 launch wheeltec_gps_driver wheeltec_dual_rtk_driver_unicore.launch.py"
  "dual_rtk_driver_node"
  "ros2 launch fast_lio mapping.launch.py"
  "fast_lio/fastlio_mapping"
  "ros2 run keya_robot_controller robot_controller_node"
  "robot_controller_node"
  "ros2 launch orbbec_camera gemini_330_series.launch.py"
  "ros2 run domain_bridge_cpp domain_bridge_event_bundle"
  "domain_bridge_event_bundle"
  "导航-all2-单侧点云"
  "yolo_radar_cam_roi_icp"
  "ros2 launch vehicle_simulator system_real_robot.launch"
  "ros2 launch waypoint_example waypoint_example_garage.launch"
  "waypoint_example/waypointExample"
)

declare -A seen=()
pids=()
for pattern in "${patterns[@]}"; do
  while IFS= read -r pid; do
    [[ -z "${pid}" || "${pid}" == "$$" ]] && continue
    if [[ -z "${seen[${pid}]:-}" ]]; then
      seen["${pid}"]=1
      pids+=("${pid}")
    fi
  done < <(pgrep -f "${pattern}" || true)
done

if (( ${#pids[@]} == 0 )); then
  echo "No matching ROS module processes found."
  exit 0
fi

echo "Matching ROS module processes:"
ps -fp "${pids[@]}" || true

if [[ "${MODE}" == "--dry-run" ]]; then
  echo
  echo "Dry run only. Re-run with --kill to stop these processes."
  exit 0
fi

echo
echo "Sending SIGINT..."
for pid in "${pids[@]}"; do
  if kill -0 "${pid}" 2>/dev/null; then
    pgid="$(ps -o pgid= -p "${pid}" | tr -d ' ' || true)"
    if [[ -n "${pgid}" ]]; then
      kill -INT -"${pgid}" 2>/dev/null || kill -INT "${pid}" 2>/dev/null || true
    else
      kill -INT "${pid}" 2>/dev/null || true
    fi
  fi
done

sleep 3
remaining=()
for pid in "${pids[@]}"; do
  if kill -0 "${pid}" 2>/dev/null; then
    remaining+=("${pid}")
  fi
done

if (( ${#remaining[@]} > 0 )); then
  echo "Some processes are still alive, sending SIGTERM..."
  for pid in "${remaining[@]}"; do
    pgid="$(ps -o pgid= -p "${pid}" | tr -d ' ' || true)"
    if [[ -n "${pgid}" ]]; then
      kill -TERM -"${pgid}" 2>/dev/null || kill -TERM "${pid}" 2>/dev/null || true
    else
      kill -TERM "${pid}" 2>/dev/null || true
    fi
  done
fi

sleep 2
remaining=()
for pid in "${pids[@]}"; do
  if kill -0 "${pid}" 2>/dev/null; then
    remaining+=("${pid}")
  fi
done

if [[ ${#remaining[@]} -gt 0 || "${MODE}" == "--force" ]]; then
  echo "Some processes are still alive, sending SIGKILL..."
  for pid in "${remaining[@]}"; do
    pgid="$(ps -o pgid= -p "${pid}" | tr -d ' ' || true)"
    if [[ -n "${pgid}" ]]; then
      kill -KILL -"${pgid}" 2>/dev/null || kill -KILL "${pid}" 2>/dev/null || true
    else
      kill -KILL "${pid}" 2>/dev/null || true
    fi
  done
fi

sleep 1
remaining=()
declare -A final_seen=()
for pattern in "${patterns[@]}"; do
  while IFS= read -r pid; do
    [[ -z "${pid}" || "${pid}" == "$$" ]] && continue
    if [[ -z "${final_seen[${pid}]:-}" ]]; then
      final_seen["${pid}"]=1
      remaining+=("${pid}")
    fi
  done < <(pgrep -f "${pattern}" || true)
done

if (( ${#remaining[@]} > 0 )); then
  echo "Cleanup finished, but matching processes are still alive:"
  ps -fp "${remaining[@]}" || true
  exit 1
fi

echo "Cleanup complete. No matching ROS module processes remain."
