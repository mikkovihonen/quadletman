#!/usr/bin/env bash
# Run quadletman locally for development as the qm-dev system user.
#
# Usage:
#   ./scripts/run_dev.sh            # run as qm-dev user
#   ./scripts/run_dev.sh --debug    # run with DEBUG log level
#
# The first run performs one-time setup (needs sudo): creates the qm-dev
# system user, installs sudoers, and sets up data directories.

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

_stop_existing_dev() {
    # Kill any running dev instance (quadletman started by qm-dev user)
    local pids
    pids=$(pgrep -u "$DEV_USER" -f 'from quadletman\.main import run' 2>/dev/null || true)
    if [ -n "$pids" ]; then
        _info "Stopping existing dev instance"
        sudo kill $pids 2>/dev/null || true
        # Wait briefly for graceful shutdown
        local waited=0
        while [ $waited -lt 5 ] && pgrep -u "$DEV_USER" -f 'from quadletman\.main import run' >/dev/null 2>&1; do
            sleep 1
            waited=$((waited + 1))
        done
        # Force-kill if still running
        pids=$(pgrep -u "$DEV_USER" -f 'from quadletman\.main import run' 2>/dev/null || true)
        if [ -n "$pids" ]; then
            sudo kill -9 $pids 2>/dev/null || true
        fi
        _ok "Stopped previous dev instance"
    fi
}

_ensure_venv() {
    _info "Syncing dependencies"
    uv sync --group dev
}

_compile_translations() {
    _info "Compiling translations"
    uv run pybabel compile -d quadletman/locale -D quadletman 2>/dev/null
    _ok "Translations compiled"
}

_compile_css() {
    _info "Compiling CSS"
    TAILWINDCSS_VERSION=v4.2.2 uv run tailwindcss \
        -i quadletman/static/src/app.css \
        -o quadletman/static/src/tailwind.css --minify
    _ok "CSS compiled"
}

# ---------------------------------------------------------------------------
# One-time setup
# ---------------------------------------------------------------------------

_setup_dev_user() {
    _info "Setting up dev environment (qm-dev user)"

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

    # Ensure subuid/subgid ranges for rootless podman
    DEV_UID=$(id -u "$DEV_USER")
    if ! grep -q "^${DEV_USER}:" /etc/subuid 2>/dev/null; then
        sudo usermod --add-subuids 100000-165535 "$DEV_USER"
        _ok "Added subuid range for $DEV_USER"
    else
        _ok "subuid range already configured"
    fi
    if ! grep -q "^${DEV_USER}:" /etc/subgid 2>/dev/null; then
        sudo usermod --add-subgids 100000-165535 "$DEV_USER"
        _ok "Added subgid range for $DEV_USER"
    else
        _ok "subgid range already configured"
    fi

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
# Run
# ---------------------------------------------------------------------------

_run() {
    _check_service_conflict
    _stop_existing_dev
    _ensure_venv
    _compile_css
    _compile_translations
    _setup_dev_user

    # Sync project + venv to a qm-dev-accessible location under /tmp
    DEV_SRC="$DEV_DATA/src"
    _info "Syncing project to $DEV_SRC"
    sudo -u "$DEV_USER" mkdir -p "$DEV_SRC"
    sudo rsync -a --delete \
        --exclude='__pycache__' \
        --exclude='.git' \
        --exclude='*.pyc' \
        "$PROJECT_DIR/" "$DEV_SRC/"
    sudo chown -R "$DEV_USER:$DEV_USER" "$DEV_SRC"
    # Rewrite venv shebangs to point to the synced python (originals reference
    # the workspace venv which qm-* users cannot traverse)
    sudo sed -i "1s|#!.*/python[0-9.]*|#!$DEV_SRC/.venv/bin/python3|" "$DEV_SRC"/.venv/bin/*
    _ok "Project synced"

    # Resolve the venv site-packages
    VENV_SP=""
    for sp in "$DEV_SRC"/.venv/lib/python*/site-packages; do
        [ -d "$sp" ] && VENV_SP="$sp" && break
    done
    if [ -z "$VENV_SP" ]; then
        echo "ERROR: could not find .venv site-packages" >&2
        exit 1
    fi

    _info "Starting quadletman as $DEV_USER"
    _info "Data: $DEV_DATA | Agent socket: $DEV_RUN/agent.sock"
    echo ""

    sudo -u "$DEV_USER" env \
        PATH="$DEV_SRC/.venv/bin:${PATH}" \
        QUADLETMAN_DB_PATH="$DEV_DATA/quadletman.db" \
        QUADLETMAN_VOLUMES_BASE="$DEV_DATA/volumes" \
        QUADLETMAN_AGENT_SOCKET="$DEV_RUN/agent.sock" \
        ${DEV_LOG_LEVEL:+QUADLETMAN_LOG_LEVEL=$DEV_LOG_LEVEL} \
        ${DEV_PODMAN_VERSION:+QUADLETMAN_PODMAN_VERSION_OVERRIDE=$DEV_PODMAN_VERSION} \
        PYTHONPATH="$DEV_SRC:$VENV_SP${PYTHONPATH:+:$PYTHONPATH}" \
        "$DEV_SRC/.venv/bin/python" -c "from quadletman.main import run; run()"
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

DEV_LOG_LEVEL=""
DEV_PODMAN_VERSION=""

for arg in "$@"; do
    case "$arg" in
        --debug|-d)   DEV_LOG_LEVEL="debug" ;;
        --podman-version=*)
            DEV_PODMAN_VERSION="${arg#*=}" ;;
        --help|-h)
            echo "Usage: $0 [--debug] [--podman-version=X.Y.Z]"
            echo ""
            echo "  Runs quadletman as the qm-dev system user."
            echo "  --debug                Set log level to DEBUG"
            echo "  --podman-version=X.Y.Z Override detected Podman version (UI testing)"
            echo ""
            echo "The first run creates the qm-dev system user and installs"
            echo "a dev sudoers file. Subsequent runs skip setup if already configured."
            exit 0
            ;;
        *)
            echo "Unknown option: $arg" >&2
            echo "Usage: $0 [--debug] [--podman-version=X.Y.Z]" >&2
            exit 1
            ;;
    esac
done

_run
