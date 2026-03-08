#!/usr/bin/env bash
# dxl_remote.sh
#
# Run a Dynamixel Python tool on the ARM host (Jetson) over SSH.
# The OpenCM9.04 must be connected to the Jetson running dxl_u2d2_bridge.ino.
#
# Usage:
#   ./dxl_remote.sh <tool> [tool-args...]
#
# Tools:
#   scan                         Scan for servos
#   monitor  --id <N>            Stream servo state as CSV
#   nudge    --id <N> [opts]     Nudge servo position
#   id_change --current <N> --new <M>  Change servo ID
#
# Examples:
#   ./dxl_remote.sh scan
#   ./dxl_remote.sh monitor --id 1
#   ./dxl_remote.sh nudge --id 1 --nudge 10 --once
#   ./dxl_remote.sh id_change --current 1 --new 5
#
# Config:
#   Set ARM_HOST and ARM_PORT in tools/remote_update/flash.conf

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../remote_update/flash.conf"

# Defaults
ARM_HOST=""
ARM_PORT="/dev/ttyACM0"

if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=../remote_update/flash.conf
    source "$CONFIG_FILE"
fi

usage() {
  cat <<USAGE
Usage:
  $0 <tool> [tool-args...]

Tools:
  scan                                   Scan bus for servos
  monitor  --id <N> [--interval <s>]     Stream servo state as CSV
  nudge    --id <N> [--nudge <deg>]      Nudge servo and return
  id_change --current <N> --new <M>      Change servo ID

Config (tools/remote_update/flash.conf):
  ARM_HOST  — SSH target (e.g. mata-mata@100.111.193.124)
  ARM_PORT  — Serial port on Jetson (e.g. /dev/ttyACM0)

Override:
  ARM_HOST=user@host $0 scan
  ARM_PORT=/dev/ttyUSB0 $0 monitor --id 1
USAGE
}

if [[ $# -eq 0 || "$1" == "-h" || "$1" == "--help" ]]; then
  usage; exit 0
fi

TOOL="$1"; shift

case "$TOOL" in
  scan|monitor|nudge|id_change) ;;
  *) echo "Unknown tool: $TOOL" >&2; usage >&2; exit 2 ;;
esac

if [[ -z "$ARM_HOST" ]]; then
  echo "Missing ARM_HOST. Set it in flash.conf or: ARM_HOST=user@host $0 $TOOL" >&2
  exit 2
fi

# ── SSH ControlMaster (one password prompt) ────────────────────────────────────
_ssh_socket="/tmp/black-mata-dxl-ssh-$$"
_ssh_opts=(-o ControlMaster=auto -o ControlPath="$_ssh_socket" -o ControlPersist=60)

cleanup() {
  ssh "${_ssh_opts[@]}" -O exit "$ARM_HOST" 2>/dev/null || true
  rm -f "$_ssh_socket"
}
trap cleanup EXIT

echo "Connecting to $ARM_HOST..."
ssh "${_ssh_opts[@]}" -fN "$ARM_HOST"

# ── Sync Python tools to Jetson ────────────────────────────────────────────────
REMOTE_DIR="~/.black-mata-dxl"
echo "Syncing tools to $ARM_HOST:$REMOTE_DIR..."
ssh "${_ssh_opts[@]}" "$ARM_HOST" "mkdir -p $REMOTE_DIR"
scp -o ControlPath="$_ssh_socket" \
    "$SCRIPT_DIR"/dxl_common.py \
    "$SCRIPT_DIR"/dxl_scan.py \
    "$SCRIPT_DIR"/dxl_monitor.py \
    "$SCRIPT_DIR"/dxl_nudge.py \
    "$SCRIPT_DIR"/dxl_id_change.py \
    "$ARM_HOST:$REMOTE_DIR/" 2>/dev/null

# ── Ensure dynamixel-sdk is installed (Python 3.6-compatible version) ──────────
ssh "${_ssh_opts[@]}" "$ARM_HOST" bash -s <<'EOS'
python3 -c "import dynamixel_sdk" 2>/dev/null && exit 0
echo "dynamixel-sdk not found on Jetson — installing..."
pip3 install "dynamixel-sdk<4.0" --user -q
EOS

# ── Auto-detect port if configured one is missing ─────────────────────────────
RESOLVED_PORT="$(ssh "${_ssh_opts[@]}" "$ARM_HOST" bash -s -- "$ARM_PORT" <<'EOS'
PORT="$1"
if [[ -n "$PORT" && -e "$PORT" ]]; then echo "$PORT"; exit 0; fi
PORT="$(ls /dev/serial/by-id/*ROBOTIS* 2>/dev/null | head -n1 || true)"
[[ -z "$PORT" ]] && PORT="$(ls /dev/ttyACM* 2>/dev/null | head -n1 || true)"
[[ -z "$PORT" ]] && PORT="$(ls /dev/ttyUSB* 2>/dev/null | head -n1 || true)"
if [[ -z "$PORT" ]]; then
  echo "ERROR: OpenCM not found on Jetson. Is it connected and powered?" >&2
  exit 1
fi
echo "$PORT"
EOS
)"

echo "Using port: $RESOLVED_PORT"
echo "---"

# ── Run the tool on the Jetson ────────────────────────────────────────────────
ssh "${_ssh_opts[@]}" "$ARM_HOST" \
    "cd $REMOTE_DIR && python3 dxl_${TOOL}.py --port '$RESOLVED_PORT' $*"
