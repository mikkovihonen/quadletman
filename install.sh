#!/usr/bin/env bash
# quadletman installer
# Run as root: sudo bash install.sh

set -euo pipefail

INSTALL_DIR="/usr/local/lib/quadletman"
BIN="/usr/local/bin/quadletman"
DATA_DIR="/var/lib/quadletman"
SERVICE_FILE="/etc/systemd/system/quadletman.service"

# Require root
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: This installer must be run as root." >&2
  exit 1
fi

echo "==> Installing quadletman"

# Check dependencies
for cmd in python3 pip3 podman systemctl loginctl; do
  if ! command -v "$cmd" &>/dev/null; then
    echo "WARNING: '$cmd' not found — please install it before using quadletman"
  fi
done

# Check python version
PYVER=$(python3 -c 'import sys; print(sys.version_info >= (3,12))' 2>/dev/null || echo False)
if [[ "$PYVER" != "True" ]]; then
  echo "ERROR: Python 3.12+ is required" >&2
  exit 1
fi

# Install Python package
echo "==> Installing Python dependencies"
pip3 install --quiet "$(dirname "$0")"

# Create data directory
echo "==> Creating data directory $DATA_DIR"
install -d -m 0755 "$DATA_DIR"
install -d -m 0755 "$DATA_DIR/volumes"

# Install systemd service
echo "==> Installing systemd service"
cp "$(dirname "$0")/quadletman.service" "$SERVICE_FILE"
systemctl daemon-reload
systemctl enable --now quadletman.service

echo ""
echo "======================================"
echo " quadletman installed successfully!"
echo "======================================"
echo ""
echo " Service status: systemctl status quadletman"
echo " Web UI:         http://$(hostname -I | awk '{print $1}'):8080"
echo ""
echo " Authentication: use your Linux username and password."
echo " Only users in the 'sudo' or 'wheel' group can log in."
echo ""
echo " Configuration:  edit /etc/systemd/system/quadletman.service"
echo "   QUADLETMAN_PORT=8080     (listening port)"
echo "   QUADLETMAN_HOST=0.0.0.0  (listening address)"
echo ""
