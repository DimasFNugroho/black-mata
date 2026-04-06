#!/usr/bin/env bash
set -uo pipefail

# serial_monitor.sh — stream raw serial output from the OpenCM9.04
#
# Interactive: prompts for mode and port at runtime.
# No command-line arguments needed — just run the script.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/../remote_update/flash.conf"

# Defaults (may be overridden by flash.conf)
ARM_HOST=""
ARM_PORT=""
BAUD="115200"

if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=../remote_update/flash.conf
    source "$CONFIG_FILE"
fi

# ── Helpers ───────────────────────────────────────────────────────────────────

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

prompt_port() {
    # Show detected port as the default, let user confirm or override
    local detected
    detected="$(detect_port)"

    if [[ -n "$detected" ]]; then
        read -rp "Serial port [$detected]: " input
        echo "${input:-$detected}"
    else
        read -rp "Serial port (e.g. /dev/ttyACM0): " input
        echo "$input"
    fi
}

# ── Mode selection ────────────────────────────────────────────────────────────

echo ""
echo "Serial Monitor — OpenCM9.04"
echo "==========================="
echo " 1) Local  — read directly from serial port (run on Jetson)"
echo " 2) Remote — stream via SSH from Jetson (run on x86)"
echo ""
read -rp "Select mode [1/2]: " mode_choice

case "$mode_choice" in
    1) MODE="local" ;;
    2) MODE="remote" ;;
    *) echo "Invalid choice." >&2; exit 1 ;;
esac

# ── Local mode ────────────────────────────────────────────────────────────────

run_local() {
    echo ""
    PORT="$(prompt_port)"

    if [[ -z "$PORT" ]]; then
        echo "No serial port specified. Is the OpenCM connected?" >&2
        exit 1
    fi

    if [[ ! -e "$PORT" ]]; then
        echo "Port not found: $PORT" >&2
        exit 1
    fi

    echo ""
    echo "Reading from $PORT at $BAUD baud  (Ctrl+C to stop)"
    echo "---"
    stty -F "$PORT" "$BAUD" raw -echo 2>/dev/null || true
    cat "$PORT"
}

# ── Remote mode ───────────────────────────────────────────────────────────────

run_remote() {
    echo ""

    # Prompt for ARM_HOST, showing flash.conf value as default
    if [[ -n "$ARM_HOST" ]]; then
        read -rp "Jetson SSH address [$ARM_HOST]: " input
        ARM_HOST="${input:-$ARM_HOST}"
    else
        read -rp "Jetson SSH address (e.g. user@192.168.1.50): " ARM_HOST
    fi

    if [[ -z "$ARM_HOST" ]]; then
        echo "No SSH address provided." >&2
        exit 1
    fi

    # Prompt for port on Jetson, showing flash.conf value as default
    if [[ -n "$ARM_PORT" ]]; then
        read -rp "Serial port on Jetson [$ARM_PORT]: " input
        ARM_PORT="${input:-$ARM_PORT}"
    else
        read -rp "Serial port on Jetson (leave blank to auto-detect): " ARM_PORT
    fi

    echo ""
    echo "Connecting to $ARM_HOST..."

    # One multiplexed SSH connection — single password prompt
    _ssh_socket="/tmp/black-mata-serial-ssh-$$"
    _ssh_opts=(-o ControlMaster=auto -o ControlPath="$_ssh_socket" -o ControlPersist=60)

    cleanup() {
        ssh "${_ssh_opts[@]}" -O exit "$ARM_HOST" 2>/dev/null || true
        rm -f "$_ssh_socket"
    }
    trap cleanup EXIT

    ssh "${_ssh_opts[@]}" -fN "$ARM_HOST"

    echo "Streaming from $ARM_HOST  (Ctrl+C to stop)"
    echo "---"

    ssh "${_ssh_opts[@]}" "$ARM_HOST" bash -s -- "$ARM_PORT" "$BAUD" <<'EOS'
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

# ── Dispatch ──────────────────────────────────────────────────────────────────

case "$MODE" in
    local)  run_local ;;
    remote) run_remote ;;
esac
