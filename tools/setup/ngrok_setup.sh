#!/usr/bin/env bash
# ngrok_setup.sh — Install ngrok on the Jetson and configure auth token.
#
# Usage:
#   bash tools/setup/ngrok_setup.sh
#
# After setup, expose the dashboard with:
#   ngrok http 8082

set -e

NGROK_BIN="/usr/local/bin/ngrok"
ARCH=$(uname -m)

# ── Map machine architecture to ngrok download target ─────────────────────────

case "$ARCH" in
    aarch64) NGROK_ARCH="arm64" ;;
    armv7l)  NGROK_ARCH="arm"   ;;
    x86_64)  NGROK_ARCH="amd64" ;;
    *)
        echo "ERROR: Unsupported architecture: $ARCH"
        exit 1
        ;;
esac

# ── Install ngrok binary ───────────────────────────────────────────────────────

if command -v ngrok &>/dev/null; then
    echo "ngrok already installed: $(ngrok version)"
else
    echo "Downloading ngrok for $ARCH ($NGROK_ARCH)..."
    TMP=$(mktemp -d)
    curl -sSL "https://bin.equinox.io/c/bNyj1mQVY4c/ngrok-v3-stable-linux-${NGROK_ARCH}.tgz" \
        -o "$TMP/ngrok.tgz"
    tar -xzf "$TMP/ngrok.tgz" -C "$TMP"
    sudo mv "$TMP/ngrok" "$NGROK_BIN"
    sudo chmod +x "$NGROK_BIN"
    rm -rf "$TMP"
    echo "Installed: $(ngrok version)"
fi

# ── Auth token setup ───────────────────────────────────────────────────────────

if ngrok config check &>/dev/null 2>&1 && grep -q "authtoken" ~/.config/ngrok/ngrok.yml 2>/dev/null; then
    echo "Auth token already configured."
else
    echo ""
    echo "You need a free ngrok account to use tunnels."
    echo "  1. Sign up at https://dashboard.ngrok.com/signup"
    echo "  2. Copy your auth token from https://dashboard.ngrok.com/get-started/your-authtoken"
    echo ""
    read -rp "Paste your ngrok auth token: " TOKEN
    if [ -z "$TOKEN" ]; then
        echo "No token entered. Run 'ngrok config add-authtoken <token>' manually later."
    else
        ngrok config add-authtoken "$TOKEN"
        echo "Auth token saved."
    fi
fi

# ── Done ───────────────────────────────────────────────────────────────────────

echo ""
echo "Setup complete. To expose the dashboard for a demo:"
echo "  ngrok http 8082"
echo ""
echo "ngrok will print a temporary public HTTPS URL."
echo "Share it with anyone for the duration of the demo."
echo "The URL changes every time ngrok restarts (free tier)."
echo "Stop the tunnel with Ctrl+C when the demo is done."
