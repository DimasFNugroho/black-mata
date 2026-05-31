#!/usr/bin/env bash
# setup_bluetooth_host.sh — one-time Bluetooth host commissioning for the Jetson.
#
# Run this ONCE per robot (you need sudo). Afterwards the dashboard pairing
# modal — and the terminal setup_gamepad.sh — pair controllers with NO sudo, so
# an end-user with only a browser can re-pair / swap gamepads freely.
#
# This script owns all the root-level, persistent Bluetooth setup:
#   - ERTM disable (Xbox-style controllers on kernel < 5.12)
#   - rfkill unblock (systemd persists it across reboots)
#   - AutoEnable=true so the adapter powers on at every boot
#   - the dashboard user in the 'bluetooth' group (pair/connect without sudo)
#   - bluetooth.service enabled + started
#
# It does NOT fix the RTL8761B dongle driver — that is heavier and dongle
# specific. If no adapter is found, run jetson_btrtl_8761b_fix.sh first.
#
# Usage (on the Jetson):
#   bash tools/gamepad/setup_bluetooth_host.sh
#
# Idempotent — safe to re-run.

set -euo pipefail

ERTM_CONF=/etc/modprobe.d/bluetooth-xbox.conf
BT_CONF=/etc/bluetooth/main.conf
USER_TO_GRANT="${SUDO_USER:-$USER}"     # the user that runs the dashboard

step() { echo; echo "── $* ──"; }
ok()   { echo "  [OK]  $*"; }
warn() { echo "  [!!]  $*"; }

# 1. Adapter present? (driver-level — not fixed here)
step "1/6 Bluetooth adapter"
HCI="$(hciconfig 2>/dev/null | grep -o '^hci[0-9]*' | head -1)"
if [ -n "$HCI" ]; then
    ok "Adapter detected: $HCI"
else
    warn "No Bluetooth adapter found."
    warn "If using the RTL8761B USB dongle and the kernel logs 'unknown project"
    warn "id 14', run jetson_btrtl_8761b_fix.sh first, then re-run this script."
fi

# 2. ERTM disable (persists via modprobe.d)
step "2/6 Disable ERTM"
if grep -qs disable_ertm "$ERTM_CONF"; then
    ok "Already set in $ERTM_CONF"
else
    echo 'options bluetooth disable_ertm=1' | sudo tee "$ERTM_CONF" >/dev/null
    ok "Wrote $ERTM_CONF"
fi
if [ -e /sys/module/bluetooth/parameters/disable_ertm ]; then
    echo 1 | sudo tee /sys/module/bluetooth/parameters/disable_ertm >/dev/null || true
fi

# 3. rfkill unblock (systemd-rfkill restores this across reboots)
step "3/6 rfkill unblock"
sudo rfkill unblock bluetooth 2>/dev/null || true
ok "Bluetooth unblocked"

# 4. AutoEnable=true so the adapter powers on at boot (no runtime sudo needed)
step "4/6 Adapter auto-power (AutoEnable=true)"
if [ -f "$BT_CONF" ]; then
    if grep -qsE '^[[:space:]]*AutoEnable[[:space:]]*=[[:space:]]*true' "$BT_CONF"; then
        ok "AutoEnable already true"
    elif grep -qsE '^[[:space:]]*#?[[:space:]]*AutoEnable' "$BT_CONF"; then
        sudo sed -i -E 's/^[[:space:]]*#?[[:space:]]*AutoEnable[[:space:]]*=.*/AutoEnable=true/' "$BT_CONF"
        ok "Set AutoEnable=true"
    elif grep -qs '^\[Policy\]' "$BT_CONF"; then
        sudo sed -i '/^\[Policy\]/a AutoEnable=true' "$BT_CONF"
        ok "Added AutoEnable=true under [Policy]"
    else
        printf '\n[Policy]\nAutoEnable=true\n' | sudo tee -a "$BT_CONF" >/dev/null
        ok "Appended [Policy] AutoEnable=true"
    fi
else
    sudo mkdir -p "$(dirname "$BT_CONF")"
    printf '[Policy]\nAutoEnable=true\n' | sudo tee "$BT_CONF" >/dev/null
    ok "Created $BT_CONF with AutoEnable=true"
fi

# 5. Let the dashboard user pair without sudo (bluetooth group)
step "5/6 Grant '$USER_TO_GRANT' Bluetooth access"
if id -nG "$USER_TO_GRANT" 2>/dev/null | tr ' ' '\n' | grep -qx bluetooth; then
    ok "$USER_TO_GRANT already in 'bluetooth' group"
else
    sudo usermod -aG bluetooth "$USER_TO_GRANT"
    ok "Added $USER_TO_GRANT to 'bluetooth' (log out/in for it to take effect)"
fi

# 6. Enable + (re)start the service, then bring the adapter up and power it on
#    NOW (so commissioning doesn't require a reboot to take effect).
step "6/6 Enable bluetooth service"
sudo systemctl enable bluetooth >/dev/null 2>&1 || true
sudo systemctl restart bluetooth
sleep 1
[ -n "$HCI" ] && sudo hciconfig "$HCI" up 2>/dev/null || true
printf 'power on\nquit\n' | bluetoothctl >/dev/null 2>&1 || true
sleep 1
if printf 'show\nquit\n' | bluetoothctl 2>/dev/null | grep -q 'Powered: yes'; then
    ok "Adapter powered"
else
    warn "Adapter not powered yet. AutoEnable=true will power it at the next"
    warn "boot — a reboot is the most reliable way to finish commissioning."
fi

echo
echo "Done. End-users can now pair from the dashboard (GAMEPAD pill) without sudo."
echo "If '$USER_TO_GRANT' was just added to the 'bluetooth' group, restart the"
echo "dashboard process (or log out/in) so the new group membership applies."
