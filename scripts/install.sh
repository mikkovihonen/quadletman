#!/usr/bin/env bash
# quadletman installer
# Run as root: sudo bash scripts/install.sh

set -euo pipefail

DATA_DIR="/var/lib/quadletman"
SERVICE_FILE="/etc/systemd/system/quadletman.service"
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# Require root
if [[ $EUID -ne 0 ]]; then
  echo "ERROR: This installer must be run as root." >&2
  exit 1
fi

echo "==> Installing quadletman"

# ---------------------------------------------------------------------------
# Detect distro family
# ---------------------------------------------------------------------------
install_packages() {
  if [ -f /etc/debian_version ]; then
    DISTRO="debian"
  elif [ -f /etc/redhat-release ]; then
    DISTRO="redhat"
  else
    DISTRO="unknown"
  fi

  case "$DISTRO" in
    debian)
      echo "==> Detected Debian/Ubuntu — installing system dependencies"
      apt-get update -qq
      apt-get install -y -qq \
        python3 python3-pip python3-venv \
        podman \
        libpam0g libpam0g-dev \
        systemd \
        sudo \
        procps
      # Recommended (non-fatal if unavailable)
      apt-get install -y -qq policycoreutils selinux-utils \
        systemd-container 2>/dev/null || true
      ;;
    redhat)
      echo "==> Detected Fedora/RHEL — installing system dependencies"
      dnf install -y \
        python3 python3-pip \
        podman \
        pam pam-devel \
        systemd \
        sudo \
        procps-ng
      # Recommended (non-fatal if unavailable)
      dnf install -y policycoreutils policycoreutils-python-utils 2>/dev/null || true
      ;;
    *)
      echo "WARNING: Unknown distro — cannot install system packages automatically."
      echo "Please ensure the following are installed:"
      echo "  python3 (>= 3.12), pip3, podman, PAM libraries + dev headers,"
      echo "  systemd, sudo, procps"
      echo ""
      # Verify critical commands exist
      local missing=0
      for cmd in python3 pip3 podman systemctl loginctl sudo; do
        if ! command -v "$cmd" &>/dev/null; then
          echo "ERROR: '$cmd' not found" >&2
          missing=1
        fi
      done
      if [[ $missing -eq 1 ]]; then
        echo "ERROR: Missing required dependencies. Install them and re-run." >&2
        exit 1
      fi
      ;;
  esac
}

install_packages

# ---------------------------------------------------------------------------
# Check Python version
# ---------------------------------------------------------------------------
PYVER=$(python3 -c 'import sys; print(sys.version_info >= (3,12))' 2>/dev/null || echo False)
if [[ "$PYVER" != "True" ]]; then
  echo "ERROR: Python 3.12+ is required (found $(python3 --version 2>&1))" >&2
  exit 1
fi

# ---------------------------------------------------------------------------
# Install Python package
# ---------------------------------------------------------------------------
echo "==> Installing Python package"
pip3 install --quiet "$PROJECT_DIR"

# ---------------------------------------------------------------------------
# Create data directory
# ---------------------------------------------------------------------------
echo "==> Creating data directory $DATA_DIR"
install -d -m 0755 "$DATA_DIR"
install -d -m 0755 "$DATA_DIR/volumes"

# ---------------------------------------------------------------------------
# Install systemd service
# ---------------------------------------------------------------------------
echo "==> Installing systemd service"
cp "$PROJECT_DIR/quadletman.service" "$SERVICE_FILE"
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
