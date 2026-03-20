#!/usr/bin/env bash
# ------------------------------------------------------------------
# smoke-test-all.sh — Build, install, and smoke-test quadletman on
# every Vagrant VM defined in the Vagrantfile.
#
# Works on:
#   • Linux bare-metal (libvirt provider)
#   • WSL2 (VirtualBox provider on the Windows host)
#
# Usage:
#   bash packaging/smoke-test-all.sh              # test all VMs
#   bash packaging/smoke-test-all.sh fedora debian # test specific VMs
#   bash packaging/smoke-test-all.sh --destroy     # tear down all VMs after
#   bash packaging/smoke-test-all.sh --reprovision # rsync + reprovision existing VMs
# ------------------------------------------------------------------
set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────
ALL_VMS=(fedora ubuntu debian)

declare -A VM_BOX=(
    [fedora]="bento/fedora-41"
    [ubuntu]="bento/ubuntu-24.04"
    [debian]="bento/debian-13"
)

declare -A VM_PORT=(
    [fedora]=8081
    [ubuntu]=8082
    [debian]=8083
)

declare -A VM_PKG=(
    [fedora]="RPM"
    [ubuntu]="DEB"
    [debian]="DEB"
)

# ── Colour helpers (disabled when stdout is not a terminal) ───────
if [[ -t 1 ]]; then
    C_RESET="\033[0m"
    C_BOLD="\033[1m"
    C_GREEN="\033[1;32m"
    C_RED="\033[1;31m"
    C_YELLOW="\033[1;33m"
    C_CYAN="\033[1;36m"
    C_DIM="\033[2m"
else
    C_RESET="" C_BOLD="" C_GREEN="" C_RED="" C_YELLOW="" C_CYAN="" C_DIM=""
fi

info()  { printf "${C_CYAN}▸${C_RESET} %s\n" "$*"; }
ok()    { printf "${C_GREEN}✓${C_RESET} %s\n" "$*"; }
warn()  { printf "${C_YELLOW}!${C_RESET} %s\n" "$*"; }
fail()  { printf "${C_RED}✗${C_RESET} %s\n" "$*"; }
fatal() { fail "$*"; exit 1; }

# ── Parse arguments ───────────────────────────────────────────────
DESTROY_AFTER=false
REPROVISION=false
REQUESTED_VMS=()

for arg in "$@"; do
    case "$arg" in
        --destroy)     DESTROY_AFTER=true ;;
        --reprovision) REPROVISION=true ;;
        --help|-h)
            echo "Usage: $0 [--destroy] [--reprovision] [vm ...]"
            echo ""
            echo "  vm ...          VMs to test (default: all — ${ALL_VMS[*]})"
            echo "  --reprovision   rsync + reprovision existing VMs instead of full up"
            echo "  --destroy       destroy VMs after testing"
            echo ""
            exit 0
            ;;
        -*)
            fatal "Unknown option: $arg (try --help)"
            ;;
        *)
            # Validate VM name
            found=false
            for v in "${ALL_VMS[@]}"; do
                [[ "$v" == "$arg" ]] && found=true && break
            done
            $found || fatal "Unknown VM: $arg (available: ${ALL_VMS[*]})"
            REQUESTED_VMS+=("$arg")
            ;;
    esac
done

# Default to all VMs if none specified
if [[ ${#REQUESTED_VMS[@]} -eq 0 ]]; then
    REQUESTED_VMS=("${ALL_VMS[@]}")
fi

# ── Detect environment ────────────────────────────────────────────
detect_environment() {
    if grep -qi microsoft /proc/version 2>/dev/null; then
        ENVIRONMENT="wsl2"
    else
        ENVIRONMENT="linux"
    fi

    if [[ "$ENVIRONMENT" == "wsl2" ]]; then
        # WSL2: use vagrant.exe (Windows host) with VirtualBox
        if command -v vagrant.exe &>/dev/null; then
            VAGRANT="vagrant.exe"
        else
            fatal "vagrant.exe not found on PATH. Install Vagrant on Windows:\n  winget install --id HashiCorp.Vagrant --source winget --silent"
        fi
        PROVIDER="virtualbox"

        # Verify VirtualBox is reachable
        if ! command -v VBoxManage.exe &>/dev/null && ! "$VAGRANT" --version &>/dev/null; then
            warn "VBoxManage.exe not found — VirtualBox may not be installed on the Windows host"
        fi
    else
        # Native Linux: use vagrant with libvirt
        if ! command -v vagrant &>/dev/null; then
            fatal "vagrant not found. Install it:\n  sudo dnf install -y vagrant   # or apt-get install vagrant"
        fi
        VAGRANT="vagrant"
        PROVIDER="libvirt"

        # Check libvirt is running
        if ! systemctl is-active --quiet libvirtd 2>/dev/null; then
            warn "libvirtd is not running — attempting to start it"
            sudo systemctl start libvirtd || fatal "Could not start libvirtd"
        fi

        # Check vagrant-libvirt plugin
        if ! $VAGRANT plugin list 2>/dev/null | grep -q vagrant-libvirt; then
            fatal "vagrant-libvirt plugin not installed. Run:\n  vagrant plugin install vagrant-libvirt"
        fi
    fi

    info "Environment: ${ENVIRONMENT} — provider: ${PROVIDER} — vagrant: ${VAGRANT}"
}

# ── Ensure Vagrant box is available ───────────────────────────────
ensure_box() {
    local box="$1"
    # Strip \r from vagrant.exe output (Windows line endings when called from WSL2)
    if $VAGRANT box list 2>/dev/null | tr -d '\r' | grep -q "^${box} .*(${PROVIDER},"; then
        ok "Box ${box} (${PROVIDER}) already downloaded"
    else
        info "Downloading box ${box} for provider ${PROVIDER} ..."
        $VAGRANT box add "$box" --provider "$PROVIDER" || \
            fatal "Failed to download box ${box}"
        ok "Box ${box} (${PROVIDER}) downloaded"
    fi
}

# ── Log directory ─────────────────────────────────────────────────
LOG_DIR=$(mktemp -d "${TMPDIR:-/tmp}/quadletman-smoke-XXXXXX")
info "Logs will be saved to ${LOG_DIR}/"

# ── Run one VM ────────────────────────────────────────────────────
# Globals filled by run_vm: VM_RESULTS associative array
declare -A VM_RESULTS
declare -A VM_DURATIONS

run_vm() {
    local vm="$1"
    local log="${LOG_DIR}/${vm}.log"
    local start_time end_time duration

    printf "\n${C_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}\n"
    printf "${C_BOLD}  %-8s  %s package on %s${C_RESET}\n" "$vm" "${VM_PKG[$vm]}" "${VM_BOX[$vm]}"
    printf "${C_BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${C_RESET}\n"

    start_time=$(date +%s)

    # Check current VM state
    local vm_state
    vm_state=$($VAGRANT status "$vm" 2>/dev/null | tr -d '\r' | grep "^${vm} " | awk '{print $2}' || echo "not_created")

    local rc=0
    if [[ "$REPROVISION" == true && "$vm_state" == "running" ]]; then
        info "Resyncing source to ${vm} ..."
        $VAGRANT rsync "$vm" >> "$log" 2>&1 || true
        info "Reprovisioning ${vm} ..."
        $VAGRANT provision "$vm" >> "$log" 2>&1 || rc=$?
    elif [[ "$REPROVISION" == true && "$vm_state" != "running" ]]; then
        warn "${vm} is not running (state: ${vm_state}) — doing full 'vagrant up' instead"
        $VAGRANT up "$vm" --provider="$PROVIDER" >> "$log" 2>&1 || rc=$?
    else
        if [[ "$vm_state" == "running" ]]; then
            info "${vm} is already running — resyncing and reprovisioning"
            $VAGRANT rsync "$vm" >> "$log" 2>&1 || true
            $VAGRANT provision "$vm" >> "$log" 2>&1 || rc=$?
        else
            info "Starting ${vm} (state: ${vm_state}) ..."
            $VAGRANT up "$vm" --provider="$PROVIDER" >> "$log" 2>&1 || rc=$?
        fi
    fi

    end_time=$(date +%s)
    duration=$(( end_time - start_time ))
    VM_DURATIONS[$vm]="${duration}"

    if [[ $rc -eq 0 ]]; then
        VM_RESULTS[$vm]="PASS"
        ok "${vm}: all smoke tests passed (${duration}s)"
    else
        VM_RESULTS[$vm]="FAIL"
        fail "${vm}: smoke tests failed (${duration}s) — see ${log}"
        # Show the last 30 lines of the log for quick diagnosis
        printf "${C_DIM}"
        tail -30 "$log" 2>/dev/null || true
        printf "${C_RESET}\n"
    fi
}

# ── Destroy VMs if requested ──────────────────────────────────────
destroy_vms() {
    info "Destroying VMs: ${REQUESTED_VMS[*]}"
    for vm in "${REQUESTED_VMS[@]}"; do
        $VAGRANT destroy "$vm" -f 2>/dev/null || true
    done
    ok "VMs destroyed"
}

# ── Main ──────────────────────────────────────────────────────────
main() {
    local total_start total_end total_duration

    printf "\n${C_BOLD}quadletman smoke-test runner${C_RESET}\n"
    printf "${C_DIM}VMs: %s${C_RESET}\n\n" "${REQUESTED_VMS[*]}"

    detect_environment

    # Ensure all required boxes are downloaded
    for vm in "${REQUESTED_VMS[@]}"; do
        ensure_box "${VM_BOX[$vm]}"
    done

    total_start=$(date +%s)

    # Run each VM sequentially
    for vm in "${REQUESTED_VMS[@]}"; do
        run_vm "$vm"
    done

    total_end=$(date +%s)
    total_duration=$(( total_end - total_start ))

    # ── Summary ───────────────────────────────────────────────────
    local pass=0 fail_count=0
    for vm in "${REQUESTED_VMS[@]}"; do
        [[ "${VM_RESULTS[$vm]}" == "PASS" ]] && (( pass++ )) || (( fail_count++ ))
    done

    printf "\n"
    printf "${C_BOLD}╔══════════════════════════════════════════════════════════════╗${C_RESET}\n"
    printf "${C_BOLD}║                    SMOKE TEST RESULTS                       ║${C_RESET}\n"
    printf "${C_BOLD}╠══════════════════════════════════════════════════════════════╣${C_RESET}\n"
    printf "${C_BOLD}║  %-10s %-22s %-8s %8s     ║${C_RESET}\n" "VM" "DISTRO" "RESULT" "TIME"
    printf "${C_BOLD}╠══════════════════════════════════════════════════════════════╣${C_RESET}\n"

    for vm in "${REQUESTED_VMS[@]}"; do
        local result="${VM_RESULTS[$vm]}"
        local dur="${VM_DURATIONS[$vm]}"
        local dur_fmt
        if [[ $dur -ge 60 ]]; then
            dur_fmt="$(( dur / 60 ))m$(( dur % 60 ))s"
        else
            dur_fmt="${dur}s"
        fi
        local colour
        if [[ "$result" == "PASS" ]]; then
            colour="$C_GREEN"
        else
            colour="$C_RED"
        fi
        printf "║  %-10s %-22s ${colour}%-8s${C_RESET} %8s     ║\n" \
            "$vm" "${VM_BOX[$vm]}" "$result" "$dur_fmt"
    done

    # Total duration
    local total_fmt
    if [[ $total_duration -ge 60 ]]; then
        total_fmt="$(( total_duration / 60 ))m$(( total_duration % 60 ))s"
    else
        total_fmt="${total_duration}s"
    fi

    printf "${C_BOLD}╠══════════════════════════════════════════════════════════════╣${C_RESET}\n"
    printf "║  %-10s %-22s %-8s %8s     ║\n" "" "" "TOTAL" "$total_fmt"
    printf "${C_BOLD}╠══════════════════════════════════════════════════════════════╣${C_RESET}\n"

    if [[ $fail_count -eq 0 ]]; then
        printf "${C_BOLD}║${C_GREEN}  All ${pass} VM(s) passed.${C_RESET}${C_BOLD}                                       ║${C_RESET}\n"
    else
        printf "${C_BOLD}║${C_RED}  ${fail_count} of $(( pass + fail_count )) VM(s) failed.${C_RESET}${C_BOLD}                                      ║${C_RESET}\n"
    fi

    printf "${C_BOLD}╠══════════════════════════════════════════════════════════════╣${C_RESET}\n"
    printf "║  Logs: %-53s║\n" "${LOG_DIR}/"
    for vm in "${REQUESTED_VMS[@]}"; do
        printf "║    %-56s ║\n" "${vm}.log"
    done
    printf "${C_BOLD}╚══════════════════════════════════════════════════════════════╝${C_RESET}\n"

    # Destroy if requested
    if [[ "$DESTROY_AFTER" == true ]]; then
        printf "\n"
        destroy_vms
    fi

    # Exit with failure if any VM failed
    [[ $fail_count -eq 0 ]]
}

main
