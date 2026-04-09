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

> **Important:** If VirtualBox is not installed on the Windows host, smoke-test VMs cannot
> be run from WSL2. The WSL2 kernel does not provide a usable VM backend for libvirt or
> VirtualBox without the host-side VM provider installed.

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

### Running the smoke tests

Run Vagrant-hosted smoke tests through the pytest harness:

```bash
uv run pytest tests/e2e -m vm --artifacts-dir=test-artifacts --report-format=json,csv,html
```

The harness will install the required Playwright browser binaries, provision the VMs, install the package, start the service, and execute
browser smoke tests automatically.

Supported flags:

- `--destroy-vms` — destroy VMs after the run
- `--reprovision` — reprovision existing VMs
- `--settings-file=<path>` — use a custom VM settings file
- `--collect-logs` — capture VM logs and artifacts

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

### Choosing a provider explicitly

Vagrant auto-selects the provider. To force one, set the `VAGRANT_DEFAULT_PROVIDER` environment variable:

```bash
export VAGRANT_DEFAULT_PROVIDER=libvirt      # Linux bare-metal
export VAGRANT_DEFAULT_PROVIDER=virtualbox   # Windows / macOS / WSL2
```

Then run the harness as usual.

See **[docs/packaging.md — Smoke testing](packaging.md#smoke-testing)** for the full
guide: prerequisites per OS, first-time setup, running/re-testing, inspecting the VMs,
and what the smoke tests verify.

### End-to-end browser tests

`tests/e2e` contains Playwright-driven browser smoke tests that verify the web UI works correctly. The E2E test harness provides both local testing (against a live local server) and VM-based testing (against clean OS installations).

#### Architecture Overview

The E2E testing system consists of:

- **Local server**: A test instance of quadletman running with authentication bypassed
- **Browser automation**: Playwright controls Chromium to interact with the web UI
- **Artifact collection**: Screenshots, videos, logs, and structured reports
- **VM provisioning**: Optional Vagrant-based testing on clean Fedora/Ubuntu/Debian systems

#### Local E2E Testing

Local tests run against a live server started as a subprocess. The server uses:

- **Test authentication**: `QUADLETMAN_TEST_AUTH_USER=testuser` bypasses PAM auth
- **Isolated database**: Temporary SQLite database in `/tmp`
- **Agent socket**: Unix socket for monitoring agent communication
- **HTTP server**: Runs on `http://127.0.0.1:18080`

Key fixtures in `tests/e2e/conftest.py`:

- `live_server`: Starts the app in TCP mode for browser tests
- `live_server_socket`: Starts the app in Unix socket mode for socket tests
- `test_artifacts_dir`: Creates per-test artifact directories
- `setup_test_artifacts`: Stores artifact paths for reporting

#### Test Types

**Browser Tests** (use `page` fixture):
- Interact with the web UI through Playwright
- Generate screenshots on failure
- Examples: `test_dashboard_loads`, `test_login_page_still_accessible`

**API Tests** (HTTP requests only):
- Test REST endpoints without browser automation
- No screenshots (no browser context)
- Examples: `test_health_endpoint`, socket connectivity tests

#### VM-Based E2E Testing

VM tests provision clean OS installations using Vagrant and run the same browser tests against real deployments. Three VMs are supported:

- **fedora**: RPM package on Fedora 41 with SELinux enforcing
- **ubuntu**: DEB package on Ubuntu 24.04
- **debian**: DEB package on Debian 13

VM tests are marked with `@pytest.mark.vm` and use parametrized fixtures:

```python
@pytest.mark.vm
def test_dashboard_loads_on_vm(page, server_url, vm_name):
    # Test runs on each VM type
    pass
```

#### Test Execution

Run local E2E tests:
```bash
uv run pytest tests/e2e -m "e2e and not vm"
```

Run VM-based E2E tests:
```bash
uv run pytest tests/e2e -m vm --artifacts-dir=test-artifacts
```

The harness automatically:
- Installs Playwright browser binaries
- Starts the test server
- Provisions VMs (for VM tests)
- Collects artifacts and generates reports

#### Artifact Collection

Tests collect debugging artifacts automatically:

- **Screenshots**: Captured on browser test failures (`failure.png`)
- **Videos**: Full test session recordings (VM tests only)
- **Logs**: Server logs, VM system logs, SELinux audit logs
- **Reports**: HTML, JSON, and CSV test result summaries

Artifacts are organized by test and VM:
```
test-artifacts/
├── local/test_health_endpoint/          # Local API test
├── fedora/test_dashboard_loads[fedora-chromium]/  # VM browser test
│   ├── failure.png                      # Screenshot (if failed)
│   ├── videos/                          # Session recordings
│   ├── vagrant.log                      # VM provisioning log
│   ├── journal.log                      # systemd logs
│   └── selinux-audit.log                # SELinux logs
└── report.html                          # Test summary report
```

#### Reporting

The harness generates structured reports showing:

- Test name, status, duration, and VM
- Links to screenshots and artifacts
- Error details for failed tests
- Summary statistics

Reports are saved as:
- `report.html`: Human-readable web report
- `report.json`: Machine-readable structured data
- `report.csv`: Spreadsheet-compatible format

#### Configuration Options

CLI options for E2E tests:

- `--artifacts-dir=DIR`: Directory for test artifacts (default: `test-artifacts`)
- `--report-format=FORMATS`: Report formats (default: `json,csv,html`)
- `--destroy-vms`: Destroy VMs after testing
- `--reprovision`: Reprovision existing VMs
- `--settings-file=PATH`: Custom VM configuration file
- `--collect-logs`: Capture VM logs and artifacts

#### VM Configuration

VM settings are defined in `tests/e2e/vm-settings.yaml`:

```yaml
vms:
  fedora:
    box: bento/fedora-41
    package: RPM
    memory: 2048
    cpus: 2
    hostname: quadletman-smoke-fedora
```

Customize VMs by creating your own settings file:

```bash
cp tests/e2e/vm-settings.yaml my-settings.yaml
# Edit my-settings.yaml
uv run pytest tests/e2e -m vm --settings-file=my-settings.yaml
```

#### Test Development

When adding new E2E tests:

1. **Browser tests**: Use `@pytest.mark.e2e` and the `page` fixture
2. **API tests**: Use `@pytest.mark.e2e` without `page` fixture
3. **VM tests**: Add `@pytest.mark.vm` for cross-platform testing
4. **Assertions**: Use standard pytest assertions
5. **Timeouts**: Playwright has built-in timeouts; adjust with `page.wait_for_selector(timeout=5000)`

The harness automatically handles:
- Browser installation
- Server lifecycle
- VM provisioning/teardown
- Artifact collection
- Report generation