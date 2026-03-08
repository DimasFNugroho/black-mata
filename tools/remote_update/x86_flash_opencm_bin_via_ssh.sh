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
RETRIES="5"
RETRY_WAIT="20"

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
  --retries <N>           Max upload attempts before giving up (default: 5)
  --retry-wait <seconds>  Wait between retries (default: 20)
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
    --retries)
      RETRIES="${2:-}"
      shift 2
      ;;
    --retry-wait)
      RETRY_WAIT="${2:-}"
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

echo "[1/3] Copying bin to ARM: $ARM_HOST:$REMOTE_BIN"
scp "$BIN_FILE" "$ARM_HOST:$REMOTE_BIN"

echo "[2/3] Running uploader on ARM host (up to $RETRIES attempt(s), ${RETRY_WAIT}s between retries)"

_attempt=0
_flash_ok=0
while [[ $_attempt -lt $RETRIES ]]; do
  _attempt=$(( _attempt + 1 ))
  echo "Attempt $_attempt / $RETRIES ..."

  _rc=0
  ssh "$ARM_HOST" bash -s -- "$UPLOADER" "$ARM_PORT" "$BAUD" "$REMOTE_BIN" "$GO" "$TARGET" <<'EOS' || _rc=$?
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
  if [[ $_attempt -lt $RETRIES ]]; then
    echo "Waiting ${RETRY_WAIT}s before next attempt..." >&2
    sleep "$RETRY_WAIT"
  fi
done

if [[ $_flash_ok -eq 0 ]]; then
  echo "Flash failed after $RETRIES attempt(s)." >&2
  exit 1
fi

echo "[3/3] Flash complete."
