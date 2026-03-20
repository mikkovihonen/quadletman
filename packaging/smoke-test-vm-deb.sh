#!/usr/bin/env bash
# Provisioner script for the Vagrant Debian/Ubuntu smoke-test VM.
# Runs inside the Ubuntu VM as root.
# See docs/packaging.md for usage instructions.
set -euo pipefail

PROJECT=/vagrant/quadletman
SMOKE_USER=smoketest
SMOKE_PASS=smoketest

separator() { echo ""; echo "==> $*"; }

separator "Installing build and runtime dependencies"
apt-get update -qq
apt-get install -y --no-install-recommends \
    debhelper dh-python python3 python3-venv python3-pip \
    devscripts build-essential rsync libpam0g-dev \
    podman curl

separator "Building DEB"
cd "$PROJECT"
bash packaging/build-deb.sh

DEB=$(ls "$PROJECT"/quadletman_*.deb 2>/dev/null | head -1)
if [[ -z "$DEB" ]]; then
    echo "ERROR: no .deb found after build" >&2
    exit 1
fi
echo "    Built: $DEB"

separator "Installing $DEB"
apt-get install -y "$DEB"

separator "Creating smoke-test system user (sudo group for PAM auth)"
if ! id "$SMOKE_USER" &>/dev/null; then
    useradd -m "$SMOKE_USER"
fi
echo "${SMOKE_USER}:${SMOKE_PASS}" | chpasswd
usermod -aG sudo "$SMOKE_USER"

separator "Enabling and starting quadletman"
systemctl enable --now quadletman

# Give the service a moment to finish startup
for i in $(seq 1 10); do
    systemctl is-active --quiet quadletman && break
    sleep 1
done

if ! systemctl is-active --quiet quadletman; then
    echo "ERROR: quadletman failed to start" >&2
    journalctl -u quadletman -n 80 --no-pager
    exit 1
fi
echo "    Service is active."

# Wait for the app to bind to port 8080 (systemd is-active goes green before uvicorn is ready)
echo "    Waiting for port 8080..."
for i in $(seq 1 30); do
    ss -tlnp | grep -q ':8080' && break
    sleep 1
done
if ! ss -tlnp | grep -q ':8080'; then
    echo "ERROR: port 8080 never opened" >&2
    journalctl -u quadletman -n 80 --no-pager
    exit 1
fi

separator "Smoke test: login and authenticated GET /"
COOKIE_JAR=$(mktemp)
# Step 1: POST credentials to /login and capture the session cookie
LOGIN_STATUS=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 10 \
    -c "$COOKIE_JAR" \
    -d "username=${SMOKE_USER}&password=${SMOKE_PASS}" \
    http://localhost:8080/login || true)
echo "    Login POST status: $LOGIN_STATUS"
if [[ "$LOGIN_STATUS" != "200" && "$LOGIN_STATUS" != "303" && "$LOGIN_STATUS" != "302" ]]; then
    echo "ERROR: login POST failed with status $LOGIN_STATUS" >&2
    journalctl -u quadletman -n 80 --no-pager
    exit 1
fi

# Step 2: GET / with the session cookie
HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 10 \
    -b "$COOKIE_JAR" \
    -L \
    http://localhost:8080/ || true)
echo "    HTTP status: $HTTP"
rm -f "$COOKIE_JAR"
if [[ "$HTTP" != "200" ]]; then
    echo "ERROR: expected 200, got $HTTP" >&2
    journalctl -u quadletman -n 80 --no-pager
    exit 1
fi

separator "Smoke test: unauthenticated request must redirect to login"
HTTP_UNAUTH=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 10 \
    http://localhost:8080/ || true)
echo "    HTTP status (no auth): $HTTP_UNAUTH"
if [[ "$HTTP_UNAUTH" != "303" && "$HTTP_UNAUTH" != "302" ]]; then
    echo "ERROR: expected redirect for unauthenticated request, got $HTTP_UNAUTH" >&2
    exit 1
fi

echo ""
echo "============================================================"
echo " All smoke tests passed."
echo " UI:  http://localhost:8082/"
echo " Auth: ${SMOKE_USER} / ${SMOKE_PASS}"
echo "============================================================"
