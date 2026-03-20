#!/usr/bin/env bash
# ------------------------------------------------------------------------------
# repo-gpg-key.sh — GPG key lifecycle for the quadletman package repository
#
# Commands:
#   generate     Create a new signing key pair (interactive, for maintainer use)
#   export       Export public key + fingerprint to packaging/repo/
#   rotate       Generate a successor key, cross-sign it with the old key
#   ci-import    Import private key from $GPG_PRIVATE_KEY env var (for CI)
#   ci-export    Print base64-encoded private key for storing in GitHub secrets
#   info         Show current key details
#
# Key identity (Name-Real / Name-Email) can be overridden:
#   GPG_KEY_NAME="quadletman repo"  GPG_KEY_EMAIL="repo@example.com"
#
# Typical workflow:
#   1. maintainer runs:  ./scripts/repo-gpg-key.sh generate
#   2. maintainer runs:  ./scripts/repo-gpg-key.sh export
#   3. maintainer runs:  ./scripts/repo-gpg-key.sh ci-export | gh secret set GPG_PRIVATE_KEY
#   4. CI runs:          ./scripts/repo-gpg-key.sh ci-import
#   5. (years later)     ./scripts/repo-gpg-key.sh rotate
# ------------------------------------------------------------------------------
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
EXPORT_DIR="${REPO_ROOT}/packaging/repo"

KEY_NAME="${GPG_KEY_NAME:-quadletman repo}"
KEY_EMAIL="${GPG_KEY_EMAIL:-noreply@quadletman.dev}"
KEY_EXPIRE="${GPG_KEY_EXPIRE:-3y}"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_find_key() {
    # Return the fingerprint of the most recent (newest) matching secret key.
    # Prints nothing and returns 1 if no key is found.
    gpg --list-secret-keys --with-colons "${KEY_EMAIL}" 2>/dev/null \
        | awk -F: '/^fpr:/{print $10}' \
        | tail -1
}

_require_key() {
    local fpr
    fpr="$(_find_key)"
    if [[ -z "${fpr}" ]]; then
        echo "ERROR: No signing key found for <${KEY_EMAIL}>." >&2
        echo "       Run:  $0 generate" >&2
        exit 1
    fi
    echo "${fpr}"
}

# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

cmd_generate() {
    if _find_key &>/dev/null && [[ -n "$(_find_key)" ]]; then
        echo "A key for <${KEY_EMAIL}> already exists:"
        gpg --list-secret-keys "${KEY_EMAIL}"
        echo ""
        echo "To create a new key (rotation), use:  $0 rotate"
        exit 1
    fi

    echo "Generating Ed25519 signing key for: ${KEY_NAME} <${KEY_EMAIL}>"
    echo "Expiry: ${KEY_EXPIRE}"
    echo ""

    gpg --batch --gen-key <<EOF
Key-Type: eddsa
Key-Curve: ed25519
Name-Real: ${KEY_NAME}
Name-Email: ${KEY_EMAIL}
Expire-Date: ${KEY_EXPIRE}
%commit
EOF

    local fpr
    fpr="$(_find_key)"
    echo ""
    echo "Key generated:  ${fpr}"
    echo ""
    echo "Next steps:"
    echo "  1. $0 export            — write public key to ${EXPORT_DIR}/"
    echo "  2. $0 ci-export         — copy private key to GitHub secrets"
    echo "  3. git add packaging/repo/ && git commit"
}

cmd_export() {
    local fpr
    fpr="$(_require_key)"

    mkdir -p "${EXPORT_DIR}"

    # Armored public key — users import this
    gpg --armor --export "${fpr}" > "${EXPORT_DIR}/gpg-key.asc"

    # Fingerprint file — for verification docs and CI pinning
    echo "${fpr}" > "${EXPORT_DIR}/gpg-fingerprint.txt"

    # Revocation certificate — commit alongside the public key so it's
    # available if the private key is compromised. The revocation cert is
    # NOT secret; it's inert until applied to a keyring.
    gpg --gen-revoke --output "${EXPORT_DIR}/gpg-revocation.asc" \
        --batch --yes "${fpr}" 2>/dev/null || true

    echo "Exported to ${EXPORT_DIR}/:"
    ls -l "${EXPORT_DIR}"/gpg-*
    echo ""
    echo "Commit these files so users and CI can verify signatures."
}

cmd_rotate() {
    local old_fpr
    old_fpr="$(_require_key)"

    echo "=== Key rotation ==="
    echo ""
    echo "Current key: ${old_fpr}"
    echo ""
    echo "This will:"
    echo "  1. Generate a new Ed25519 key with the same identity"
    echo "  2. Cross-sign the new key with the old key (trust chain)"
    echo "  3. Export the new public key + a transition notice"
    echo ""
    read -rp "Continue? [y/N] " confirm
    [[ "${confirm}" =~ ^[Yy]$ ]] || exit 0

    # Rename old key's UID to mark it superseded (adds a new UID, keeps the old)
    echo ""
    echo "--- Generating successor key ---"

    # Generate successor with a temporary email to avoid collision, then fix it
    local tmp_email="rotate-${RANDOM}@quadletman.dev"
    gpg --batch --gen-key <<EOF
Key-Type: eddsa
Key-Curve: ed25519
Name-Real: ${KEY_NAME}
Name-Email: ${tmp_email}
Expire-Date: ${KEY_EXPIRE}
%commit
EOF

    local new_fpr
    new_fpr="$(gpg --list-secret-keys --with-colons "${tmp_email}" \
        | awk -F: '/^fpr:/{print $10}' | tail -1)"

    # Replace temporary UID with the real identity
    gpg --batch --quick-add-uid "${new_fpr}" "${KEY_NAME} <${KEY_EMAIL}>"
    gpg --batch --quick-revuid "${new_fpr}" "${KEY_NAME} <${tmp_email}>"

    # Cross-sign: old key signs new key (tells users "I endorse my successor")
    echo ""
    echo "--- Cross-signing new key with old key ---"
    gpg --batch --yes --default-key "${old_fpr}" --sign-key "${new_fpr}"

    # Export transition bundle
    mkdir -p "${EXPORT_DIR}"

    # Archive the old public key
    gpg --armor --export "${old_fpr}" > "${EXPORT_DIR}/gpg-key-old.asc"

    # Export new public key as the active key
    gpg --armor --export "${new_fpr}" > "${EXPORT_DIR}/gpg-key.asc"
    echo "${new_fpr}" > "${EXPORT_DIR}/gpg-fingerprint.txt"

    # Fresh revocation cert for the new key
    gpg --gen-revoke --output "${EXPORT_DIR}/gpg-revocation.asc" \
        --batch --yes "${new_fpr}" 2>/dev/null || true

    # Write a transition notice with both fingerprints
    cat > "${EXPORT_DIR}/KEY-TRANSITION.md" <<TRANSITION
# GPG Key Transition

The quadletman package signing key has been rotated.

| | Fingerprint |
|---|---|
| **New (active)** | \`${new_fpr}\` |
| **Old (retired)** | \`${old_fpr}\` |

The new key has been cross-signed by the old key. To verify the
chain of trust:

\`\`\`bash
# Import both keys
curl -fsSL https://YOUR_REPO_URL/gpg-key.asc | gpg --import
curl -fsSL https://YOUR_REPO_URL/gpg-key-old.asc | gpg --import

# Verify the cross-signature
gpg --check-sigs ${new_fpr}
\`\`\`

## Update instructions

**RPM users:**
\`\`\`bash
rpm --import https://YOUR_REPO_URL/gpg-key.asc
\`\`\`

**DEB users:**
\`\`\`bash
curl -fsSL https://YOUR_REPO_URL/gpg-key.asc \\
  | gpg --dearmor > /etc/apt/keyrings/quadletman.gpg
\`\`\`

Rotation date: $(date -u +%Y-%m-%d)
TRANSITION

    echo ""
    echo "=== Rotation complete ==="
    echo ""
    echo "Old key:  ${old_fpr}  (retained in gpg-key-old.asc)"
    echo "New key:  ${new_fpr}  (active in gpg-key.asc)"
    echo ""
    echo "Next steps:"
    echo "  1. $0 ci-export        — update GitHub secret with new private key"
    echo "  2. git add packaging/repo/ && git commit"
    echo "  3. Re-sign and republish the package repository"
    echo "  4. Update repo URL in KEY-TRANSITION.md, then publish it"
    echo ""
    echo "Keep the old private key until all users have migrated (typically"
    echo "one release cycle). Then optionally remove it with:"
    echo "  gpg --delete-secret-and-public-key ${old_fpr}"
}

cmd_ci_import() {
    if [[ -z "${GPG_PRIVATE_KEY:-}" ]]; then
        echo "ERROR: GPG_PRIVATE_KEY environment variable is not set." >&2
        echo "       Set it from GitHub Actions secrets." >&2
        exit 1
    fi

    echo "${GPG_PRIVATE_KEY}" | base64 -d | gpg --batch --import

    local fpr
    fpr="$(_find_key)"
    echo "Imported key: ${fpr}"

    # Trust the key so gpg doesn't prompt during signing
    echo "${fpr}:6:" | gpg --import-ownertrust
}

cmd_ci_export() {
    local fpr
    fpr="$(_require_key)"

    echo "Exporting private key ${fpr} as base64."
    echo "Pipe this into:  gh secret set GPG_PRIVATE_KEY"
    echo ""
    gpg --armor --export-secret-keys "${fpr}" | base64 -w 0
    echo ""
}

cmd_info() {
    local fpr
    fpr="$(_find_key)"
    if [[ -z "${fpr}" ]]; then
        echo "No signing key found for <${KEY_EMAIL}>."
        exit 0
    fi

    echo "=== Signing key ==="
    gpg --list-secret-keys --keyid-format long "${fpr}"

    echo ""
    echo "=== Expiry check ==="
    local expires
    expires="$(gpg --list-keys --with-colons "${fpr}" \
        | awk -F: '/^pub:/{print $7}')"
    if [[ -z "${expires}" || "${expires}" == "0" ]]; then
        echo "Key does not expire."
    else
        local expires_date
        expires_date="$(date -d "@${expires}" +%Y-%m-%d 2>/dev/null || echo "${expires}")"
        local now
        now="$(date +%s)"
        local days_left=$(( (expires - now) / 86400 ))

        echo "Expires:    ${expires_date}"
        echo "Days left:  ${days_left}"
        if (( days_left < 90 )); then
            echo ""
            echo "WARNING: Key expires in less than 90 days."
            echo "         Run:  $0 rotate"
        fi
    fi

    if [[ -f "${EXPORT_DIR}/gpg-fingerprint.txt" ]]; then
        local exported_fpr
        exported_fpr="$(cat "${EXPORT_DIR}/gpg-fingerprint.txt")"
        echo ""
        if [[ "${exported_fpr}" == "${fpr}" ]]; then
            echo "Published key matches local key."
        else
            echo "WARNING: Published fingerprint (${exported_fpr}) differs from local key (${fpr})."
            echo "         Run:  $0 export"
        fi
    fi
}

# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

case "${1:-}" in
    generate)   cmd_generate ;;
    export)     cmd_export ;;
    rotate)     cmd_rotate ;;
    ci-import)  cmd_ci_import ;;
    ci-export)  cmd_ci_export ;;
    info)       cmd_info ;;
    *)
        echo "Usage: $0 {generate|export|rotate|ci-import|ci-export|info}"
        echo ""
        echo "Commands:"
        echo "  generate   Create a new Ed25519 signing key pair"
        echo "  export     Write public key + revocation cert to packaging/repo/"
        echo "  rotate     Generate successor key, cross-sign with old key"
        echo "  ci-import  Import private key from \$GPG_PRIVATE_KEY (base64, for CI)"
        echo "  ci-export  Print base64 private key for GitHub secrets"
        echo "  info       Show key details and expiry status"
        exit 1
        ;;
esac
