# Testing

## Unit and integration tests

The Python test suite lives under `tests/` and is run with pytest. It must **not** run as root.

```bash
uv run pytest                        # all unit + router tests
uv run pytest tests/routers/         # router tests only
uv run pytest tests/services/        # service-layer tests only
uv run pytest tests/e2e              # Playwright browser tests (needs a live server)
npm test                             # JavaScript unit tests (Node 20+ required)
```

Key rules:
- Every test that would invoke `subprocess.run`, `os.chown`, `pwd.getpwnam`, or similar
  system APIs **must mock those calls**. Tests must not create Linux users, touch
  `/var/lib/`, call `systemctl`, or write outside `/tmp`.
- JS tests load source files via `window.eval` in jsdom — no source changes needed.
  DOM-heavy code is covered by E2E tests.

---

## RPM smoke-test VM (Fedora + SELinux)

A Vagrant-based Fedora VM lets you build and install the real RPM package and verify the
application works correctly under an SELinux-enforcing environment.

The `Vagrantfile` at the project root provisions a Fedora 41 VM, builds the RPM from the
current source tree, installs it, starts the systemd service, and runs basic smoke tests.

### Prerequisites

#### Linux bare-metal (recommended)

Install Vagrant and the libvirt provider:

```bash
# Fedora / RHEL
sudo dnf install -y vagrant libvirt libvirt-devel virt-install qemu-kvm
sudo systemctl enable --now libvirtd
sudo usermod -aG libvirt $USER   # log out and back in afterwards

vagrant plugin install vagrant-libvirt
```

#### Windows host (including WSL2)

WSL2 does not expose `/dev/kvm`, so libvirt cannot be used from WSL2. Use VirtualBox on
the Windows host instead, then invoke Vagrant from WSL2 or a Windows terminal.

Install Vagrant and VirtualBox on the **Windows host** using winget. Open a PowerShell or
Command Prompt window (not WSL2) and run:

```powershell
winget install --id HashiCorp.Vagrant  --source winget --silent
winget install --id Oracle.VirtualBox  --source winget --silent
```

Then **restart Windows** (VirtualBox installs a kernel driver that requires a reboot).

After reboot, verify the installs:

```powershell
vagrant --version
VBoxManage --version
```

Ensure `vagrant.exe` is on your `PATH` — the winget installer adds it automatically, but
open a new terminal after the restart to pick up the updated `PATH`.

> **Note:** The winget VirtualBox installer does **not** add `VBoxManage` to `PATH`.
> If the `VBoxManage --version` check above fails, add the VirtualBox install directory
> manually: open **System Properties → Environment Variables** and append
> `C:\Program Files\Oracle\VirtualBox` to the user or system `Path`. Vagrant itself
> locates VirtualBox through its own detection logic and does not rely on `PATH`.

From WSL2 you can call `vagrant.exe` directly:

```bash
cd /home/<you>/workspace/quadletman
vagrant.exe up
```

Or open a Windows terminal, `cd` to the project directory, and run `vagrant up` there.

> **Why not libvirt on WSL2?** WSL2 runs inside a Hyper-V VM. Microsoft does not expose
> `/dev/kvm` to the WSL2 kernel by default, so KVM-backed virtualisation is not available.
> VirtualBox runs on the Windows host outside WSL2 and does not have this restriction.

#### macOS

Install Vagrant and VirtualBox via Homebrew:

```bash
brew install --cask vagrant virtualbox
```

### First-time setup

```bash
vagrant box add bento/fedora-41          # download the base box once (~700 MB)
```

### Running the smoke tests

```bash
vagrant up          # create VM, build RPM, install, start service, run smoke tests
```

At the end of a successful run you will see:

```
============================================================
 All smoke tests passed.
 UI:  http://localhost:8081/
 Auth: smoketest / smoketest
============================================================
```

Open http://localhost:8081/ in your browser to exercise the UI manually.

### Re-testing after code changes

```bash
vagrant rsync        # push local source changes into the VM
vagrant provision    # re-build RPM, re-install, re-run smoke tests
```

### Inspecting the VM

```bash
vagrant ssh                                   # open a shell inside the VM
sudo journalctl -u quadletman -f              # follow service logs
sudo ausearch -m avc -ts today                # all SELinux denials today
sudo ausearch -m avc -ts today -c quadletman  # denials for quadletman only
sudo getenforce                               # confirm Enforcing mode
```

### Tearing down

```bash
vagrant destroy -f   # delete the VM and free disk space
```

### What the smoke tests verify

The provisioner script `packaging/smoke-test-vm.sh` checks:

1. **RPM builds cleanly** from the current source tree on Fedora.
2. **Service starts** and reaches `active (running)` state within 10 seconds.
3. **Authenticated GET /** returns HTTP 200 (PAM auth works, app responds).
4. **Unauthenticated GET /** returns HTTP 401 (auth is enforced).
5. **No SELinux AVC denials** attributed to the `quadletman` process.

### Choosing a provider explicitly

Vagrant auto-selects the provider. To force one:

```bash
vagrant up --provider=libvirt      # Linux bare-metal
vagrant up --provider=virtualbox   # Windows / macOS / WSL2
```
