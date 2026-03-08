#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../remote_update/flash.conf"

# Defaults
ARM_HOST=""
ARM_PORT="/dev/ttyACM0"
BAUD="115200"

# Load config (ARM_HOST and ARM_PORT come from flash.conf)
if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=flash.conf
    source "$CONFIG_FILE"
fi

usage() {
  cat <<USAGE
Usage:
  $0 [options]

Config file:
  $CONFIG_FILE
  Set ARM_HOST and ARM_PORT there to avoid passing flags every time.

Options:
  --arm-host <user@ip>   ARM host running the OpenCM (required)
  --arm-port <path>      Serial device on ARM (default: $ARM_PORT)
  --baud <N>             Baud rate (default: $BAUD)
  -h, --help             Show this help

Example:
  $0 --arm-host mata-mata@192.168.1.50
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arm-host) ARM_HOST="${2:-}"; shift 2 ;;
    --arm-port) ARM_PORT="${2:-}"; shift 2 ;;
    --baud)     BAUD="${2:-}";     shift 2 ;;
    -h|--help)  usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [[ -z "$ARM_HOST" ]]; then
  echo "Missing ARM_HOST. Set it in flash.conf or pass --arm-host." >&2
  usage >&2
  exit 2
fi

# Open a single multiplexed SSH connection (one password prompt)
_ssh_socket="/tmp/black-mata-imu-ssh-$$"
_ssh_opts=(-o ControlMaster=auto -o ControlPath="$_ssh_socket" -o ControlPersist=60)

cleanup() {
  ssh "${_ssh_opts[@]}" -O exit "$ARM_HOST" 2>/dev/null || true
  rm -f "$_ssh_socket"
}
trap cleanup EXIT

echo "Connecting to $ARM_HOST..."
ssh "${_ssh_opts[@]}" -fN "$ARM_HOST"

echo "Streaming IMU data from $ARM_HOST:$ARM_PORT (Ctrl+C to stop)"
echo "---"

ssh "${_ssh_opts[@]}" "$ARM_HOST" \
  "stty -F '$ARM_PORT' $BAUD raw -echo 2>/dev/null; cat '$ARM_PORT'"
