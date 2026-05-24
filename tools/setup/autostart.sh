#!/usr/bin/env bash
# autostart.sh — Enable or disable Black-Mata auto-start services on the Jetson.
#
# Usage (run on the Jetson):
#   bash tools/setup/autostart.sh enable
#   bash tools/setup/autostart.sh disable
#
# 'enable'  installs the three services and starts them now + on every boot.
# 'disable' stops the three services and prevents them from starting on boot.
#           Does NOT uninstall the service files — re-enable any time with 'enable'.

set -e

SERVICES="black-mata-camera black-mata-dashboard black-mata-ngrok"
REPO_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
CURRENT_USER="$(whoami)"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# ── Helpers ────────────────────────────────────────────────────────────────────

usage() {
    echo "Usage: bash tools/setup/autostart.sh enable|disable"
    exit 1
}

install_services() {
    echo "Installing service files..."
    for svc in $SERVICES; do
        sed \
            -e "s|__REPO_DIR__|${REPO_DIR}|g" \
            -e "s|__USER__|${CURRENT_USER}|g" \
            "${SCRIPT_DIR}/${svc}.service" \
            | sudo tee "/etc/systemd/system/${svc}.service" > /dev/null
        echo "  Installed /etc/systemd/system/${svc}.service"
    done
    sudo systemctl daemon-reload
}

# ── Commands ───────────────────────────────────────────────────────────────────

cmd_enable() {
    install_services

    # Check serial port access
    if ! groups | grep -qw dialout; then
        echo ""
        echo "WARNING: User '${CURRENT_USER}' is not in the 'dialout' group."
        echo "  The dashboard server may fail to open the serial port."
        echo "  Fix with: sudo usermod -aG dialout ${CURRENT_USER}  (then log out and back in)"
        echo ""
    fi

    echo "Enabling and starting services..."
    for svc in $SERVICES; do
        sudo systemctl enable "${svc}.service"
        sudo systemctl start  "${svc}.service"
        echo "  ${svc}: $(sudo systemctl is-active ${svc}.service)"
    done

    echo ""
    echo "Auto-start enabled. Services will run on every boot."
    echo "Dashboard: http://localhost:8082"
    echo ""
    echo "To check status:  sudo systemctl status black-mata-camera black-mata-dashboard black-mata-ngrok"
    echo "To view logs:     journalctl -u black-mata-dashboard -f"
}

cmd_disable() {
    echo "Stopping and disabling services..."
    for svc in $SERVICES; do
        sudo systemctl stop    "${svc}.service" 2>/dev/null || true
        sudo systemctl disable "${svc}.service" 2>/dev/null || true
        echo "  ${svc}: stopped and disabled"
    done

    echo ""
    echo "Auto-start disabled. Services will not run on next boot."
    echo "Re-enable any time with:  bash tools/setup/autostart.sh enable"
}

# ── Entry point ────────────────────────────────────────────────────────────────

case "${1:-}" in
    enable)  cmd_enable  ;;
    disable) cmd_disable ;;
    *)       usage       ;;
esac
