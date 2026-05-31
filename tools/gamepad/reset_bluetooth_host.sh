#!/usr/bin/env bash
# reset_bluetooth_host.sh — revert the Tier-2 Bluetooth commissioning to a clean
# default, so you can re-run the full commissioning procedure from scratch and
# verify it end-to-end on a single robot.
#
# Undoes what setup_bluetooth_host.sh did, and forgets all paired controllers:
#   - removes /etc/modprobe.d/bluetooth-xbox.conf (ERTM)
#   - sets AutoEnable=false in /etc/bluetooth/main.conf
#   - removes the dashboard user from the 'bluetooth' group
#   - clears all paired-device bonds
#
# It does NOT touch the Tier-1 driver fix (the RTL8761B adapter keeps working),
# and it leaves bluetooth.service enabled (the OS default). After running,
# REBOOT and then run commission_bluetooth.sh to walk the procedure again.
#
# To also revert Tier 1 (rarely needed — disables the dongle until re-fixed):
#   sudo rm -f /etc/modprobe.d/blacklist-rtk_btusb.conf
#   restore the backed-up btrtl.ko and /lib/firmware/rtl_bt firmware, then reboot.
#
# Usage (on the Jetson):
#   bash tools/gamepad/reset_bluetooth_host.sh [dashboard_user]

set -u

DASH_USER="${1:-${SUDO_USER:-$USER}}"
ERTM_CONF=/etc/modprobe.d/bluetooth-xbox.conf
BT_CONF=/etc/bluetooth/main.conf

step() { echo; echo "── $* ──"; }
ok()   { echo "  [OK]  $*"; }

echo "Reverting Tier-2 Bluetooth commissioning for user '${DASH_USER}'"

# 1. Forget all paired controllers (clean pairing state for the re-test).
step "1/4 Clear paired devices"
printf "power on\nremove '*'\nquit\n" | bluetoothctl >/dev/null 2>&1 || true
ok "Removed known device bonds (remove '*')"

# 2. Remove the ERTM modprobe drop-in (kernel reverts to default on reboot).
step "2/4 ERTM"
if [ -f "$ERTM_CONF" ]; then
    sudo rm -f "$ERTM_CONF"
    ok "Removed $ERTM_CONF"
else
    ok "Already absent: $ERTM_CONF"
fi

# 3. Turn off adapter auto-power (back to the default behaviour).
step "3/4 AutoEnable"
if grep -qsE '^[[:space:]]*AutoEnable[[:space:]]*=' "$BT_CONF"; then
    sudo sed -i -E 's/^[[:space:]]*#?[[:space:]]*AutoEnable[[:space:]]*=.*/AutoEnable=false/' "$BT_CONF"
    ok "Set AutoEnable=false in $BT_CONF"
else
    ok "No AutoEnable line in $BT_CONF (default)"
fi

# 4. Remove the dashboard user from the 'bluetooth' group.
step "4/4 bluetooth group"
if id -nG "$DASH_USER" 2>/dev/null | tr ' ' '\n' | grep -qx bluetooth; then
    sudo gpasswd -d "$DASH_USER" bluetooth >/dev/null
    ok "Removed ${DASH_USER} from 'bluetooth' group"
else
    ok "${DASH_USER} not in 'bluetooth' group"
fi

echo
echo "Done. Now:"
echo "  sudo reboot"
echo "  # after reboot, walk the procedure again from a clean baseline:"
echo "  bash tools/gamepad/commission_bluetooth.sh"
