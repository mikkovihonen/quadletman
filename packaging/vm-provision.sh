#!/usr/bin/env bash
set -euo pipefail

PROJECT=/vagrant/quadletman
SMOKE_USER=smoketest
SMOKE_PASS=smoketest
PACKAGE_TYPE=${1:-RPM}

separator() {
  echo ""
  echo "==> $*"
}

separator "Provisioning smoke-test VM for package type: ${PACKAGE_TYPE}"

if [[ "${PACKAGE_TYPE}" == "RPM" ]]; then
  separator "Installing build dependencies"
  dnf install -y rpm-build python3 python3-pip rpmdevtools podman audit keyutils

  separator "Building RPM"
  cd "${PROJECT}"
  bash packaging/build-rpm.sh

  RPM=$(ls ~/rpmbuild/RPMS/*/quadletman-*.rpm 2>/dev/null | head -1)
  if [[ -z "${RPM}" ]]; then
    echo "ERROR: no RPM found after build" >&2
    exit 1
  fi

  separator "Installing ${RPM}"
  dnf install -y "${RPM}"
elif [[ "${PACKAGE_TYPE}" == "DEB" ]]; then
  separator "Updating apt cache"
  apt-get update -y

  separator "Installing build dependencies"
  apt-get install -y python3 python3-venv python3-pip devscripts build-essential libpam0g-dev

  separator "Building DEB"
  cd "${PROJECT}"
  bash packaging/build-deb.sh

  DEB=$(ls quadletman_*.deb 2>/dev/null | head -1)
  if [[ -z "${DEB}" ]]; then
    echo "ERROR: no DEB found after build" >&2
    exit 1
  fi

  separator "Installing ${DEB}"
  apt-get install -y "./${DEB}"
else
  echo "ERROR: unsupported package type: ${PACKAGE_TYPE}" >&2
  exit 1
fi

separator "Creating smoke-test system user (PAM auth)"
if ! id "${SMOKE_USER}" &>/dev/null; then
  useradd -m "${SMOKE_USER}"
fi
echo "${SMOKE_USER}:${SMOKE_PASS}" | chpasswd

if [[ "${PACKAGE_TYPE}" == "RPM" ]]; then
  usermod -aG wheel "${SMOKE_USER}"
else
  usermod -aG sudo "${SMOKE_USER}"
fi

separator "Enabling and starting quadletman"
systemctl enable --now quadletman

separator "Waiting for quadletman service to become active"
for i in $(seq 1 15); do
  if systemctl is-active --quiet quadletman; then
    break
  fi
  sleep 1
 done

if ! systemctl is-active --quiet quadletman; then
  echo "ERROR: quadletman failed to start" >&2
  journalctl -u quadletman -n 80 --no-pager
  exit 1
fi

separator "Waiting for port 8080"
for i in $(seq 1 30); do
  if ss -tlnp | grep -q ':8080'; then
    break
  fi
  sleep 1
 done

if ! ss -tlnp | grep -q ':8080'; then
  echo "ERROR: port 8080 never opened" >&2
  journalctl -u quadletman -n 80 --no-pager
  exit 1
fi

separator "VM provisioning complete"
