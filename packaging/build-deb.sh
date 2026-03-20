#!/usr/bin/env bash
# Build a .deb package for quadletman on Ubuntu/Debian.
#
# Usage:
#   bash packaging/build-deb.sh
#
# Prerequisites (install once):
#   sudo apt-get install -y debhelper dh-python python3 python3-venv \
#                           python3-pip devscripts build-essential
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# VERSION can be pre-set by the caller (e.g. CI passes the tag without the leading 'v').
# Fallback: derive from the nearest git tag so local builds work without extra steps.
VERSION=${VERSION:-$(git -C "$PROJECT_DIR" describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || echo "0.0.0.dev")}

echo "==> Building .deb for quadletman ${VERSION}"

# Install build dependencies if missing
MISSING_PKGS=()
for pkg in debhelper dh-python python3 python3-venv python3-pip devscripts libpam0g-dev; do
    if ! dpkg -l "$pkg" &>/dev/null; then
        MISSING_PKGS+=("$pkg")
    fi
done
if [[ ${#MISSING_PKGS[@]} -gt 0 ]]; then
    echo "==> Installing build dependencies: ${MISSING_PKGS[*]}"
    sudo apt-get install -y "${MISSING_PKGS[@]}"
fi

# dpkg-buildpackage expects to run from the source root with a debian/ dir
# We use a temporary source tree to avoid polluting the working directory.
BUILD_DIR=$(mktemp -d)
trap "rm -rf ${BUILD_DIR}" EXIT

echo "==> Preparing source tree in ${BUILD_DIR}..."

# Copy project (excluding .git and caches)
rsync -a --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.egg-info' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='dist' \
    --exclude='build' \
    "${PROJECT_DIR}/" "${BUILD_DIR}/quadletman-${VERSION}/"

# Copy debian packaging files and stamp the correct version into changelog.
# dpkg-buildpackage reads the version from the first line of debian/changelog.
cp -r "${SCRIPT_DIR}/debian/" "${BUILD_DIR}/quadletman-${VERSION}/debian/"
CHANGELOG_DATE=$(date -R)
cat > "${BUILD_DIR}/quadletman-${VERSION}/debian/changelog" << EOF
quadletman (${VERSION}-1) unstable; urgency=low

  * Release ${VERSION}.

 -- quadletman packager <packager@example.com>  ${CHANGELOG_DATE}
EOF

# Create orig tarball (required by dpkg-buildpackage)
cd "${BUILD_DIR}"
tar -czf "quadletman_${VERSION}.orig.tar.gz" "quadletman-${VERSION}/"

# hatch-vcs reads the version from git, but the build tree has no .git directory.
# Export the pretend version so hatchling uses it directly instead of querying git.
# Both var names are set: the package-specific one (preferred) and the generic fallback.
export SETUPTOOLS_SCM_PRETEND_VERSION_FOR_QUADLETMAN="${VERSION}"
export SETUPTOOLS_SCM_PRETEND_VERSION="${VERSION}"

# Build the package
cd "${BUILD_DIR}/quadletman-${VERSION}"
dpkg-buildpackage -us -uc -b

# Copy results back
DEB=$(ls "${BUILD_DIR}"/quadletman_${VERSION}-*.deb 2>/dev/null | head -1)
if [[ -n "${DEB}" ]]; then
    cp "${DEB}" "${PROJECT_DIR}/"
    DEST_DEB="${PROJECT_DIR}/$(basename "${DEB}")"
    echo ""
    echo "==> Build complete!"
    echo "    DEB: ${DEST_DEB}"
    echo ""
    echo "Install with:"
    echo "  sudo apt install ${DEST_DEB}"
    echo "  # or: sudo dpkg -i ${DEST_DEB} && sudo apt-get install -f"
else
    echo "ERROR: .deb file not found in ${BUILD_DIR}" >&2
    ls "${BUILD_DIR}"/ >&2
    exit 1
fi
