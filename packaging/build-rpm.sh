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

# VERSION can be pre-set by the caller (e.g. CI passes the tag without the leading 'v').
# Fallback: derive from the nearest git tag so local builds work without extra steps.
VERSION=${VERSION:-$(git -C "$PROJECT_DIR" describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || echo "0.0.0.dev")}

TARBALL="quadletman-${VERSION}.tar.gz"
SPEC="${SCRIPT_DIR}/rpm/quadletman.spec"

# RPM Version field forbids '-'; split "X.Y.Z-pre" into Version=X.Y.Z Release=0.pre.1
# For plain releases like "0.1.0" the pre-release part is empty and Release stays "1".
RPM_VERSION="${VERSION%%-*}"          # "0.0.1-alpha" -> "0.0.1"   "0.1.0" -> "0.1.0"
RPM_PRERELEASE="${VERSION#${RPM_VERSION}}"  # "-alpha"              ""
RPM_PRERELEASE="${RPM_PRERELEASE#-}"       # "alpha"               ""
if [[ -n "${RPM_PRERELEASE}" ]]; then
    RPM_RELEASE="0.${RPM_PRERELEASE}.1"    # pre-release sorts before 1 (RPM convention)
else
    RPM_RELEASE="1"
fi

echo "==> Building RPM for quadletman ${VERSION} (Version: ${RPM_VERSION}, Release: ${RPM_RELEASE})"

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

RPM_DEFINES=(
    --define "pkg_version ${RPM_VERSION}"
    --define "pkg_release ${RPM_RELEASE}"
    --define "pkg_full_version ${VERSION}"
)

if $USE_MOCK; then
    echo "==> Building SRPM first..."
    rpmbuild -bs "${RPM_DEFINES[@]}" ~/rpmbuild/SPECS/quadletman.spec
    SRPM=$(ls ~/rpmbuild/SRPMS/quadletman-${RPM_VERSION}-*.src.rpm 2>/dev/null | head -1)
    if [[ -z "${SRPM}" ]]; then
        echo "ERROR: SRPM not found after rpmbuild -bs" >&2
        exit 1
    fi
    echo "==> Building RPM in mock chroot..."
    mock --rebuild "${SRPM}"
    echo ""
    echo "==> RPM built in /var/lib/mock/*/result/"
else
    echo "==> Building RPM with rpmbuild..."
    rpmbuild -ba "${RPM_DEFINES[@]}" ~/rpmbuild/SPECS/quadletman.spec

    echo ""
    echo "==> Build complete!"
    echo "    RPM: $(ls ~/rpmbuild/RPMS/*/quadletman-${RPM_VERSION}-*.rpm 2>/dev/null || echo '(check ~/rpmbuild/RPMS/)')"
    echo "    SRPM: $(ls ~/rpmbuild/SRPMS/quadletman-${RPM_VERSION}-*.src.rpm 2>/dev/null || echo '(check ~/rpmbuild/SRPMS/)')"
    echo ""
    echo "Install with:"
    echo "  sudo dnf install ~/rpmbuild/RPMS/*/quadletman-${RPM_VERSION}-*.rpm"
fi
