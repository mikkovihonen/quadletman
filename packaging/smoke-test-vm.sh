#!/usr/bin/env bash
# Provisioner script for the Vagrant smoke-test VM.
# Runs inside the Fedora VM as root.
# See docs/testing.md for usage instructions.
set -euo pipefail

PROJECT=/vagrant/quadletman
SMOKE_USER=smoketest
SMOKE_PASS=smoketest

separator() { echo ""; echo "==> $*"; }

separator "SELinux status (must be Enforcing for a valid smoke test)"
getenforce

separator "Installing build dependencies"
dnf install -y rpm-build python3 python3-pip rpmdevtools podman audit

separator "Building RPM"
cd "$PROJECT"
bash packaging/build-rpm.sh

RPM=$(ls ~/rpmbuild/RPMS/*/quadletman-*.rpm 2>/dev/null | head -1)
if [[ -z "$RPM" ]]; then
    echo "ERROR: no RPM found after build" >&2
    exit 1
fi
echo "    Built: $RPM"

separator "Installing $RPM"
dnf install -y "$RPM"

separator "Creating smoke-test system user (wheel group for PAM auth)"
if ! id "$SMOKE_USER" &>/dev/null; then
    useradd -m "$SMOKE_USER"
fi
echo "${SMOKE_USER}:${SMOKE_PASS}" | chpasswd
usermod -aG wheel "$SMOKE_USER"

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

separator "Smoke test: HTTP health check (GET /)"
HTTP=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 10 \
    -u "${SMOKE_USER}:${SMOKE_PASS}" \
    http://localhost:8000/ || true)
echo "    HTTP status: $HTTP"
if [[ "$HTTP" != "200" ]]; then
    echo "ERROR: expected 200, got $HTTP" >&2
    journalctl -u quadletman -n 80 --no-pager
    exit 1
fi

separator "Smoke test: unauthenticated request must be rejected"
HTTP_UNAUTH=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 10 \
    http://localhost:8000/ || true)
echo "    HTTP status (no auth): $HTTP_UNAUTH"
if [[ "$HTTP_UNAUTH" != "401" ]]; then
    echo "ERROR: expected 401 for unauthenticated request, got $HTTP_UNAUTH" >&2
    exit 1
fi

separator "SELinux AVC check (unexpected denials for quadletman?)"
# ausearch exits 1 when nothing is found — that is the success case
if ausearch -m avc -ts today -c quadletman 2>/dev/null | grep -q 'type=AVC'; then
    echo "WARNING: SELinux AVC denials found for quadletman:"
    ausearch -m avc -ts today -c quadletman 2>/dev/null || true
else
    echo "    No AVC denials for quadletman."
fi

echo ""
echo "============================================================"
echo " All smoke tests passed."
echo " UI:  http://localhost:8001/"
echo " Auth: ${SMOKE_USER} / ${SMOKE_PASS}"
echo "============================================================"
