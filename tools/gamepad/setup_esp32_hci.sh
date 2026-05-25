#!/usr/bin/env bash
# setup_esp32_hci.sh — Register the ESP32 as a Linux Bluetooth adapter (hci0)
#                      via hciattach, and install a systemd service so it
#                      comes up automatically at every boot.
#
# Run this on the Jetson after the ESP32 firmware has been flashed:
#   bash tools/gamepad/setup_esp32_hci.sh
#
# Once complete, hci0 is live and setup_gamepad.sh can pair the controller.
# To switch to a real USB BT dongle later: stop + disable this service,
# plug in the dongle, and run setup_gamepad.sh — nothing else changes.

BAUD=115200
SERVICE_NAME="hci-esp32"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

# ── Helpers ───────────────────────────────────────────────────────────────────

step() { echo ""; echo "── $* ──"; }
ok()   { echo "  [OK]  $*"; }
warn() { echo "  [!!]  $*"; }
err()  { echo ""; echo "  ERROR: $*" >&2; exit 1; }

find_hci() {
    hciconfig 2>/dev/null | grep -o 'hci[0-9]\+' | head -1 || true
}

# ── Step 1: Find ESP32 serial port ───────────────────────────────────────────

step "Step 1/4 — Find ESP32 serial port"

mapfile -t PORTS < <(ls /dev/ttyUSB* 2>/dev/null || true)

if [ ${#PORTS[@]} -eq 0 ]; then
    err "No /dev/ttyUSB* devices found.
       Make sure the ESP32 is plugged into the Jetson USB port and the
       firmware has been flashed (bash tools/gamepad/flash_esp32.sh on x86)."
fi

if [ ${#PORTS[@]} -eq 1 ]; then
    PORT="${PORTS[0]}"
    ok "Auto-detected: ${PORT}"
else
    echo "  Multiple USB serial ports found:"
    for i in "${!PORTS[@]}"; do
        printf "    [%d] %s\n" "$i" "${PORTS[$i]}"
    done
    echo ""
    read -rp "  Enter the number for the ESP32 port: " IDX
    if ! [[ "$IDX" =~ ^[0-9]+$ ]] || [ "$IDX" -ge "${#PORTS[@]}" ]; then
        err "Invalid selection '${IDX}'."
    fi
    PORT="${PORTS[$IDX]}"
    ok "Selected: ${PORT}"
fi

# Ensure the current user can access the port
if ! groups | grep -qw 'dialout'; then
    sudo usermod -aG dialout "${USER}"
    warn "Added ${USER} to dialout group. A logout/login is needed for this to
       take effect in new shells, but sudo will be used for now."
fi
sudo chmod a+rw "${PORT}"

# ── Step 2: Test hciattach ────────────────────────────────────────────────────

step "Step 2/4 — Test hciattach"

# Kill any stale hciattach on this port
sudo pkill -f "hciattach ${PORT}" 2>/dev/null || true
sleep 1

echo "  Attaching ${PORT} at ${BAUD} baud (noflow)..."
sudo hciattach "${PORT}" any "${BAUD}" noflow
sleep 2

HCI=$(find_hci)
[ -z "$HCI" ] && err "hciattach ran but no HCI device appeared.
       Check that:
         1. The ESP32 firmware was flashed correctly (flash_esp32.sh).
         2. No other process is using ${PORT}."

sudo hciconfig "${HCI}" up
ok "${HCI} is up."

# ── Step 3: Install systemd service ──────────────────────────────────────────

step "Step 3/4 — Install systemd service"

sudo tee "${SERVICE_FILE}" >/dev/null <<EOF
[Unit]
Description=Bluetooth HCI adapter via ESP32 on ${PORT}
After=bluetooth.service
Requires=bluetooth.service

[Service]
Type=forking
ExecStartPre=/bin/sleep 2
ExecStart=/usr/bin/hciattach ${PORT} any ${BAUD} noflow
RemainAfterExit=yes
Restart=on-failure
RestartSec=5

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "${SERVICE_NAME}"
ok "Service ${SERVICE_NAME} installed and enabled at boot."

# ── Step 4: Verify ────────────────────────────────────────────────────────────

step "Step 4/4 — Verify"

echo "  Bluetooth adapters:"
hciconfig -a | sed 's/^/    /'

echo ""
echo "─────────────────────────────────────────────────────────────────────────"
echo ""
echo "  ${HCI} is live. ESP32 will register as hci0 automatically at boot."
echo ""
echo "  To switch to a real USB BT dongle later:"
echo "    sudo systemctl disable --now ${SERVICE_NAME}"
echo "    (plug in dongle — no other changes needed)"
echo ""
echo "  Next — pair the Xbox controller:"
echo ""
echo "    bash tools/gamepad/setup_gamepad.sh"
echo ""
