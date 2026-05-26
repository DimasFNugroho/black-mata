#!/usr/bin/env bash
# setup_gamepad.sh — Pair an Xbox wireless controller over Bluetooth on the
#                    Jetson and verify it shows up as an evdev input device.
#
# The script is fully interactive — it guides you through every step.
# You only need to:
#   1. Put the controller in pairing mode when prompted.
#   2. Pick it from the discovered device list.
#
# Usage (run from repo root):
#   bash tools/gamepad/setup_gamepad.sh [--verbose]
#
#   --verbose   Show raw bluetoothctl output instead of progress indicators.
#
# After setup, validate with:
#   python3 tools/gamepad/gamepad_test.py

PAIRING_SCAN_SECS=15
VERBOSE=0

# ── Argument parsing ──────────────────────────────────────────────────────────

for arg in "$@"; do
    case "$arg" in
        --verbose|-v) VERBOSE=1 ;;
        *) echo "Unknown option: $arg" >&2; exit 1 ;;
    esac
done

# ── Helpers ───────────────────────────────────────────────────────────────────

pause() {
    echo ""
    read -rp "  Press Enter when ready..."
    echo ""
}

step() {
    echo ""
    echo "── $* ──"
}

ok()  { echo "  [OK]  $*"; }
warn(){ echo "  [!!]  $*"; }
err() { echo ""; echo "  ERROR: $*" >&2; exit 1; }

find_hci() {
    hciconfig 2>/dev/null | grep -o 'hci[0-9]\+' | head -1 || true
}

# Build a block-character bar of a given width.
#   draw_bar FILLED TOTAL WIDTH
draw_bar() {
    local filled=$1 total=$2 width=$3
    local n_fill=$(( filled * width / total )) n_empty i bar=""
    n_empty=$(( width - n_fill ))
    for ((i=0; i<n_fill;  i++)); do bar+="█"; done
    for ((i=0; i<n_empty; i++)); do bar+="░"; done
    printf "%s" "${bar}"
}

# Show a countdown progress bar while a background PID is running.
# Exits as soon as the PID finishes or the timer reaches zero.
#   countdown_bar LABEL DURATION_SECS BG_PID
countdown_bar() {
    local label="$1" total="$2" pid="$3" width=24 i
    for ((i=0; i<=total; i++)); do
        printf "\r  %-18s [%s] %2ds " \
            "${label}" "$(draw_bar $i $total $width)" $(( total - i ))
        kill -0 "${pid}" 2>/dev/null || break
        [ $i -lt $total ] && sleep 1
    done
    printf "\r  %-18s [%s] done\n" "${label}" "$(draw_bar $total $total $width)"
}

# Show a timed progress bar + live phase label while a bluetooth operation runs.
# Advances the bar 1s per tick; reads LOG_FILE to update the phase label.
#   pair_progress LOG_FILE BG_PID TOTAL_SECS
pair_progress() {
    local logfile="$1" pid="$2" total="$3" width=24
    local elapsed=0 phase="Rediscovering" log

    while kill -0 "${pid}" 2>/dev/null; do
        log=$(cat "${logfile}" 2>/dev/null)

        if   echo "${log}" | grep -q 'Connected: yes'; then
            phase="Connected     ✓"
        elif echo "${log}" | grep -q 'Failed to connect'; then
            phase="Connect failed ✗"
        elif echo "${log}" | grep -q 'Attempting to connect'; then
            phase="Connecting"
        elif echo "${log}" | grep -q 'trust.*succeeded'; then
            phase="Trusted       ✓"
        elif echo "${log}" | grep -q 'Changing.*trust'; then
            phase="Trusting"
        elif echo "${log}" | grep -q 'Paired: yes\|Pairing successful'; then
            phase="Paired        ✓"
        elif echo "${log}" | grep -q 'Attempting to pair'; then
            phase="Pairing"
        elif echo "${log}" | grep -q 'Discovery stopped'; then
            phase="Rediscovered"
        fi

        local capped=$(( elapsed < total ? elapsed : total ))
        printf "\r  %-18s [%s] %2ds " \
            "${phase}" "$(draw_bar $capped $total $width)" $(( total - capped ))
        sleep 1
        elapsed=$(( elapsed + 1 ))
    done
    printf "\r  %-18s [%s] done\n" "${phase}" "$(draw_bar $total $total $width)"
}

# ── Step 1: python-evdev ──────────────────────────────────────────────────────

step "Step 1/6 — python-evdev"

command -v pip3 >/dev/null 2>&1 || \
    err "pip3 not found. Run: sudo apt-get install -y python3-pip"

if pip3 show evdev &>/dev/null; then
    EVDEV_VER=$(pip3 show evdev | grep '^Version' | awk '{print $2}')
    ok "evdev ${EVDEV_VER} already installed."
else
    pip3 install --user 'evdev>=1.6'
    pip3 show evdev &>/dev/null || err "evdev install failed."
    ok "evdev installed."
fi

# Resolve the Python interpreter that pip3 belongs to so the rest of the
# script (and the test command at the end) uses the same one.
PYTHON=$(pip3 --version | grep -oP '(?<=\()python[0-9.]+' | head -1)
PYTHON=${PYTHON:-python3}
ok "Using interpreter: ${PYTHON} ($(${PYTHON} --version 2>&1))"

# ── Step 2: Disable ERTM ─────────────────────────────────────────────────────

step "Step 2/6 — Disable Bluetooth ERTM"
echo "  (Required for Xbox BT pairing on Linux kernel < 5.12)"

MODPROBE_CONF="/etc/modprobe.d/bluetooth-xbox.conf"
if grep -q 'disable_ertm' "${MODPROBE_CONF}" 2>/dev/null; then
    ok "ERTM already disabled in ${MODPROBE_CONF}."
else
    echo 'options bluetooth disable_ertm=1' | sudo tee "${MODPROBE_CONF}" >/dev/null
    ok "Written: ${MODPROBE_CONF}"
fi
if [ -f /sys/module/bluetooth/parameters/disable_ertm ]; then
    echo 1 | sudo tee /sys/module/bluetooth/parameters/disable_ertm >/dev/null
    ok "ERTM disabled in running kernel."
fi

# ── Step 3: Bluetooth adapter ─────────────────────────────────────────────────

step "Step 3/6 — Bluetooth adapter"

sudo systemctl restart bluetooth
sleep 1

HCI=$(find_hci)

if [ -z "$HCI" ]; then
    warn "No Bluetooth adapter detected. Trying to load USB BT driver (btusb)..."
    sudo modprobe btusb 2>/dev/null || true
    sleep 2
    sudo systemctl restart bluetooth
    sleep 1
    HCI=$(find_hci)
fi

if [ -z "$HCI" ]; then
    echo ""
    echo "  Still no adapter. How is your BT hardware connected?"
    echo ""
    echo "    [0] USB BT dongle  — plug in the dongle and press Enter to retry"
    echo "    [1] ESP32 via USB  — run setup_esp32_hci.sh first, then retry"
    echo ""
    read -rp "  Choice [0/1]: " CHOICE
    case "$CHOICE" in
        1)
            err "Run this first to register the ESP32 as a BT adapter:
       bash tools/gamepad/setup_esp32_hci.sh
       Then re-run this script."
            ;;
        *)
            echo "  Retrying after USB dongle plug-in..."
            sudo modprobe btusb 2>/dev/null || true
            sleep 2
            sudo systemctl restart bluetooth
            sleep 1
            HCI=$(find_hci)
            ;;
    esac
fi

[ -z "$HCI" ] && err "No Bluetooth adapter found.
       USB BT dongle: check it appears in 'lsusb' and the btusb driver loaded.
       ESP32:         run 'bash tools/gamepad/setup_esp32_hci.sh' first."

sudo hciconfig "${HCI}" up 2>/dev/null || true
ok "Adapter ${HCI} is up."

# ── Step 4: Controller in pairing mode ───────────────────────────────────────

step "Step 4/6 — Put controller in pairing mode"
echo ""
echo "  Put the controller into Bluetooth pairing mode."
echo ""
echo "  Machenike G3 V2:"
echo "    Hold the Pair/Mode button until the LED flashes rapidly."
echo "    (Usually the small button on top; may require a long-press or"
echo "     a key combo — check your controller's manual.)"
echo ""
echo "  Xbox Wireless Controller:"
echo "    Press Xbox button to wake. Hold the Connect button (top edge,"
echo "    next to USB-C) ~3 s until the Xbox logo flashes rapidly."
echo ""
pause

# ── Step 5: Scan and pair ─────────────────────────────────────────────────────

step "Step 5/6 — Scan, pair, and connect"

echo "  Scanning for ${PAIRING_SCAN_SECS} seconds..."

if [ "${VERBOSE}" -eq 1 ]; then
    {
        echo "power on"
        echo "agent on"
        echo "default-agent"
        echo "scan on"
        sleep "${PAIRING_SCAN_SECS}"
        echo "scan off"
        sleep 1
    } | bluetoothctl 2>&1 || true
else
    {
        echo "power on"
        echo "agent on"
        echo "default-agent"
        echo "scan on"
        sleep "${PAIRING_SCAN_SECS}"
        echo "scan off"
        sleep 1
    } | bluetoothctl >/dev/null 2>&1 &
    SCAN_PID=$!
    countdown_bar "Scanning" "${PAIRING_SCAN_SECS}" "${SCAN_PID}"
    wait "${SCAN_PID}" 2>/dev/null || true
fi

echo ""
echo "  Devices discovered:"
echo ""

mapfile -t DEVICES < <(bluetoothctl devices 2>/dev/null | grep '^Device ')

if [ ${#DEVICES[@]} -eq 0 ]; then
    err "No devices found. Make sure the controller LED is flashing (pairing mode)
       and re-run the script."
fi

for i in "${!DEVICES[@]}"; do
    printf "    [%d] %s\n" "$i" "${DEVICES[$i]}"
done

echo ""
read -rp "  Enter the number of the gamepad: " IDX

if ! [[ "$IDX" =~ ^[0-9]+$ ]] || [ "$IDX" -ge "${#DEVICES[@]}" ]; then
    err "Invalid selection '${IDX}'."
fi

MAC=$(echo "${DEVICES[$IDX]}" | awk '{print $2}')
NAME=$(echo "${DEVICES[$IDX]}" | cut -d' ' -f3-)
echo ""
ok "Selected: ${NAME}  (${MAC})"

# Remove any stale pairing entry so 'pair' never hits AlreadyExists and
# br-connection-create-socket doesn't fail on mismatched link keys.
ALREADY_KNOWN=$(bluetoothctl info "${MAC}" 2>/dev/null | grep -c 'Device' || true)
if [ "${ALREADY_KNOWN}" -gt 0 ]; then
    warn "Stale pairing found — removing it first for a clean re-pair..."
    bluetoothctl remove "${MAC}" 2>/dev/null || true
    sleep 1
fi

echo ""
echo "  Pairing, trusting, and connecting..."
warn "Keep the controller in pairing mode (LED flashing rapidly)."
echo ""

# Re-scan within the same session so the agent is registered before 'pair'
# and the device is rediscovered in BlueZ's cache before we try to pair it.
if [ "${VERBOSE}" -eq 1 ]; then
    {
        echo "agent on"
        echo "default-agent"
        echo "scan on"
        sleep 5
        echo "scan off"
        sleep 1
        echo "pair ${MAC}"
        sleep 8
        echo "trust ${MAC}"
        sleep 1
        echo "connect ${MAC}"
        sleep 5
    } | bluetoothctl 2>&1 || true
else
    BTLOG=$(mktemp /tmp/btctl_pair.XXXXXX)
    trap 'rm -f "${BTLOG}"' EXIT

    {
        echo "agent on"
        echo "default-agent"
        echo "scan on"
        sleep 5
        echo "scan off"
        sleep 1
        echo "pair ${MAC}"
        sleep 8
        echo "trust ${MAC}"
        sleep 1
        echo "connect ${MAC}"
        sleep 5
    } | bluetoothctl >> "${BTLOG}" 2>&1 &
    BTPID=$!

    pair_progress "${BTLOG}" "${BTPID}" 20
    wait "${BTPID}" 2>/dev/null || true
    rm -f "${BTLOG}"
    trap - EXIT
fi

sleep 2

CONNECTED=$(bluetoothctl info "${MAC}" 2>/dev/null | grep -c 'Connected: yes' || true)
if [ "${CONNECTED}" -gt 0 ]; then
    ok "Controller connected."
else
    warn "Connection status unclear — the controller may still be connecting."
    warn "If it fails, try: bluetoothctl connect ${MAC}"
fi

# ── Step 6: Verify evdev input device ────────────────────────────────────────

step "Step 6/6 — Verify input device"

sleep 1
echo ""
echo "  Input devices visible right now:"
"${PYTHON}" tools/gamepad/gamepad_test.py --list 2>/dev/null | sed 's/^/    /'

echo ""
echo "─────────────────────────────────────────────────────────────────────────"
echo ""
echo "  Setup complete. Run the live input test:"
echo ""
echo "    ${PYTHON} tools/gamepad/gamepad_test.py"
echo ""
echo "  If the controller disconnects later, reconnect with:"
echo ""
echo "    bluetoothctl connect ${MAC}"
echo ""
