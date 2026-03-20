# Packaging

quadletman is distributed as native OS packages — RPM for Fedora/RHEL-family systems and
DEB for Ubuntu/Debian-family systems. Both build scripts live under `packaging/`.

## Package architecture

Both packages are **architecture-dependent** (`Architecture: any` in the DEB control file;
`BuildArch` left unset in the RPM spec so it inherits the build host arch). This is
intentional: the packages bundle a complete Python virtualenv — including compiled C
extensions from dependencies such as `psutil` and `python-pam` — directly inside the
package. The `.so` files compiled during `dpkg-buildpackage` / `rpmbuild` are
architecture-specific binaries and cannot be shared across CPU architectures.

Keeping compilation at **build time** (rather than install time) means:

- The target machine needs no compiler, no Python headers, and no `libpam0g-dev` / `pam-devel`.
- Installation is fast and deterministic — no network access or pip invocation at install time.
- The package is self-contained: the bundled venv under `/usr/lib/quadletman/venv/` is the
  sole runtime dependency for Python code.

If you need to support a different CPU architecture, build the package on (or cross-compile
for) that architecture. A separate `.deb` or `.rpm` is produced per arch; this is standard
practice for compiled packages.

## Building packages

The `VERSION` environment variable controls the package version. If not set, the build
scripts derive it automatically from the nearest annotated git tag (e.g. `v0.3.1` →
`0.3.1`). CI passes `VERSION` explicitly when building release packages.

### RPM (Fedora / RHEL / AlmaLinux / Rocky Linux)

**Build script:** `packaging/build-rpm.sh`
**Spec file:** `packaging/rpm/quadletman.spec`

The spec `%build` section creates a virtualenv and pip-installs the app with all
dependencies. The resulting venv is copied into `%{_libdir}/quadletman/venv/` by the
`%install` section.

**Build dependencies** (install once):

```bash
sudo dnf install -y rpm-build rpmdevtools python3 python3-pip pam-devel
rpmdev-setuptree
```

**Runtime dependencies** (declared in the spec `Requires:` field):

```
python3 >= 3.12, podman, pam, systemd, sudo, procps-ng
```

**Build and install:**

```bash
bash packaging/build-rpm.sh
sudo dnf install ~/rpmbuild/RPMS/*/quadletman-*.rpm
```

### DEB (Ubuntu / Debian)

**Build script:** `packaging/build-deb.sh`
**Packaging files:** `packaging/debian/`

The `debian/rules` file overrides `dh_auto_build` to:

1. Create a fresh virtualenv under `debian/quadletman-venv/`.
2. `pip install` the app and all dependencies (including C extensions) into that venv.
3. Copy the compiled venv into the package staging tree at `usr/lib/quadletman/venv/`.

A small wrapper script at `/usr/bin/quadletman` sets `PYTHONPATH` explicitly and executes
the bundled Python interpreter, so the service does not rely on `pyvenv.cfg` symlink
resolution (which can fail in some distro configurations).

**Build dependencies** (installed automatically by `build-deb.sh` if missing):

```
debhelper dh-python python3 python3-venv python3-pip devscripts build-essential libpam0g-dev
```

**Runtime dependencies** (declared in `debian/control`):

```
python3 (>= 3.12), podman, libpam0g, systemd, sudo, procps
```

Note that `libpam0g-dev` is **not** a runtime dependency — it is only needed at build time
for compiling `python-pam`. The compiled `.so` links against `libpam.so.0` which is
provided by `libpam0g`.

**Build and install:**

```bash
bash packaging/build-deb.sh
sudo apt install ./quadletman_*.deb
```

## Upgrading

Build the new package from the updated source tree, then install over the existing package.
The service applies any pending database migrations automatically on startup.

### RPM-based systems

```bash
bash packaging/build-rpm.sh
sudo dnf upgrade ~/rpmbuild/RPMS/*/quadletman-*.rpm
sudo systemctl restart quadletman
```

### DEB-based systems

```bash
bash packaging/build-deb.sh
sudo apt install ./quadletman_*.deb
sudo systemctl restart quadletman
```

## CI release builds

Pushing an annotated tag to `main` triggers the release workflow
(`.github/workflows/release.yml`), which runs the following parallel jobs:

1. **CI gate** — full test suite; all downstream jobs depend on this.
2. **build-wheel** — builds the Python wheel via `uv build --wheel` (platform-independent).
3. **build-rpm** — builds an RPM inside a Fedora container using `packaging/build-rpm.sh`.
4. **build-deb** — builds a `.deb` on Ubuntu using `packaging/build-deb.sh`.
5. **publish** — collects all artifacts, extracts the release notes from `CHANGELOG.md`,
   and creates a GitHub Release with the wheel, RPM, and DEB attached.

See **[docs/ways-of-working.md](ways-of-working.md)** for the full release step-by-step.

## Smoke testing

Vagrant-based VMs let you build and install real packages on clean systems and verify the
application works end-to-end.

| VM | Base box | Package | Port | Extra checks |
|---|---|---|---|---|
| **fedora** (primary) | `bento/fedora-41` | RPM | `localhost:8081` | SELinux AVC denials |
| **ubuntu** | `bento/ubuntu-24.04` | DEB | `localhost:8082` | — |

Both VMs run the same HTTP smoke tests: login via PAM, authenticated GET, unauthenticated
redirect. The Fedora VM additionally verifies there are no SELinux AVC denials.

### What the smoke tests verify

1. **Package builds cleanly** from the current source tree.
2. **Service starts** and reaches `active (running)` state within 10 seconds.
3. **Authenticated GET /** returns HTTP 200 (PAM auth works, app responds).
4. **Unauthenticated GET /** returns HTTP 302/303 (auth is enforced).
5. **No SELinux AVC denials** attributed to `quadletman` (Fedora only).

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
vagrant.exe up fedora
```

Or open a Windows terminal, `cd` to the project directory, and run `vagrant up fedora` there.

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
vagrant box add bento/fedora-41          # Fedora VM (~700 MB)
vagrant box add bento/ubuntu-24.04       # Ubuntu VM (~700 MB)
```

### Running the smoke tests

```bash
vagrant up fedora      # Fedora RPM + SELinux smoke test
vagrant up ubuntu      # Ubuntu DEB smoke test
```

The Fedora VM is the primary — `vagrant ssh` without a name connects to it. The Ubuntu VM
has `autostart: false` so `vagrant up` without arguments starts only Fedora.

At the end of a successful run you will see:

```
============================================================
 All smoke tests passed.
 UI:  http://localhost:8081/        (Fedora)
 UI:  http://localhost:8082/        (Ubuntu)
 Auth: smoketest / smoketest
============================================================
```

### Re-testing after code changes

```bash
vagrant rsync fedora && vagrant provision fedora     # Fedora only
vagrant rsync ubuntu && vagrant provision ubuntu     # Ubuntu only
```

### Inspecting the VMs

```bash
vagrant ssh fedora                                # shell into Fedora VM
vagrant ssh ubuntu                                # shell into Ubuntu VM
sudo journalctl -u quadletman -f                  # follow service logs (either VM)
sudo ausearch -m avc -ts today -c quadletman      # SELinux denials (Fedora only)
sudo getenforce                                   # confirm Enforcing mode (Fedora only)
```

### Tearing down

```bash
vagrant destroy -f              # delete all VMs
vagrant destroy fedora -f       # delete only Fedora VM
vagrant destroy ubuntu -f       # delete only Ubuntu VM
```

### Choosing a provider explicitly

Vagrant auto-selects the provider. To force one:

```bash
vagrant up fedora --provider=libvirt      # Linux bare-metal
vagrant up ubuntu --provider=virtualbox   # Windows / macOS / WSL2
```
