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

# Include all project files except packaging artifacts and caches
tar -czf "${TARBALL}" \
    --transform "s|^${PROJECT_DIR#/}|quadletman-${VERSION}|" \
    --transform "s|^\.|quadletman-${VERSION}|" \
    -C "$(dirname "$PROJECT_DIR")" \
    --exclude="$(basename "$PROJECT_DIR")/.git" \
    --exclude="$(basename "$PROJECT_DIR")/__pycache__" \
    --exclude="$(basename "$PROJECT_DIR")/*/__pycache__" \
    --exclude="$(basename "$PROJECT_DIR")/*/*/__pycache__" \
    --exclude="$(basename "$PROJECT_DIR")/*.egg-info" \
    --exclude="$(basename "$PROJECT_DIR")/dist" \
    --exclude="$(basename "$PROJECT_DIR")/build" \
    --exclude="$(basename "$PROJECT_DIR")/.venv" \
    --exclude="$(basename "$PROJECT_DIR")/venv" \
    "$(basename "$PROJECT_DIR")"

echo "==> Created: $(pwd)/${TARBALL}"
echo "    Version: ${VERSION}"
