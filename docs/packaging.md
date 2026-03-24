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

The CI release workflow builds packages for **x86_64** and **ARM64** (aarch64) on every
release. If you need to support a different CPU architecture, build the package on (or
cross-compile for) that architecture. A separate `.deb` or `.rpm` is produced per arch;
this is standard practice for compiled packages.

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
3. **build-rpm** (×2) — builds RPMs inside a Fedora container for x86_64 (`ubuntu-latest`)
   and aarch64 (`ubuntu-24.04-arm`) using `packaging/build-rpm.sh`.
4. **build-deb** (×2) — builds `.deb` packages on Ubuntu for amd64 (`ubuntu-latest`) and
   arm64 (`ubuntu-24.04-arm`) using `packaging/build-deb.sh`.
5. **publish** — collects all artifacts, extracts the release notes from `CHANGELOG.md`,
   and creates a GitHub Release with the wheel, RPMs, and DEBs attached.
6. **publish-repo** — builds GPG-signed RPM and DEB repository metadata (multi-arch),
   uploads the repo as a tarball release asset (`repo-site.tar.gz`), and triggers the Docs
   workflow to redeploy GitHub Pages with both docs and packages.

See **[docs/ways-of-working.md](ways-of-working.md)** for the full release step-by-step.

## Package repository (GitHub Pages)

Releases are published to GPG-signed package repositories hosted on GitHub Pages. Two
channels are maintained:

| Channel | URL | Contents |
|---|---|---|
| **stable** | `https://mikkovihonen.github.io/quadletman/packages/stable/` | Stable releases only (tags without `-`) |
| **unstable** | `https://mikkovihonen.github.io/quadletman/packages/unstable/` | Pre-releases (`-alpha`, `-rc`, etc.) |

Users add one or both repositories and receive updates via `dnf upgrade` / `apt upgrade`.

### How it works

The Docs workflow (`.github/workflows/docs.yml`) is the **single deployer** to GitHub Pages.
It builds the MkDocs documentation site and merges in the package repository files from
releases. This avoids the conflict that would arise from two independent workflows deploying
to the same gh-pages branch.

**On docs changes** (push to `main` affecting `docs/`, `README.md`, or `mkdocs.yml`):

1. Builds the MkDocs site into `site/`.
2. Downloads `repo-stable.tar.gz` from the latest stable GitHub Release (if it exists).
3. Downloads `repo-unstable.tar.gz` from the latest pre-release (if it exists).
4. Extracts into `site/packages/stable/` and `site/packages/unstable/`.
5. Deploys the combined site to gh-pages via `ghp-import --force`.

**On any release** (tag push):

1. The `publish-repo` job in the Release workflow determines the channel from the tag
   (stable if no `-`, unstable otherwise), builds GPG-signed repository metadata
   (`scripts/publish-repo.sh`), and uploads it as `repo-{channel}.tar.gz` on the
   GitHub Release.
2. The job then triggers the Docs workflow via `workflow_dispatch`, which picks up both
   channel tarballs and redeploys docs and packages together.

### Repository layout

```
gh-pages/
├── (mkdocs documentation site)         # MkDocs-generated docs at the root
└── packages/
    ├── stable/                          # Stable channel
    │   ├── gpg-key.asc
    │   ├── index.html
    │   ├── rpm/
    │   │   ├── quadletman-*.rpm
    │   │   └── repodata/
    │   │       ├── repomd.xml
    │   │       └── repomd.xml.asc
    │   └── deb/
    │       ├── pool/
    │       │   └── quadletman_*.deb      # All architectures in one pool
    │       └── dists/stable/
    │           ├── Release
    │           ├── Release.gpg
    │           ├── InRelease
    │           └── main/
    │               ├── binary-amd64/
    │               │   ├── Packages
    │               │   └── Packages.gz
    │               └── binary-arm64/
    │                   ├── Packages
    │                   └── Packages.gz
    └── unstable/                        # Unstable channel (same structure)
        ├── gpg-key.asc
        ├── index.html
        ├── rpm/
        │   └── ...
        └── deb/
            └── ...
```

### User install instructions

Replace `{CHANNEL}` with `stable` or `unstable` in the commands below.

**Fedora / RHEL / AlmaLinux / Rocky Linux:**

```bash
sudo rpm --import https://mikkovihonen.github.io/quadletman/packages/{CHANNEL}/gpg-key.asc
sudo tee /etc/yum.repos.d/quadletman.repo <<'EOF'
[quadletman]
name=quadletman
baseurl=https://mikkovihonen.github.io/quadletman/packages/{CHANNEL}/rpm/
enabled=1
gpgcheck=1
gpgkey=https://mikkovihonen.github.io/quadletman/packages/{CHANNEL}/gpg-key.asc
EOF
sudo dnf install quadletman
```

**Ubuntu / Debian:**

```bash
curl -fsSL https://mikkovihonen.github.io/quadletman/packages/{CHANNEL}/gpg-key.asc \
  | sudo gpg --dearmor -o /etc/apt/keyrings/quadletman.gpg
echo "deb [signed-by=/etc/apt/keyrings/quadletman.gpg] \
  https://mikkovihonen.github.io/quadletman/packages/{CHANNEL}/deb/ stable main" \
  | sudo tee /etc/apt/sources.list.d/quadletman.list
sudo apt update
sudo apt install quadletman
```

### GPG signing key management

The signing key is managed with `scripts/repo-gpg-key.sh`. See the script's built-in help
(`./scripts/repo-gpg-key.sh`) for all commands.

**Initial setup (one-time, maintainer only):**

```bash
./scripts/repo-gpg-key.sh generate          # create Ed25519 key (3-year expiry)
./scripts/repo-gpg-key.sh export            # write public key to packaging/repo/
./scripts/repo-gpg-key.sh ci-export \
  | gh secret set GPG_PRIVATE_KEY           # store private key in GitHub secrets
git add packaging/repo/ && git commit -m "Add repo signing key"
```

**Key rotation (before expiry):**

```bash
./scripts/repo-gpg-key.sh info              # check days until expiry
./scripts/repo-gpg-key.sh rotate            # generate successor, cross-sign
./scripts/repo-gpg-key.sh ci-export \
  | gh secret set GPG_PRIVATE_KEY           # update GitHub secret
git add packaging/repo/ && git commit -m "Rotate repo signing key"
```

Rotation cross-signs the new key with the old key, so users who trust the old key can
verify the successor. The old public key is archived as `packaging/repo/gpg-key-old.asc`
and a `KEY-TRANSITION.md` is generated with user-facing migration instructions.

**Key files:**

| File | Purpose |
|---|---|
| `packaging/repo/gpg-key.asc` | Active public key (committed to git, deployed to gh-pages) |
| `packaging/repo/gpg-fingerprint.txt` | Fingerprint for verification |
| `packaging/repo/gpg-revocation.asc` | Pre-generated revocation certificate |
| `packaging/repo/gpg-key-old.asc` | Previous key (after rotation) |
| `packaging/repo/KEY-TRANSITION.md` | User-facing rotation notice (after rotation) |
| `scripts/repo-gpg-key.sh` | Key lifecycle automation |
| `scripts/publish-repo.sh` | Repository metadata builder |

### GitHub repository settings required

1. **GitHub Pages:** Settings → Pages → Source: "Deploy from a branch" → Branch: `gh-pages` / `/ (root)`.
2. **Secret:** Settings → Secrets → Actions → `GPG_PRIVATE_KEY` (base64-encoded private key).

### Local testing

Build the repository locally without signing to test the layout:

```bash
mkdir /tmp/artifacts
cp ~/rpmbuild/RPMS/*/quadletman-*.rpm /tmp/artifacts/
cp quadletman_*.deb /tmp/artifacts/
bash scripts/publish-repo.sh /tmp/artifacts/ --unsigned
# Inspect _site/ directory
```

## Smoke testing

Vagrant-based VMs let you build and install real packages on clean systems and verify the
application works end-to-end.

| VM | Base box | Package | Port | Extra checks |
|---|---|---|---|---|
| **fedora** (primary) | `bento/fedora-41` | RPM | `localhost:8081` | SELinux AVC denials |
| **ubuntu** | `bento/ubuntu-24.04` | DEB | `localhost:8082` | — |
| **debian** | `bento/debian-13` | DEB | `localhost:8083` | — |

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
vagrant box add bento/debian-13           # Debian VM (~700 MB)
```

### Running the smoke tests

**All VMs at once** (recommended):

```bash
bash packaging/smoke-test-all.sh                     # all VMs sequentially
bash packaging/smoke-test-all.sh fedora debian        # specific VMs only
bash packaging/smoke-test-all.sh --reprovision        # rsync + reprovision running VMs
bash packaging/smoke-test-all.sh --destroy            # tear down VMs after testing
```

The script auto-detects the environment (native Linux → libvirt, WSL2 → VirtualBox),
downloads missing Vagrant boxes, runs each VM sequentially, and prints a summary table.

**Individual VMs:**

```bash
vagrant up fedora      # Fedora RPM + SELinux smoke test
vagrant up ubuntu      # Ubuntu DEB smoke test
vagrant up debian      # Debian DEB smoke test (minimal)
```

The Fedora VM is the primary — `vagrant ssh` without a name connects to it. The Ubuntu and
Debian VMs have `autostart: false` so `vagrant up` without arguments starts only Fedora.

At the end of a successful run you will see:

```
============================================================
 All smoke tests passed.
 UI:  http://localhost:8081/        (Fedora)
 UI:  http://localhost:8082/        (Ubuntu)
 UI:  http://localhost:8083/        (Debian)
 Auth: smoketest / smoketest
============================================================
```

### Re-testing after code changes

```bash
vagrant rsync fedora && vagrant provision fedora     # Fedora only
vagrant rsync ubuntu && vagrant provision ubuntu     # Ubuntu only
vagrant rsync debian && vagrant provision debian     # Debian only
```

### Inspecting the VMs

```bash
vagrant ssh fedora                                # shell into Fedora VM
vagrant ssh ubuntu                                # shell into Ubuntu VM
vagrant ssh debian                                # shell into Debian VM
sudo journalctl -u quadletman -f                  # follow service logs (any VM)
sudo ausearch -m avc -ts today -c quadletman      # SELinux denials (Fedora only)
sudo getenforce                                   # confirm Enforcing mode (Fedora only)
```

### Tearing down

```bash
vagrant destroy -f              # delete all VMs
vagrant destroy fedora -f       # delete only Fedora VM
vagrant destroy ubuntu -f       # delete only Ubuntu VM
vagrant destroy debian -f       # delete only Debian VM
```

### Choosing a provider explicitly

Vagrant auto-selects the provider. To force one:

```bash
vagrant up fedora --provider=libvirt      # Linux bare-metal
vagrant up ubuntu --provider=virtualbox   # Windows / macOS / WSL2
```
