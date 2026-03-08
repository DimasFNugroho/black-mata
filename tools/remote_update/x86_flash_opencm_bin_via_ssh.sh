#!/usr/bin/env bash
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/flash.conf"

# Hardcoded defaults
ARM_HOST=""
BIN_FILE=""
ARM_PORT=""
REMOTE_BIN="/tmp/opencm_flash.bin"
UPLOADER="/usr/local/bin/opencm9.04_ld_armhf"
BAUD="57600"
GO="1"
TARGET="opencm"
FLASH_TIMEOUT="20"

# Load config file if present (overrides hardcoded defaults above)
if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=flash.conf
    source "$CONFIG_FILE"
fi

usage() {
  cat <<USAGE
Usage:
  $0 --arm-host <user@ip> --bin <local.bin> [options]

Config file:
  $CONFIG_FILE
  Set any option as a shell variable (ARM_HOST, BIN_FILE, ARM_PORT, etc.).
  CLI arguments override config file values.

Options:
  --arm-port <path>       ARM serial device (auto-detect if omitted)
  --remote-bin <path>     Temp path on ARM for uploaded bin (default: /tmp/opencm_flash.bin)
  --uploader <path>       Uploader path on ARM (default: /usr/local/bin/opencm9.04_ld_armhf)
  --baud <N>              Baudrate for uploader (default: 57600)
  --go <0|1>              Send go command after flash (default: 1)
  --target <name>         Target arg for uploader (default: opencm)
  --timeout <seconds>     Stop retrying after this many seconds (default: 20)
  -h, --help              Show this help

Example:
  $0 --arm-host mata-mata@192.168.1.50 \\
     --bin /home/user/opencm_blink.ino.bin
USAGE
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --arm-host)
      ARM_HOST="${2:-}"
      shift 2
      ;;
    --bin)
      BIN_FILE="${2:-}"
      shift 2
      ;;
    --arm-port)
      ARM_PORT="${2:-}"
      shift 2
      ;;
    --remote-bin)
      REMOTE_BIN="${2:-}"
      shift 2
      ;;
    --uploader)
      UPLOADER="${2:-}"
      shift 2
      ;;
    --baud)
      BAUD="${2:-}"
      shift 2
      ;;
    --go)
      GO="${2:-}"
      shift 2
      ;;
    --target)
      TARGET="${2:-}"
      shift 2
      ;;
    --timeout)
      FLASH_TIMEOUT="${2:-}"
      shift 2
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown argument: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$ARM_HOST" || -z "$BIN_FILE" ]]; then
  echo "Missing required arguments." >&2
  usage >&2
  exit 2
fi

if [[ ! -f "$BIN_FILE" ]]; then
  echo "Local .bin not found: $BIN_FILE" >&2
  exit 2
fi

# Open a single multiplexed SSH connection (one password prompt).
_ssh_socket="/tmp/black-mata-ssh-$$"
_ssh_opts=( -o ControlMaster=auto -o ControlPath="$_ssh_socket" -o ControlPersist=60 )

cleanup() {
  ssh "${_ssh_opts[@]}" -O exit "$ARM_HOST" 2>/dev/null || true
  rm -f "$_ssh_socket"
}
trap cleanup EXIT

echo "[0/3] Opening SSH connection to $ARM_HOST"
ssh "${_ssh_opts[@]}" -fN "$ARM_HOST"

echo "[1/3] Copying bin to ARM: $ARM_HOST:$REMOTE_BIN"
scp -o ControlMaster=no -o ControlPath="$_ssh_socket" "$BIN_FILE" "$ARM_HOST:$REMOTE_BIN"

echo "[2/3] Running uploader on ARM host (timeout: ${FLASH_TIMEOUT}s)"

_attempt=0
_flash_ok=0
_deadline=$(( SECONDS + FLASH_TIMEOUT ))

while [[ $SECONDS -lt $_deadline ]]; do
  _attempt=$(( _attempt + 1 ))
  _elapsed=$(( SECONDS - ( _deadline - FLASH_TIMEOUT ) ))
  echo "Attempt $_attempt (${_elapsed}s elapsed) ..."

  _rc=0
  ssh "${_ssh_opts[@]}" "$ARM_HOST" bash -s -- "$UPLOADER" "$ARM_PORT" "$BAUD" "$REMOTE_BIN" "$GO" "$TARGET" <<'EOS' || _rc=$?
set -euo pipefail
UP="$1"
PORT="$2"
BAUD="$3"
BIN="$4"
GO="$5"
TARGET="$6"

if [[ ! -x "$UP" ]]; then
  echo "Uploader not executable on ARM: $UP" >&2
  exit 2
fi
if [[ ! -f "$BIN" ]]; then
  echo "Bin file missing on ARM: $BIN" >&2
  exit 2
fi

if [[ -n "$PORT" && ! -e "$PORT" ]]; then
  echo "Requested ARM port does not exist: $PORT. Falling back to auto-detect." >&2
  PORT=""
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
  echo "Could not auto-detect ARM serial port. Pass --arm-port explicitly." >&2
  exit 2
fi

echo "Using ARM serial port: $PORT"

"$UP" "$PORT" "$BAUD" "$BIN" "$GO" "$TARGET"
EOS

  if [[ $_rc -eq 0 ]]; then
    _flash_ok=1
    break
  fi

  echo "Attempt $_attempt failed (exit code $_rc)." >&2
done

if [[ $_flash_ok -eq 0 ]]; then
  echo "Flash failed: timeout after ${FLASH_TIMEOUT}s (${_attempt} attempt(s))." >&2
  exit 1
fi

echo "[3/3] Flash complete."
