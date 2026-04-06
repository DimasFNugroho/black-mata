#!/usr/bin/env bash
set -uo pipefail

# serial_monitor.sh — stream raw serial output from the OpenCM9.04
#
# Two modes:
#   local   — read directly from a local serial port (run this on the Jetson)
#   remote  — SSH into the Jetson and forward the serial stream (run on x86)
#
# Mode is auto-detected: remote if ARM_HOST is configured, else local.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../remote_update/flash.conf"

# Defaults
MODE=""       # auto: remote if ARM_HOST is set, else local
ARM_HOST=""
PORT=""
BAUD="115200"

# Load config (may set ARM_HOST, ARM_PORT)
if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=../remote_update/flash.conf
    source "$CONFIG_FILE"
    # flash.conf uses ARM_PORT — map it to PORT if PORT not already set
    PORT="${PORT:-${ARM_PORT:-}}"
fi

usage() {
  cat <<USAGE
Usage:
  $0 [options]

Mode (default: remote if ARM_HOST is configured, else local):
  --local                Read directly from a local serial port
  --remote               Read via SSH from the Jetson

Options:
  --port <path>          Serial device to read from (auto-detect if omitted)
  --arm-host <user@ip>   Jetson SSH address (remote mode; overrides flash.conf)
  --baud <N>             Baud rate (default: $BAUD)
  -h, --help             Show this help

Config file:
  $CONFIG_FILE
  Set ARM_HOST and ARM_PORT there to avoid passing flags every time.

Examples:
  $0                                      # auto-detect mode and port
  $0 --local                              # force local, auto-detect port
  $0 --local --port /dev/ttyACM0         # force local, specific port
  $0 --remote                             # force remote, ARM_HOST from flash.conf
  $0 --remote --arm-host user@192.168.1.50
  $0 --remote --port /dev/ttyACM0        # specific port on the Jetson
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --local)    MODE="local" ;;
    --remote)   MODE="remote" ;;
    --port)     PORT="${2:-}"; shift ;;
    --arm-host) ARM_HOST="${2:-}"; shift ;;
    --baud)     BAUD="${2:-}"; shift ;;
    -h|--help)  usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
  shift
done

# Auto-detect mode
if [[ -z "$MODE" ]]; then
  if [[ -n "$ARM_HOST" ]]; then
    MODE="remote"
  else
    MODE="local"
  fi
fi

# ── Port auto-detection (shared logic) ───────────────────────────────────────
detect_port() {
  local p=""
  p="$(ls /dev/opencm 2>/dev/null || true)"
  [[ -n "$p" ]] && echo "$p" && return
  p="$(ls /dev/serial/by-id/*ROBOTIS* 2>/dev/null | head -n1 || true)"
  [[ -n "$p" ]] && echo "$p" && return
  p="$(ls /dev/ttyACM* 2>/dev/null | head -n1 || true)"
  [[ -n "$p" ]] && echo "$p" && return
  p="$(ls /dev/ttyUSB* 2>/dev/null | head -n1 || true)"
  [[ -n "$p" ]] && echo "$p" && return
  echo ""
}

# ── Local mode ───────────────────────────────────────────────────────────────
run_local() {
  if [[ -n "$PORT" && ! -e "$PORT" ]]; then
    echo "# Port not found: $PORT — auto-detecting..." >&2
    PORT=""
  fi

  if [[ -z "$PORT" ]]; then
    PORT="$(detect_port)"
  fi

  if [[ -z "$PORT" ]]; then
    echo "Could not find OpenCM serial port. Is it connected and powered?" >&2
    echo "Try: ls -l /dev/opencm /dev/serial/by-id/ /dev/ttyACM*" >&2
    exit 1
  fi

  echo "# [local] Reading from $PORT at $BAUD baud (Ctrl+C to stop)"
  echo "---"
  stty -F "$PORT" "$BAUD" raw -echo 2>/dev/null || true
  cat "$PORT"
}

# ── Remote mode ──────────────────────────────────────────────────────────────
run_remote() {
  if [[ -z "$ARM_HOST" ]]; then
    echo "Missing ARM_HOST. Set it in flash.conf or pass --arm-host." >&2
    usage >&2
    exit 2
  fi

  # Open a single multiplexed SSH connection (one password prompt)
  _ssh_socket="/tmp/black-mata-serial-ssh-$$"
  _ssh_opts=(-o ControlMaster=auto -o ControlPath="$_ssh_socket" -o ControlPersist=60)

  cleanup() {
    ssh "${_ssh_opts[@]}" -O exit "$ARM_HOST" 2>/dev/null || true
    rm -f "$_ssh_socket"
  }
  trap cleanup EXIT

  echo "Connecting to $ARM_HOST..."
  ssh "${_ssh_opts[@]}" -fN "$ARM_HOST"

  echo "# [remote] Streaming from $ARM_HOST (Ctrl+C to stop)"
  echo "---"

  ssh "${_ssh_opts[@]}" "$ARM_HOST" bash -s -- "$PORT" "$BAUD" <<'EOS'
set -euo pipefail
PORT="$1"
BAUD="$2"

detect_port() {
  local p=""
  p="$(ls /dev/opencm 2>/dev/null || true)"
  [[ -n "$p" ]] && echo "$p" && return
  p="$(ls /dev/serial/by-id/*ROBOTIS* 2>/dev/null | head -n1 || true)"
  [[ -n "$p" ]] && echo "$p" && return
  p="$(ls /dev/ttyACM* 2>/dev/null | head -n1 || true)"
  [[ -n "$p" ]] && echo "$p" && return
  p="$(ls /dev/ttyUSB* 2>/dev/null | head -n1 || true)"
  [[ -n "$p" ]] && echo "$p" && return
  echo ""
}

if [[ -n "$PORT" && ! -e "$PORT" ]]; then
  echo "# Port not found: $PORT — auto-detecting..." >&2
  PORT=""
fi

if [[ -z "$PORT" ]]; then
  PORT="$(detect_port)"
fi

if [[ -z "$PORT" ]]; then
  echo "Could not find OpenCM serial port on Jetson. Is it connected and powered?" >&2
  exit 1
fi

echo "# Reading from $PORT at $BAUD baud"
stty -F "$PORT" "$BAUD" raw -echo 2>/dev/null || true
cat "$PORT"
EOS
}

# ── Dispatch ─────────────────────────────────────────────────────────────────
case "$MODE" in
  local)  run_local ;;
  remote) run_remote ;;
esac
