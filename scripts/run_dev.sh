#!/usr/bin/env bash
# Run quadletman locally for development.
#
# Usage:
#   ./scripts/run_dev.sh              # root mode (default, backward compatible)
#   ./scripts/run_dev.sh --nonroot    # non-root mode as qm-dev user (production-like)
#
# Root mode runs the app as root with dev-isolated data paths.
# Non-root mode creates a qm-dev system user that mirrors the production
# quadletman user, including sudoers, shadow group, and the agent API socket.
# The first --nonroot run performs one-time setup (needs sudo).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
cd "$PROJECT_DIR"

DEV_USER="qm-dev"
DEV_DATA="/tmp/qm-dev-data"
DEV_RUN="/run/qm-dev"
SUDOERS_SRC="$SCRIPT_DIR/sudoers.d/qm-dev"
SUDOERS_DST="/etc/sudoers.d/qm-dev"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_info()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
_ok()    { printf '\033[1;32m  ✓\033[0m %s\n' "$*"; }
_warn()  { printf '\033[1;33m  !\033[0m %s\n' "$*"; }

_check_service_conflict() {
    if systemctl is-active --quiet quadletman.service 2>/dev/null; then
        echo ""
        _warn "quadletman.service is running — the production instance will"
        _warn "conflict with the dev server (same port, shared qm-* users)."
        echo ""
        read -rp "    Stop the service and continue? [y/N] " answer
        if [[ "${answer:-}" =~ ^[Yy]$ ]]; then
            sudo systemctl stop quadletman.service
            _ok "Stopped quadletman.service"
        else
            echo "Aborted." >&2
            exit 1
        fi
    fi
}

_ensure_venv() {
    _info "Syncing dependencies"
    uv sync --group dev
}

# ---------------------------------------------------------------------------
# Non-root: one-time setup
# ---------------------------------------------------------------------------

_setup_nonroot() {
    _info "Setting up non-root dev environment (qm-dev user)"

    # Create system user
    if ! getent passwd "$DEV_USER" >/dev/null 2>&1; then
        sudo useradd --system \
            --home-dir "$DEV_DATA" \
            --shell /usr/sbin/nologin \
            --comment "quadletman dev user" \
            "$DEV_USER"
        _ok "Created system user $DEV_USER"
    else
        _ok "User $DEV_USER already exists"
    fi

    # Add to supplementary groups
    for grp in shadow systemd-journal; do
        if getent group "$grp" >/dev/null 2>&1; then
            sudo usermod -aG "$grp" "$DEV_USER" 2>/dev/null || true
        fi
    done
    _ok "Group membership: shadow, systemd-journal"

    # Install dev sudoers
    if [ -f "$SUDOERS_SRC" ]; then
        sudo install -m 0440 "$SUDOERS_SRC" "$SUDOERS_DST"
        _ok "Installed sudoers at $SUDOERS_DST"
    else
        echo "ERROR: $SUDOERS_SRC not found" >&2
        exit 1
    fi

    # Create data directories
    sudo install -d -m 0755 -o "$DEV_USER" -g "$DEV_USER" "$DEV_DATA"
    sudo install -d -m 0755 -o "$DEV_USER" -g "$DEV_USER" "$DEV_DATA/volumes"
    _ok "Data directory: $DEV_DATA"

    # Create runtime directory for agent socket
    sudo install -d -m 0755 -o "$DEV_USER" -g "$DEV_USER" "$DEV_RUN"
    _ok "Runtime directory: $DEV_RUN"
}

# ---------------------------------------------------------------------------
# Run modes
# ---------------------------------------------------------------------------

_run_root() {
    _check_service_conflict
    _info "Starting quadletman in ROOT mode"
    _ensure_venv
    sudo env \
        QUADLETMAN_DB_PATH=/tmp/qm-dev.db \
        QUADLETMAN_VOLUMES_BASE=/tmp/qm-volumes \
        .venv/bin/quadletman
}

_run_nonroot() {
    _check_service_conflict
    _ensure_venv
    _setup_nonroot

    # Resolve the venv site-packages so qm-dev can import the project
    VENV_SP=""
    for sp in "$PROJECT_DIR"/.venv/lib/python*/site-packages; do
        [ -d "$sp" ] && VENV_SP="$sp" && break
    done
    if [ -z "$VENV_SP" ]; then
        echo "ERROR: could not find .venv site-packages" >&2
        exit 1
    fi

    _info "Starting quadletman in NON-ROOT mode (user: $DEV_USER)"
    _info "Data: $DEV_DATA | Agent socket: $DEV_RUN/agent.sock"
    echo ""

    sudo -u "$DEV_USER" env \
        QUADLETMAN_DB_PATH="$DEV_DATA/quadletman.db" \
        QUADLETMAN_VOLUMES_BASE="$DEV_DATA/volumes" \
        QUADLETMAN_AGENT_SOCKET="$DEV_RUN/agent.sock" \
        PYTHONPATH="$VENV_SP${PYTHONPATH:+:$PYTHONPATH}" \
        "$PROJECT_DIR/.venv/bin/quadletman"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

case "${1:-}" in
    --nonroot|-n)
        _run_nonroot
        ;;
    --help|-h)
        echo "Usage: $0 [--nonroot]"
        echo ""
        echo "  (default)    Run as root with dev-isolated data (backward compatible)"
        echo "  --nonroot    Run as qm-dev user (production-like privilege model)"
        echo ""
        echo "The first --nonroot run creates the qm-dev system user and installs"
        echo "a dev sudoers file. Subsequent runs skip setup if already configured."
        ;;
    "")
        _run_root
        ;;
    *)
        echo "Unknown option: $1" >&2
        echo "Usage: $0 [--nonroot]" >&2
        exit 1
        ;;
esac
