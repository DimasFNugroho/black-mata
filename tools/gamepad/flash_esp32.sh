#!/usr/bin/env bash
# flash_esp32.sh — Build and flash the ESP32 HCI controller firmware.
#
# Run this on your development machine (x86), NOT on the Jetson.
# The ESP32 must be connected to this machine via USB for flashing.
# After flashing, unplug the ESP32 from here and plug it into the Jetson.
#
# Requires ESP-IDF v5.0 or later (ESP32-C6 support requires v5.x):
#   . $HOME/esp/esp-idf/export.sh
#
# ESP-IDF install guide:
#   https://docs.espressif.com/projects/esp-idf/en/latest/esp32/get-started/
#
# Usage (from repo root):
#   bash tools/gamepad/flash_esp32.sh
#   bash tools/gamepad/flash_esp32.sh --port /dev/ttyUSB1

set -e

SCRIPT_DIR="$(dirname "$(realpath "$0")")"
FIRMWARE_DIR="${SCRIPT_DIR}/../../firmware/esp32_hci"
DEFAULT_PORT="/dev/ttyUSB0"

# ── Helpers ───────────────────────────────────────────────────────────────────

step() { echo ""; echo "── $* ──"; }
ok()   { echo "  [OK]  $*"; }
err()  { echo ""; echo "  ERROR: $*" >&2; exit 1; }

# ── Parse args ────────────────────────────────────────────────────────────────

PORT="${DEFAULT_PORT}"
while [[ $# -gt 0 ]]; do
    case "$1" in
        --port|-p) PORT="$2"; shift 2 ;;
        *) echo "Unknown argument: $1"; exit 1 ;;
    esac
done

# ── Step 1: Check ESP-IDF ─────────────────────────────────────────────────────

step "Step 1/4 — Check ESP-IDF"

if [ -z "${IDF_PATH}" ]; then
    echo ""
    echo "  ESP-IDF is not sourced. Run:"
    echo ""
    echo "    . \$HOME/esp/esp-idf/export.sh"
    echo ""
    echo "  If ESP-IDF is not installed yet:"
    echo "    https://docs.espressif.com/projects/esp-idf/en/latest/esp32/get-started/"
    exit 1
fi

IDF_VER=$(idf.py --version 2>/dev/null || echo "unknown")
ok "ESP-IDF: ${IDF_VER}  (${IDF_PATH})"

# ── Step 2: Check ESP32 port ──────────────────────────────────────────────────

step "Step 2/4 — Check ESP32 serial port"

if [ ! -e "${PORT}" ]; then
    echo ""
    echo "  Port ${PORT} not found. Available USB serial ports:"
    ls /dev/ttyUSB* /dev/ttyACM* 2>/dev/null | sed 's/^/    /' || echo "    (none)"
    echo ""
    read -rp "  Enter the correct port (e.g. /dev/ttyUSB1): " PORT
fi

[ ! -e "${PORT}" ] && err "Port ${PORT} still not found."
ok "Using port: ${PORT}"

# ── Step 3: Build ─────────────────────────────────────────────────────────────

step "Step 3/4 — Build"
echo "  Firmware: ${FIRMWARE_DIR}"
echo ""

cd "${FIRMWARE_DIR}"
idf.py build

# ── Step 4: Flash ─────────────────────────────────────────────────────────────

step "Step 4/4 — Flash"
echo ""
idf.py -p "${PORT}" flash

echo ""
echo "─────────────────────────────────────────────────────────────────────────"
echo ""
echo "  Flash complete."
echo ""
echo "  Next steps:"
echo "    1. Unplug the ESP32 from this machine."
echo "    2. Plug it into the Jetson USB port."
echo "    3. On the Jetson, run:"
echo ""
echo "         bash tools/gamepad/setup_esp32_hci.sh"
echo ""
