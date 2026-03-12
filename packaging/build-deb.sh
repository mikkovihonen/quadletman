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

# Get version from pyproject.toml
VERSION=$(python3 -c "
import tomllib
with open('${PROJECT_DIR}/pyproject.toml', 'rb') as f:
    d = tomllib.load(f)
print(d['project']['version'])
")

echo "==> Building .deb for quadletman ${VERSION}"

# Install build dependencies if missing
MISSING_PKGS=()
for pkg in debhelper dh-python python3 python3-venv python3-pip devscripts; do
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

# Copy debian packaging files
cp -r "${SCRIPT_DIR}/debian/" "${BUILD_DIR}/quadletman-${VERSION}/debian/"

# Create orig tarball (required by dpkg-buildpackage)
cd "${BUILD_DIR}"
tar -czf "quadletman_${VERSION}.orig.tar.gz" "quadletman-${VERSION}/"

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
