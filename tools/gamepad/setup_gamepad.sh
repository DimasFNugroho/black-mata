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

# Installs evdev (>= 0.7.0). See tools/gamepad/gamepad_core.py for the version
# notes; in short, any build >= 0.7.0 works. Prefer apt (python3-evdev) on the
# Jetson, fall back to pip elsewhere. Detect by import — apt installs are
# invisible to `pip3 show`.
PYTHON=python3

if ${PYTHON} -c 'import evdev' 2>/dev/null; then
    EVDEV_VER=$(${PYTHON} -c 'import pkg_resources; \
print(pkg_resources.get_distribution("evdev").version)' 2>/dev/null || echo '?')
    ok "evdev ${EVDEV_VER} already installed."
elif sudo apt-get install -y python3-evdev >/dev/null 2>&1 \
        && ${PYTHON} -c 'import evdev' 2>/dev/null; then
    ok "evdev installed (apt python3-evdev)."
elif command -v pip3 >/dev/null 2>&1 && pip3 install --user evdev >/dev/null 2>&1 \
        && ${PYTHON} -c 'import evdev' 2>/dev/null; then
    ok "evdev installed (pip fallback)."
else
    err "Could not install evdev. Try: sudo apt-get install -y python3-evdev"
fi

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

# Bring the adapter fully up. On a headless box (no desktop GUI to toggle
# Bluetooth) the adapter is often rfkill-soft-blocked and/or not powered at the
# BlueZ level. Without a BlueZ `power on`, later `remove`/`pair`/`connect` fail
# with org.bluez.Error.NotReady — the desktop GUI normally does this for you.
sudo rfkill unblock bluetooth 2>/dev/null || true
sudo hciconfig "${HCI}" up 2>/dev/null || true
# Feed commands via stdin, NOT as one-shot args: on the Jetson's BlueZ 5.48,
# `bluetoothctl power on` / `show` as arguments drop into interactive mode and
# block on stdin (the script gets suspended). The piped form works everywhere.
printf 'power on\nquit\n' | bluetoothctl >/dev/null 2>&1 || true
sleep 1
if printf 'show\nquit\n' | bluetoothctl 2>/dev/null | grep -q 'Powered: yes'; then
    ok "Adapter ${HCI} is up and powered."
else
    warn "Adapter ${HCI} is up but not powered. Pairing may fail with NotReady;
       try: printf 'power on\\nquit\\n' | bluetoothctl"
fi

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

# Clear BlueZ's device cache first. This is a (re-)pairing tool, so we don't
# want stale entries from past scans (TVs, lightstrips, old bonds) cluttering
# the selection list — only what's actually advertising now should show up.
# `remove *` wipes all known devices; best-effort, piped + timed so it can't
# block on BlueZ 5.48.
echo "  Clearing previously-known devices..."
printf 'remove *\nquit\n' | timeout 10 bluetoothctl >/dev/null 2>&1 || true

echo "  Scanning for ${PAIRING_SCAN_SECS} seconds..."

# Keep one scan session running a few seconds PAST our device query. BlueZ
# evicts freshly-discovered, unpaired devices the instant discovery stops (the
# [DEL] lines at the end of a scan). Rather than rely on a `bluetoothctl
# devices` one-shot (which on BlueZ 5.48 can drop into interactive mode and
# return nothing), we capture the scan session's full output and parse the
# devices from its own [NEW]/[CHG] Device lines. Those are written while the
# scan runs, so they survive the post-scan purge. `quit` + `timeout` ensure the
# session always ends and can never block the script.
SCAN_OUT=$(mktemp /tmp/btctl_scan.XXXXXX)
{
    echo "power on"
    echo "agent on"
    echo "default-agent"
    echo "scan on"
    sleep "${PAIRING_SCAN_SECS}"
    echo "scan off"
    sleep 1
    echo "quit"
} | timeout "$(( PAIRING_SCAN_SECS + 10 ))" bluetoothctl > "${SCAN_OUT}" 2>&1 &
SCAN_PID=$!

countdown_bar "Scanning" "${PAIRING_SCAN_SECS}" "${SCAN_PID}"
wait "${SCAN_PID}" 2>/dev/null || true
[ "${VERBOSE}" -eq 1 ] && cat "${SCAN_OUT}"

echo ""
echo "  Devices discovered:"
echo ""

# One "Device <MAC> <name>" line per unique MAC, parsed from the scan output.
# Prefer a real name (from a [NEW] label or a "Name:" change) over property
# lines like RSSI/TxPower; fall back to the MAC as the label. mawk-compatible
# (no {n} intervals or gawk extensions).
mapfile -t DEVICES < <(
    sed -E 's/\x1b\[[0-9;?]*[A-Za-z]//g' "${SCAN_OUT}" \
    | awk '
        /\[NEW\] Device / || /\[CHG\] Device / {
            mac=""; for (i=1;i<=NF;i++) if ($i=="Device") { mac=$(i+1); ns=i+2; break }
            if (mac !~ /:/) next
            rest=""; for (j=ns;j<=NF;j++) rest=rest (j>ns?" ":"") $j
            if (rest ~ /^Name:/)            { sub(/^Name:[ \t]*/,"",rest); nm[mac]=rest }
            else if (rest ~ /^[A-Za-z][A-Za-z0-9]*:/) { if (!(mac in nm)) nm[mac]=mac }
            else                            { if (!(mac in nm) || nm[mac]==mac) nm[mac]=(rest==""?mac:rest) }
            if (!(mac in ord)) { ord[mac]=1; seq[++n]=mac }
        }
        END { for (k=1;k<=n;k++) printf "Device %s %s\n", seq[k], nm[seq[k]] }
    '
)
rm -f "${SCAN_OUT}"

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

echo ""
warn "Make sure the controller is STILL in pairing mode (LED flashing rapidly)."
warn "Xbox pads drop out of pairing mode in ~20-30s — if it stopped flashing,"
warn "hold the Connect button ~3s again RIGHT NOW before continuing."
read -r -p "  Press Enter to pair... " _
echo ""
echo "  Pairing and trusting..."
echo ""

# Sequence proven to work on this Jetson (BlueZ 5.48):
#   remove -> power on -> agent -> scan on (rediscover) -> pair -> trust -> scan off
# Scan stays ON through pair/trust: BlueZ purges unpaired devices the instant
# discovery stops, so pairing AFTER `scan off` races the purge and fails.
# Then connect SEPARATELY with retries: an Xbox pad drops once right after
# `trust` (Connected: no), so the connect must land after that drop to stick.
_pairtrust_script() {
#    echo "remove ${MAC}";  sleep 1
    echo "power on"
    echo "agent on"
    echo "default-agent"
    echo "scan on";        sleep 6
    echo "pair ${MAC}";    sleep 5   # pair WHILE scanning so BlueZ doesn't
    echo "trust ${MAC}";   sleep 2    # purge the device before we pair it
    echo "scan off"
    echo "quit"
}

BTLOG=$(mktemp /tmp/btctl_pair.XXXXXX)
trap 'rm -f "${BTLOG}"' EXIT

if [ "${VERBOSE}" -eq 1 ]; then
    _pairtrust_script | timeout 40 bluetoothctl 2>&1 | tee "${BTLOG}" || true
else
    _pairtrust_script | timeout 45 bluetoothctl >> "${BTLOG}" 2>&1 &
    BTPID=$!
    pair_progress "${BTLOG}" "${BTPID}" 20
    wait "${BTPID}" 2>/dev/null || true
fi

# Did pairing actually complete? If not, the controller almost certainly left
# pairing mode — skip the connect retries instead of hammering a dead device.
if grep -q 'Pairing successful\|Paired: yes' "${BTLOG}" 2>/dev/null; then
    PAIRED=1
else
    PAIRED=0
fi
rm -f "${BTLOG}"
trap - EXIT

CONNECTED=0
if [ "${PAIRED}" -eq 0 ]; then
    warn "Pairing did not complete — the controller likely left pairing mode."
    warn "Hold the Connect button (~3s, fast flash) and re-run the script."
else
    # An Xbox pad drops once right after `trust`, so connect must land after
    # that drop to hold. Check first (pairing may have auto-connected and held),
    # then connect + retry. `timeout` wraps every bluetoothctl call — including
    # `info`, which can otherwise drop into interactive mode and hang the script
    # even though the controller is connected.
    echo ""
    for attempt in 1 2 3; do
        if timeout 6 bluetoothctl info "${MAC}" 2>/dev/null | grep -q 'Connected: yes'; then
            CONNECTED=1
            break
        fi
        warn "Connecting (attempt ${attempt})..."
        { echo "connect ${MAC}"; sleep 4; echo "quit"; } \
            | timeout 10 bluetoothctl >/dev/null 2>&1 || true
        sleep 1
    done
    # Catch a connect that landed on the final attempt.
    if [ "${CONNECTED}" -eq 0 ] \
       && timeout 6 bluetoothctl info "${MAC}" 2>/dev/null | grep -q 'Connected: yes'; then
        CONNECTED=1
    fi
fi

if [ "${CONNECTED}" -eq 1 ]; then
    ok "Controller connected and holding."
else
    warn "Connection didn't hold after retries. Wake the controller and retry:"
    warn "  printf 'connect ${MAC}\\nquit\\n' | bluetoothctl"
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
