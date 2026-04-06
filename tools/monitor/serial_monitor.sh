#!/usr/bin/env bash
set -uo pipefail

# serial_monitor.sh — stream raw serial output from the OpenCM9.04
#
# Interactive: prompts for mode, host, and port at runtime.
# Configure known hosts and ports in monitor.conf (same directory).

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/monitor.conf"

# Defaults (overridden by monitor.conf)
KNOWN_HOSTS=()
KNOWN_PORTS=()
BAUD="115200"

if [[ -f "$CONFIG_FILE" ]]; then
    # shellcheck source=monitor.conf
    source "$CONFIG_FILE"
fi

# ── Generic selection menu ────────────────────────────────────────────────────
# Usage: select_from_list "Prompt" item1 item2 ...
# Prints the selected item to stdout; all display output goes to stderr
# so the menu is visible even when this function is called inside $().
select_from_list() {
    local prompt="$1"
    shift
    local items=("$@")
    local count="${#items[@]}"

    echo "" >&2
    echo "$prompt" >&2
    for (( i=0; i<count; i++ )); do
        echo "  $((i+1))) ${items[$i]}" >&2
    done
    echo "" >&2

    local choice
    while true; do
        read -rp "Select [1-$count]: " choice </dev/tty
        if [[ "$choice" =~ ^[0-9]+$ ]] && (( choice >= 1 && choice <= count )); then
            echo "${items[$((choice-1))]}"
            return
        fi
        echo "  Invalid choice, enter a number between 1 and $count." >&2
    done
}

# ── Port detection for local mode ─────────────────────────────────────────────
detect_local_ports() {
    local found=()
    local p=""

    p="$(ls /dev/opencm 2>/dev/null || true)"
    [[ -n "$p" ]] && found+=("$p")

    while IFS= read -r dev; do
        found+=("$dev")
    done < <(ls /dev/serial/by-id/*ROBOTIS* 2>/dev/null || true)

    while IFS= read -r dev; do
        found+=("$dev")
    done < <(ls /dev/ttyACM* 2>/dev/null || true)

    while IFS= read -r dev; do
        found+=("$dev")
    done < <(ls /dev/ttyUSB* 2>/dev/null || true)

    # Merge with KNOWN_PORTS, deduplicate, preserve order
    local seen=()
    local all=("${found[@]}" "${KNOWN_PORTS[@]}")
    for item in "${all[@]}"; do
        local dup=0
        for s in "${seen[@]+"${seen[@]}"}"; do
            [[ "$s" == "$item" ]] && dup=1 && break
        done
        [[ "$dup" -eq 0 ]] && seen+=("$item")
    done

    printf '%s\n' "${seen[@]}"
}

# ── Mode selection ─────────────────────────────────────────────────────────────

echo ""
echo "Serial Monitor — OpenCM9.04"
echo "==========================="
echo "  1) Local  — read directly from serial port (run on Jetson)"
echo "  2) Remote — stream via SSH from Jetson (run on x86)"
echo ""
read -rp "Select mode [1/2]: " mode_choice

case "$mode_choice" in
    1) MODE="local" ;;
    2) MODE="remote" ;;
    *) echo "Invalid choice." >&2; exit 1 ;;
esac

# ── Local mode ─────────────────────────────────────────────────────────────────

run_local() {
    mapfile -t port_list < <(detect_local_ports)

    if [[ "${#port_list[@]}" -eq 0 ]]; then
        echo "No serial ports found. Is the OpenCM connected?" >&2
        exit 1
    fi

    PORT="$(select_from_list "Select serial port:" "${port_list[@]}")"

    echo ""
    echo "Reading from $PORT at $BAUD baud  (Ctrl+C to stop)"
    echo "---"
    stty -F "$PORT" "$BAUD" raw -echo 2>/dev/null || true
    cat "$PORT"
}

# ── Remote mode ────────────────────────────────────────────────────────────────

run_remote() {
    if [[ "${#KNOWN_HOSTS[@]}" -eq 0 ]]; then
        echo "No hosts configured. Add entries to $CONFIG_FILE." >&2
        exit 1
    fi

    ARM_HOST="$(select_from_list "Select Jetson SSH address:" "${KNOWN_HOSTS[@]}")"
    ARM_PORT="$(select_from_list "Select serial port on Jetson:" "${KNOWN_PORTS[@]}")"

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

# Fall back to auto-detection if the configured port is missing
if [[ -n "$PORT" && ! -e "$PORT" ]]; then
    echo "# Port not found: $PORT — auto-detecting..." >&2
    PORT=""
fi

if [[ -z "$PORT" ]]; then
    PORT="$(ls /dev/opencm 2>/dev/null | head -n1 || true)"
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
    echo "Could not find OpenCM serial port on Jetson. Is it connected and powered?" >&2
    exit 1
fi

echo "# Reading from $PORT at $BAUD baud"
stty -F "$PORT" "$BAUD" raw -echo 2>/dev/null || true
cat "$PORT"
EOS
}

# ── Dispatch ───────────────────────────────────────────────────────────────────

case "$MODE" in
    local)  run_local ;;
    remote) run_remote ;;
esac
