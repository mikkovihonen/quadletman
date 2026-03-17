#!/usr/bin/env bash
# Create a source tarball from the project directory.
# Run from anywhere: bash packaging/build-source.sh
# Output: quadletman-<version>.tar.gz in the current directory
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
# VERSION can be pre-set by the caller (e.g. CI passes the tag without the leading 'v').
# Fallback: derive from the nearest git tag so local builds work without extra steps.
VERSION=${VERSION:-$(git -C "$PROJECT_DIR" describe --tags --abbrev=0 2>/dev/null | sed 's/^v//' || echo "0.0.0.dev")}
TARBALL="quadletman-${VERSION}.tar.gz"

echo "==> Creating source tarball ${TARBALL}"

# Stage into a temp directory named quadletman-<version> so the tarball's
# top-level directory matches what rpmbuild's %setup expects.
STAGE_DIR=$(mktemp -d)
trap "rm -rf ${STAGE_DIR}" EXIT

rsync -a \
    --exclude='.git' \
    --exclude='__pycache__' \
    --exclude='*.egg-info' \
    --exclude='.venv' \
    --exclude='venv' \
    --exclude='dist' \
    --exclude='build' \
    "${PROJECT_DIR}/" "${STAGE_DIR}/quadletman-${VERSION}/"

tar -czf "${TARBALL}" -C "${STAGE_DIR}" "quadletman-${VERSION}"

trap - EXIT
rm -rf "${STAGE_DIR}"

echo "==> Created: $(pwd)/${TARBALL}"
echo "    Version: ${VERSION}"
