#!/usr/bin/env bash
# Build an RPM package for quadletman on Fedora/RHEL/AlmaLinux/Rocky Linux.
#
# Usage:
#   bash packaging/build-rpm.sh          # builds RPM for current OS
#   bash packaging/build-rpm.sh --mock   # builds in mock (clean chroot)
#
# Prerequisites (install once):
#   sudo dnf install -y rpm-build python3 python3-pip rpmdevtools
#   rpmdev-setuptree  # creates ~/rpmbuild directory structure
#
# For --mock builds:
#   sudo dnf install -y mock
#   sudo usermod -aG mock $USER   # log out and back in after this
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
USE_MOCK=false

if [[ "${1:-}" == "--mock" ]]; then
    USE_MOCK=true
fi

# Get version from pyproject.toml
VERSION=$(python3 -c "
import tomllib
with open('${PROJECT_DIR}/pyproject.toml', 'rb') as f:
    d = tomllib.load(f)
print(d['project']['version'])
")

TARBALL="quadletman-${VERSION}.tar.gz"
SPEC="${SCRIPT_DIR}/rpm/quadletman.spec"

echo "==> Building RPM for quadletman ${VERSION}"

# Install build dependencies if missing
if ! rpm -q rpm-build &>/dev/null; then
    echo "==> Installing rpm-build..."
    sudo dnf install -y rpm-build python3 python3-pip rpmdevtools
fi

# Ensure rpmbuild tree exists
rpmdev-setuptree 2>/dev/null || mkdir -p ~/rpmbuild/{BUILD,RPMS,SOURCES,SPECS,SRPMS}

# Create source tarball
echo "==> Creating source tarball..."
cd "$PROJECT_DIR"
bash "${SCRIPT_DIR}/build-source.sh"
cp "${TARBALL}" ~/rpmbuild/SOURCES/

# Copy spec file
cp "${SPEC}" ~/rpmbuild/SPECS/quadletman.spec

if $USE_MOCK; then
    echo "==> Building SRPM first..."
    rpmbuild -bs ~/rpmbuild/SPECS/quadletman.spec
    SRPM=$(ls ~/rpmbuild/SRPMS/quadletman-${VERSION}-*.src.rpm | head -1)
    echo "==> Building RPM in mock chroot..."
    mock --rebuild "${SRPM}"
    echo ""
    echo "==> RPM built in /var/lib/mock/*/result/"
else
    echo "==> Building RPM with rpmbuild..."
    rpmbuild -ba ~/rpmbuild/SPECS/quadletman.spec

    echo ""
    echo "==> Build complete!"
    echo "    RPM: $(ls ~/rpmbuild/RPMS/noarch/quadletman-${VERSION}-*.noarch.rpm 2>/dev/null || echo '(check ~/rpmbuild/RPMS/)')"
    echo "    SRPM: $(ls ~/rpmbuild/SRPMS/quadletman-${VERSION}-*.src.rpm 2>/dev/null || echo '(check ~/rpmbuild/SRPMS/)')"
    echo ""
    echo "Install with:"
    echo "  sudo dnf install ~/rpmbuild/RPMS/noarch/quadletman-${VERSION}-*.noarch.rpm"
fi
