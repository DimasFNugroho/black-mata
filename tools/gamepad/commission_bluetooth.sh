#!/usr/bin/env bash
# commission_bluetooth.sh — resumable guide for commissioning Bluetooth on a
# black-mata. Run it, do the ONE action it prints, reboot if it says to, then
# run it again — repeat until it reports "fully commissioned".
#
# READ-ONLY: it changes nothing itself; it detects the current state and tells
# you the next step. Safe to run any number of times. The full written
# procedure is in docs/commissioning_bluetooth.md.
#
# Usage (on the Jetson, as the user that runs the dashboard):
#   bash tools/gamepad/commission_bluetooth.sh [dashboard_user]

set -u

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DASH_USER="${1:-$USER}"

rule() { echo "------------------------------------------------------------"; }

# ── Read-only state probes ──────────────────────────────────────────────────
hci="$(hciconfig 2>/dev/null | grep -o '^hci[0-9]*' | head -1)"

ertm_conf=false
grep -qs disable_ertm /etc/modprobe.d/bluetooth-xbox.conf && ertm_conf=true

ertm_run=false
case "$(cat /sys/module/bluetooth/parameters/disable_ertm 2>/dev/null)" in
    Y|1) ertm_run=true ;;
esac

autoenable=false
grep -qsE '^[[:space:]]*AutoEnable[[:space:]]*=[[:space:]]*true' \
    /etc/bluetooth/main.conf && autoenable=true

svc=false
[ "$(systemctl is-enabled bluetooth 2>/dev/null)" = enabled ] && svc=true

in_group=false
id -nG "$DASH_USER" 2>/dev/null | tr ' ' '\n' | grep -qx bluetooth && in_group=true

grp_active=false
id -nG 2>/dev/null | tr ' ' '\n' | grep -qx bluetooth && grp_active=true

powered=false
printf 'show\nquit\n' | bluetoothctl 2>/dev/null | grep -q 'Powered: yes' && powered=true

echo "Black-Mata Bluetooth commissioning guide"
echo "  user: ${DASH_USER}   host: $(hostname)"
rule

# ── Tier 1 — driver: is there an adapter at all? ────────────────────────────
if [ -z "$hci" ]; then
    echo "STATE: no Bluetooth adapter detected (driver level)."
    echo
    echo "NEXT — Tier 1 (once per OS image / kernel update):"
    echo "  1. Plug in the RTL8761B USB dongle."
    echo "  2. Put rtl8761bu_fw.bin + rtl8761bu_config.bin next to the fix script"
    echo "     (it can fetch the kernel source itself, or supply btrtl.c/.h)."
    echo "  3. bash ${HERE}/jetson_btrtl_8761b_fix.sh"
    echo "  4. sudo reboot, then run this guide again."
    exit 0
fi

# ── Tier 2 — persistent commissioning config present? ───────────────────────
if ! $ertm_conf || ! $autoenable || ! $svc || ! $in_group; then
    echo "STATE: adapter present (${hci}); commissioning config is incomplete:"
    $ertm_conf  || echo "  - ERTM not set in modprobe.d"
    $autoenable || echo "  - AutoEnable=true not set in main.conf"
    $svc        || echo "  - bluetooth.service not enabled at boot"
    $in_group   || echo "  - ${DASH_USER} not in the 'bluetooth' group"
    echo
    echo "NEXT — Tier 2 (once per robot):"
    echo "  1. bash ${HERE}/setup_bluetooth_host.sh"
    echo "  2. sudo reboot, then run this guide again."
    exit 0
fi

# ── Applied on a clean boot? (config exists but may need a reboot to take) ──
# NOTE: ERTM runtime is intentionally NOT a gate. Some kernels (incl. this
# Jetson's L4T 4.9) don't apply the modprobe.d ERTM option at boot, and most
# controllers pair fine without it — so blocking on it would loop forever.
if ! $powered || ! $grp_active; then
    echo "STATE: commissioning config is in place but not fully applied yet:"
    $powered    || echo "  - adapter not powered (AutoEnable applies at boot)"
    $grp_active || echo "  - 'bluetooth' group not active in this login session"
    echo
    echo "NEXT: sudo reboot, then run this guide again (no setup needed)."
    exit 0
fi

# ── Fully commissioned ──────────────────────────────────────────────────────
echo "STATE: fully commissioned and ready."
$ertm_run || echo "  (note: ERTM not active at runtime — fine unless a"
$ertm_run || echo "   controller refuses to pair; see setup_bluetooth_host.sh)"
echo
if [ -x "${HERE}/check_bluetooth_host.sh" ]; then
    echo "Running the formal verification check..."
    rule
    bash "${HERE}/check_bluetooth_host.sh" "$DASH_USER" || true
    rule
fi
echo "FINAL ACCEPTANCE (do once):"
echo "  Open the dashboard and pair a controller from the GAMEPAD pill —"
echo "  no terminal, no sudo. If that works, this black-mata is signed off."
exit 0
