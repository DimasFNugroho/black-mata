#!/usr/bin/env bash
# setup_gamepad.sh — Pair a Bluetooth gamepad on the Jetson.
#
# This is a thin wrapper around tools/gamepad/bt_setup.py, which owns ALL the
# pairing logic (ERTM disable, adapter bring-up, scan, pair, trust, connect,
# verify) and renders its own terminal progress bars. Keeping the logic in one
# place means the dashboard setup modal (Phase H) and this terminal flow share
# exactly the same pairing code — fix a BlueZ quirk once, both benefit.
#
# Usage (run from anywhere):
#   bash tools/gamepad/setup_gamepad.sh [--verbose]
#
#   --verbose   Echo raw bluetoothctl output (useful when pairing fails).
#
# After setup, validate with:
#   python3 tools/gamepad/gamepad_test.py
#
# First time with an RTL8761B USB dongle on the Jetson? Run
# jetson_btrtl_8761b_fix.sh once so the kernel recognises it.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON="${PYTHON:-python3}"

exec "${PYTHON}" "${SCRIPT_DIR}/bt_setup.py" "$@"
