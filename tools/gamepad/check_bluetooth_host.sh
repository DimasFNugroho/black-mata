#!/usr/bin/env bash
# check_bluetooth_host.sh — verify the one-time Bluetooth host commissioning.
#
# A manual "unit test" for a commissioning engineer. READ-ONLY: it changes
# nothing, just reports whether setup_bluetooth_host.sh did its job so an
# end-user can pair from the dashboard with no terminal and no sudo.
#
# Usage (on the Jetson, as the user that runs the dashboard):
#   bash tools/gamepad/check_bluetooth_host.sh [dashboard_user]
#
# Run it twice: once right after setup_bluetooth_host.sh, and once AFTER A
# REBOOT (without re-running setup) — a cold boot passing all checks is the
# real proof. Exit code is 0 only if every hard check passes.

set -u

DASH_USER="${1:-$USER}"          # the account the dashboard process runs as
PASS=0; WARN=0; FAIL=0

pass() { echo "  [PASS] $1"; PASS=$((PASS + 1)); }
warn() { echo "  [WARN] $1"; WARN=$((WARN + 1)); }
fail() { echo "  [FAIL] $1"; FAIL=$((FAIL + 1)); }
hint() { echo "         -> $1"; }

echo "Bluetooth host commissioning check"
echo "  dashboard user : ${DASH_USER}"
echo "  host           : $(hostname)"
echo

# 1. Bluetooth adapter present (driver level)
HCI="$(hciconfig 2>/dev/null | grep -o '^hci[0-9]*' | head -1)"
if [ -n "$HCI" ]; then
    pass "Adapter present: ${HCI}"
else
    fail "No Bluetooth adapter (hciN) found"
    hint "if using the RTL8761B dongle, run jetson_btrtl_8761b_fix.sh first"
fi

# 2. ERTM disabled persistently (modprobe.d)
if grep -qs disable_ertm /etc/modprobe.d/bluetooth-xbox.conf; then
    pass "ERTM disabled in modprobe.d (persists across reboots)"
else
    fail "ERTM not set in /etc/modprobe.d/bluetooth-xbox.conf"
    hint "re-run setup_bluetooth_host.sh"
fi

# 3. ERTM active in the running kernel (informational — not all kernels apply
#    the modprobe.d option at boot, and most controllers pair without it).
ERTM="$(cat /sys/module/bluetooth/parameters/disable_ertm 2>/dev/null || echo '?')"
case "$ERTM" in
    Y|1) pass "ERTM disabled in running kernel (${ERTM})" ;;
    *)   warn "ERTM not active at runtime (value '${ERTM}'). Only some controllers need it; the pairing test is the real check." ;;
esac

# 4. AutoEnable so the adapter powers on at boot
if grep -qsE '^[[:space:]]*AutoEnable[[:space:]]*=[[:space:]]*true' /etc/bluetooth/main.conf; then
    pass "AutoEnable=true in /etc/bluetooth/main.conf"
else
    fail "AutoEnable=true missing in /etc/bluetooth/main.conf"
    hint "re-run setup_bluetooth_host.sh"
fi

# 5. bluetooth.service enabled at boot
if [ "$(systemctl is-enabled bluetooth 2>/dev/null)" = enabled ]; then
    pass "bluetooth.service enabled at boot"
else
    fail "bluetooth.service is not enabled"
    hint "sudo systemctl enable bluetooth"
fi

# 6. Not rfkill-blocked
if rfkill list bluetooth 2>/dev/null | grep -qi 'blocked: *yes'; then
    fail "Bluetooth is rfkill-blocked"
    hint "sudo rfkill unblock bluetooth"
else
    pass "Bluetooth not rfkill-blocked"
fi

# 7. Dashboard user is in the 'bluetooth' group (config)
if id -nG "$DASH_USER" 2>/dev/null | tr ' ' '\n' | grep -qx bluetooth; then
    pass "${DASH_USER} is in the 'bluetooth' group"
else
    fail "${DASH_USER} is not in the 'bluetooth' group"
    hint "sudo usermod -aG bluetooth ${DASH_USER}"
fi

# 8. 'bluetooth' group ACTIVE in this shell (so a dashboard launched here has it)
if id -nG 2>/dev/null | tr ' ' '\n' | grep -qx bluetooth; then
    pass "'bluetooth' group is active in this shell/session"
else
    warn "'bluetooth' group not active in this shell"
    hint "log out/in or reboot, then start the dashboard, so it inherits the group"
fi

# 9. Adapter powered = ready to pair (the readiness payoff)
if printf 'show\nquit\n' | bluetoothctl 2>/dev/null | grep -q 'Powered: yes'; then
    pass "Adapter is powered (ready to pair)"
else
    fail "Adapter is not powered"
    hint "on a fresh boot AutoEnable should power it; if it stays off, re-check rfkill"
fi

echo
echo "Summary: ${PASS} pass, ${WARN} warn, ${FAIL} fail"
echo

if [ "$FAIL" -eq 0 ]; then
    echo "Host configuration looks good."
    echo
    echo "FINAL ACCEPTANCE TEST (do this once):"
    echo "  1. sudo reboot"
    echo "  2. when it comes back, re-run THIS script — do NOT run"
    echo "     setup_bluetooth_host.sh again. Every check should still pass."
    echo "  3. open the dashboard and pair from the GAMEPAD pill — no terminal,"
    echo "     no sudo. That proves an end-user gets a ready robot out of the box."
    exit 0
else
    echo "Fix the [FAIL] items above (usually: re-run setup_bluetooth_host.sh),"
    echo "then run this check again."
    exit 1
fi
