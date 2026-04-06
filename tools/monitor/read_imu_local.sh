#!/usr/bin/env bash
set -uo pipefail

# read_imu.sh — monitor BNO080 IMU serial output directly on ARM
#
# Expected CSV output from OpenCM9.04 firmware (imu_bno080_spi.ino):
#   QUAT,<ms>,<i>,<j>,<k>,<real>,<rad_accuracy>
#   ACCEL,<ms>,<x>,<y>,<z>          (m/s^2)
#   GYRO,<ms>,<x>,<y>,<z>           (rad/s)
#   LINACC,<ms>,<x>,<y>,<z>         (m/s^2, gravity removed)
#   GRAV,<ms>,<x>,<y>,<z>           (m/s^2)
#   MAG,<ms>,<x>,<y>,<z>            (uTesla)

PORT=""
BAUD="115200"

usage() {
  cat <<USAGE
Usage:
  $0 [options]

Options:
  --port <path>   Serial device (auto-detect if omitted)
  --baud <N>      Baud rate (default: $BAUD)
  -h, --help      Show this help

Example:
  $0 --port /dev/opencm
  $0                        # auto-detect port
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --port) PORT="${2:-}"; shift 2 ;;
    --baud) BAUD="${2:-}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

# Auto-detect serial port if not specified or if specified port doesn't exist
if [[ -n "$PORT" && ! -e "$PORT" ]]; then
  echo "# Configured port not found: $PORT — auto-detecting..." >&2
  PORT=""
fi

if [[ -z "$PORT" ]]; then
  PORT="$(ls /dev/opencm 2>/dev/null || true)"
fi
if [[ -z "$PORT" ]]; then
  PORT="$(ls /dev/serial/by-id/*ROBOTIS* 2>/dev/null | head -n1 || true)"
fi
if [[ -z "$PORT" ]]; then
  PORT="$(ls /dev/ttyACM* 2>/dev/null | head -n1 || true)"
fi
if [[ -z "$PORT" ]]; then
  PORT="$(ls /dev/ttyUSB* 2>/dev/null | head -n1 || true)"
fi
if [[ -z "$PORT" ]]; then
  echo "Could not find OpenCM serial port. Is it connected and powered?" >&2
  echo "Try: ls -l /dev/opencm /dev/serial/by-id/ /dev/ttyACM*" >&2
  exit 1
fi

echo "# Reading from $PORT at $BAUD baud (Ctrl+C to stop)"
echo "---"

stty -F "$PORT" "$BAUD" raw -echo 2>/dev/null || true
cat "$PORT"
