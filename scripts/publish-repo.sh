#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# publish-repo.sh — Build RPM + DEB package repository metadata for gh-pages
#
# Usage (CI):
#   GPG_PRIVATE_KEY=... bash scripts/publish-repo.sh /path/to/artifacts
#
# Usage (local testing, unsigned):
#   bash scripts/publish-repo.sh /path/to/artifacts --unsigned
#
# The artifacts directory must contain:
#   *.rpm           — RPM packages
#   *.deb           — DEB packages
#   gpg-key.asc     — public signing key (from packaging/repo/)
#
# Output: _site/ directory ready for deployment to gh-pages.
#
# Repository layout:
#   _site/
#   ├── gpg-key.asc                  # Public key for user import
#   ├── index.html                   # Repo landing page with install instructions
#   ├── rpm/
#   │   └── quadletman-*.rpm         # RPM + createrepo metadata
#   │       repodata/
#   │         repomd.xml
#   │         repomd.xml.asc         # GPG detached signature
#   └── deb/
#       ├── pool/
#       │   └── quadletman_*.deb
#       ├── dists/
#       │   └── stable/
#       │       ├── Release           # Signed inline (clearsign)
#       │       ├── Release.gpg       # Detached signature
#       │       ├── InRelease         # Inline-signed Release
#       │       └── main/
#       │           └── binary-amd64/
#       │               ├── Packages
#       │               └── Packages.gz
#       └── (future: binary-arm64/ etc.)
# ------------------------------------------------------------------------------
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

ARTIFACTS="${1:?Usage: $0 <artifacts-dir> [--unsigned]}"
UNSIGNED=false
if [[ "${2:-}" == "--unsigned" ]]; then
    UNSIGNED=true
fi

SITE="_site"
REPO_URL_BASE="${REPO_URL_BASE:-https://mikkovihonen.github.io/quadletman/packages/unstable}"

# ---------------------------------------------------------------------------
# Validate inputs
# ---------------------------------------------------------------------------

RPM_COUNT=$(find "$ARTIFACTS" -maxdepth 1 -name '*.rpm' | wc -l)
DEB_COUNT=$(find "$ARTIFACTS" -maxdepth 1 -name '*.deb' | wc -l)

if [[ "$RPM_COUNT" -eq 0 && "$DEB_COUNT" -eq 0 ]]; then
    echo "ERROR: No .rpm or .deb files found in $ARTIFACTS" >&2
    exit 1
fi

echo "==> Found ${RPM_COUNT} RPM(s), ${DEB_COUNT} DEB(s) in $ARTIFACTS"

# ---------------------------------------------------------------------------
# Import GPG key (CI mode)
# ---------------------------------------------------------------------------

GPG_FPR=""
if [[ "$UNSIGNED" == false ]]; then
    if [[ -n "${GPG_PRIVATE_KEY:-}" ]]; then
        echo "==> Importing GPG key from environment..."
        echo "$GPG_PRIVATE_KEY" | base64 -d | gpg --batch --import
    fi
    # Find the signing key
    GPG_FPR=$(gpg --list-secret-keys --with-colons 2>/dev/null \
        | awk -F: '/^fpr:/{print $10; exit}')
    if [[ -z "$GPG_FPR" ]]; then
        echo "ERROR: No GPG signing key found. Set GPG_PRIVATE_KEY or use --unsigned." >&2
        exit 1
    fi
    echo "${GPG_FPR}:6:" | gpg --import-ownertrust 2>/dev/null
    echo "==> Signing with key: ${GPG_FPR}"
fi

# ---------------------------------------------------------------------------
# Prepare output
# ---------------------------------------------------------------------------

rm -rf "$SITE"
mkdir -p "$SITE"

# Copy public key
if [[ -f "${REPO_ROOT}/packaging/repo/gpg-key.asc" ]]; then
    cp "${REPO_ROOT}/packaging/repo/gpg-key.asc" "$SITE/"
elif [[ -n "$GPG_FPR" ]]; then
    gpg --armor --export "$GPG_FPR" > "$SITE/gpg-key.asc"
fi

# ---------------------------------------------------------------------------
# RPM repository
# ---------------------------------------------------------------------------

if [[ "$RPM_COUNT" -gt 0 ]]; then
    echo "==> Building RPM repository..."
    mkdir -p "$SITE/rpm"
    cp "$ARTIFACTS"/*.rpm "$SITE/rpm/"

    createrepo_c "$SITE/rpm/"

    if [[ "$UNSIGNED" == false ]]; then
        gpg --batch --yes --detach-sign --armor \
            --default-key "$GPG_FPR" \
            "$SITE/rpm/repodata/repomd.xml"
        echo "    Signed repomd.xml"
    fi
fi

# ---------------------------------------------------------------------------
# DEB repository
# ---------------------------------------------------------------------------

if [[ "$DEB_COUNT" -gt 0 ]]; then
    echo "==> Building DEB repository..."

    ARCH="amd64"
    DIST="stable"
    COMP="main"

    DEB_ROOT="$(pwd)/$SITE/deb"
    POOL="$DEB_ROOT/pool"
    DIST_DIR="$DEB_ROOT/dists/$DIST"
    BIN_DIR="$DIST_DIR/$COMP/binary-$ARCH"

    mkdir -p "$POOL" "$BIN_DIR"
    cp "$ARTIFACTS"/*.deb "$POOL/"

    # Generate Packages index
    (cd "$DEB_ROOT" && dpkg-scanpackages --arch "$ARCH" pool/ > "$BIN_DIR/Packages")
    gzip -9 -k "$BIN_DIR/Packages"

    # Generate Release file
    cat > "$DIST_DIR/Release" <<EOF
Origin: quadletman
Label: quadletman
Suite: $DIST
Codename: $DIST
Architectures: $ARCH
Components: $COMP
Date: $(date -Ru)
EOF

    # Append checksums
    (cd "$DIST_DIR" && {
        echo "SHA256:"
        for f in "$COMP/binary-$ARCH/Packages" "$COMP/binary-$ARCH/Packages.gz"; do
            size=$(stat -c%s "$f")
            hash=$(sha256sum "$f" | awk '{print $1}')
            printf " %s %8d %s\n" "$hash" "$size" "$f"
        done
    }) >> "$DIST_DIR/Release"

    if [[ "$UNSIGNED" == false ]]; then
        # Detached signature
        gpg --batch --yes --detach-sign --armor \
            --default-key "$GPG_FPR" \
            --output "$DIST_DIR/Release.gpg" \
            "$DIST_DIR/Release"
        # Inline signature (InRelease)
        gpg --batch --yes --clearsign \
            --default-key "$GPG_FPR" \
            --output "$DIST_DIR/InRelease" \
            "$DIST_DIR/Release"
        echo "    Signed Release and InRelease"
    fi
fi

# ---------------------------------------------------------------------------
# Landing page
# ---------------------------------------------------------------------------

cat > "$SITE/index.html" <<'HTMLEOF'
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>quadletman package repository</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
         max-width: 720px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6;
         color: #e0e0e0; background: #1a1a2e; }
  h1 { border-bottom: 2px solid #444; padding-bottom: .5rem; }
  h2 { margin-top: 2rem; }
  code { background: #16213e; padding: .15em .4em; border-radius: 3px; font-size: .9em; }
  pre { background: #16213e; padding: 1rem; border-radius: 6px; overflow-x: auto; }
  pre code { background: none; padding: 0; }
  a { color: #64b5f6; }
</style>
</head>
<body>
<h1>quadletman package repository</h1>
<p>Native RPM and DEB packages for <a href="https://github.com/mikkovihonen/quadletman">quadletman</a>.</p>

<h2>Fedora / RHEL / AlmaLinux / Rocky Linux</h2>
<pre><code># Import the signing key
HTMLEOF

# Inject the actual repo URL into the HTML
cat >> "$SITE/index.html" <<HTMLEOF
sudo rpm --import ${REPO_URL_BASE}/gpg-key.asc

# Add the repository
sudo tee /etc/yum.repos.d/quadletman.repo &lt;&lt;'EOF'
[quadletman]
name=quadletman
baseurl=${REPO_URL_BASE}/rpm/
enabled=1
gpgcheck=1
gpgkey=${REPO_URL_BASE}/gpg-key.asc
EOF

# Install
sudo dnf install quadletman</code></pre>

<h2>Ubuntu / Debian</h2>
<pre><code># Import the signing key
curl -fsSL ${REPO_URL_BASE}/gpg-key.asc \\
  | sudo gpg --dearmor -o /etc/apt/keyrings/quadletman.gpg

# Add the repository
echo "deb [signed-by=/etc/apt/keyrings/quadletman.gpg] ${REPO_URL_BASE}/deb/ stable main" \\
  | sudo tee /etc/apt/sources.list.d/quadletman.list

# Install
sudo apt update
sudo apt install quadletman</code></pre>
HTMLEOF

cat >> "$SITE/index.html" <<'HTMLEOF'

<h2>Verify the signing key</h2>
<pre><code># Download and inspect
curl -fsSL $REPO_URL/gpg-key.asc | gpg --show-keys</code></pre>

<p>The key fingerprint is published in the
<a href="https://github.com/mikkovihonen/quadletman/blob/main/packaging/repo/gpg-fingerprint.txt">source repository</a>.</p>
</body>
</html>
HTMLEOF

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------

echo ""
echo "==> Repository built in ${SITE}/"
ls -lR "$SITE/" 2>/dev/null | head -40
echo ""
echo "Deploy to gh-pages to make it live at ${REPO_URL_BASE}/"
