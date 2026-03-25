#!/usr/bin/env bash
# Delete residual qm-* Linux users and groups left behind by quadletman.
#
# Usage:
#   sudo bash scripts/cleanup_qm_users.sh              # interactive (confirm each)
#   sudo bash scripts/cleanup_qm_users.sh --all        # delete all without prompting
#   sudo bash scripts/cleanup_qm_users.sh --dry-run    # list users/groups, change nothing
#
# Mirrors the cleanup sequence in user_manager.delete_service_user():
#   1. Stop systemd --user services
#   2. Disable linger
#   3. Terminate login session
#   4. Kill remaining processes
#   5. Delete user + home directory
#   6. Remove leftover home dir if userdel missed it
#   7. Remove the shared group (same name as the user)
#
# Also removes orphaned qm-* groups whose user no longer exists.

set -euo pipefail

MODE="interactive"
if [[ "${1:-}" == "--all" ]]; then
    MODE="all"
elif [[ "${1:-}" == "--dry-run" ]]; then
    MODE="dry-run"
fi

# Require root
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: Must be run as root." >&2
    exit 1
fi

# Collect qm-* users and groups
mapfile -t QM_USERS < <(getent passwd | awk -F: '/^qm-/ { print $1 }')
mapfile -t QM_GROUPS < <(getent group | awk -F: '/^qm-/ { print $1 }')

# Build a set of user names for quick lookup
declare -A USER_SET=()
for user in "${QM_USERS[@]}"; do
    USER_SET["$user"]=1
done

# Orphaned groups: qm-* groups with no matching qm-* user
ORPHAN_GROUPS=()
for grp in "${QM_GROUPS[@]}"; do
    if [[ -z "${USER_SET[$grp]:-}" ]]; then
        ORPHAN_GROUPS+=("$grp")
    fi
done

if [[ ${#QM_USERS[@]} -eq 0 && ${#ORPHAN_GROUPS[@]} -eq 0 ]]; then
    echo "No qm-* users or orphaned groups found."
    exit 0
fi

if [[ ${#QM_USERS[@]} -gt 0 ]]; then
    echo "Found ${#QM_USERS[@]} qm-* user(s):"
    for user in "${QM_USERS[@]}"; do
        uid=$(id -u "$user" 2>/dev/null || echo "?")
        home=$(getent passwd "$user" | cut -d: -f6)
        echo "  $user  (uid=$uid  home=$home)"
    done
fi

if [[ ${#ORPHAN_GROUPS[@]} -gt 0 ]]; then
    echo "Found ${#ORPHAN_GROUPS[@]} orphaned qm-* group(s):"
    for grp in "${ORPHAN_GROUPS[@]}"; do
        gid=$(getent group "$grp" | cut -d: -f3)
        echo "  $grp  (gid=$gid)"
    done
fi

if [[ "$MODE" == "dry-run" ]]; then
    echo ""
    echo "Dry run — no changes made."
    exit 0
fi

echo ""

delete_user() {
    local user="$1"
    local uid home

    uid=$(id -u "$user" 2>/dev/null || echo "")
    home=$(getent passwd "$user" | cut -d: -f6)

    echo "--- Deleting $user ---"

    # 1. Stop systemd --user services
    if [[ -n "$uid" && -S "/run/user/$uid/bus" ]]; then
        sudo -u "$user" \
            env "XDG_RUNTIME_DIR=/run/user/$uid" \
                "DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$uid/bus" \
            systemctl --user stop --all 2>/dev/null || true
        echo "  Stopped systemd --user services"
    fi

    # 2. Disable linger
    loginctl disable-linger "$user" 2>/dev/null || true

    # 3. Terminate login session
    loginctl terminate-user "$user" 2>/dev/null || true

    # 4. Kill remaining processes
    if [[ -n "$uid" ]]; then
        pkill -9 -u "$uid" 2>/dev/null || true
    fi

    # 5. Delete user + home directory
    userdel --remove "$user" 2>/dev/null || true

    # 6. Remove leftover home dir
    if [[ -n "$home" && -d "$home" ]]; then
        rm -rf "$home"
        echo "  Removed leftover home $home"
    fi

    # 7. Remove the shared group (same name as the user)
    if getent group "$user" >/dev/null 2>&1; then
        groupdel "$user" 2>/dev/null || true
        echo "  Removed group $user"
    fi

    echo "  Done"
}

delete_group() {
    local grp="$1"
    echo "--- Deleting orphaned group $grp ---"
    groupdel "$grp" 2>/dev/null || true
    echo "  Done"
}

for user in "${QM_USERS[@]}"; do
    if [[ "$MODE" == "all" ]]; then
        delete_user "$user"
    else
        read -rp "Delete $user? [y/N] " answer
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            delete_user "$user"
        else
            echo "  Skipped"
        fi
    fi
done

for grp in "${ORPHAN_GROUPS[@]}"; do
    if [[ "$MODE" == "all" ]]; then
        delete_group "$grp"
    else
        read -rp "Delete orphaned group $grp? [y/N] " answer
        if [[ "$answer" =~ ^[Yy]$ ]]; then
            delete_group "$grp"
        else
            echo "  Skipped"
        fi
    fi
done

echo ""
echo "Cleanup complete."
