# quadletman

A lightweight web UI for managing Podman Quadlet container services via user-level systemd.

## Features

- Create and manage **compartments** — each a named group of one or more containers
- Each compartment runs under a dedicated Linux system user (`qm-{compartment-id}`)
- Containers run as user-level systemd services with `loginctl linger` enabled
- Volumes stored at `/var/lib/quadletman/volumes/{compartment-id}/{volume-name}/` with SELinux contexts
- Authentication via Linux PAM — no separate credential store
- Only users in the `sudo` or `wheel` group can access the UI
- **Export** any compartment as a portable `.quadlets` bundle file (Podman 5.8+)
- **Import** `.quadlets` bundle files to create compartments from existing configurations
- **AppArmor profile** support per container (Podman 5.8+)
- **Build from Containerfile** — define containers using a local Containerfile/Dockerfile instead of a registry image (Podman 4.5+)
- **Helper users** for container UID mapping — non-root container UIDs are mapped to dedicated host users for correct volume ownership
- **Registry login** — per-compartment Docker/OCI registry credentials stored persistently in the compartment root's auth file
- **Host kernel settings** — view and apply relevant sysctl settings (unprivileged port start, IP forwarding, user namespaces, inotify limits, etc.) from the top bar; changes persist across reboots via `/etc/sysctl.d/99-quadletman.conf`
- **Secrets management** — create and assign Podman secrets to containers; secrets are stored per-compartment in the Podman secret store and injected via `Secret=` in unit files
- **Scheduled timers** — create systemd `.timer` units that run a container on a calendar schedule (`OnCalendar=`) or after boot (`OnBootSec=`); manage from the compartment detail page
- **Service templates** — snapshot a compartment's configuration as a reusable template; instantiate new compartments from a template with a single action (secret references are cleared on clone)
- **Notification webhooks** — register HTTP callbacks for `on_start`, `on_stop`, `on_failure`, and `on_restart` events per container; delivery is retried with exponential backoff (up to 3 attempts)
- **Host device passthrough** — pass host devices (GPU, serial, etc.) into containers via `AddDevice=`
- **Network mode flexibility** — choose host, none, slirp4netns, pasta, or a named network per container; add extra network aliases
- **OCI runtime selection** — specify a custom `--runtime` (e.g. crun, runc, kata) per container
- **Init process** — run tini as PID 1 for zombie reaping and signal forwarding
- **Resource weights** — set `CPUWeight=`, `IOWeight=`, and `MemoryLow=` (memory reservation) per container
- **Log rotation** — configure `max-size` and `max-file` log options for json-file and k8s-file log drivers
- **Extra [Service] directives** — pass raw systemd `[Service]` section entries for advanced use cases
- **Container image management** — list, prune dangling, and re-pull images in a compartment's Podman store
- **Database backup** — download a consistent hot backup of the SQLite database via `GET /api/backup/db`
- **Metrics history** — per-compartment CPU/memory/disk snapshots sampled every 5 minutes; queryable via API
- **Restart analytics** — track per-container restart and failure counts and timestamps; queryable via API
- **Timer last-run status** — display last trigger time and next scheduled run for each systemd timer

## Comparison with Similar Tools

quadletman targets a specific gap: a **headless server-side web UI** that manages containers
at the **systemd unit file level** rather than via the Podman socket API.

| Tool | Interface | Creates/edits Quadlet unit files | Per-service OS user isolation | Server-side web UI |
|---|---|---|---|---|
| **quadletman** | Web (HTMX) | **Yes** | **Yes** | **Yes** |
| [cockpit-podman](https://github.com/cockpit-project/cockpit-podman) | Web (Cockpit) | No — shows running containers only | No | Yes |
| [Podman Desktop](https://github.com/podman-desktop/podman-desktop) | Desktop app (Electron) | Yes (via extension) | No | No |
| [Portainer](https://github.com/portainer/portainer) | Web | No | No | Yes |
| [Dockge](https://github.com/louislam/dockge) | Web | No — Docker Compose only | No | Yes |
| [podman-tui](https://github.com/containers/podman-tui) | Terminal (TUI) | No | No | No |

**cockpit-podman** is the closest server-side alternative. It shows Podman containers (including
ones already started by Quadlet units) but does not create or edit unit files, manage system
users, or handle volumes with SELinux labels. It is a read/run UI, not a provisioning tool.

**Podman Desktop** is the only other tool that actually generates and edits Quadlet unit files
through a form interface, but it is a developer desktop application requiring an installed GUI
environment — not a tool for administering a headless Linux server remotely.

**Portainer** and **Dockge** are Docker-centric and treat Podman as a drop-in Docker socket
replacement. Neither has any concept of Quadlet unit files, systemd user services, or
per-service Linux user isolation.

quadletman's distinctive combination — generating Quadlet unit files, running each service
group under its own isolated Linux user, managing host volumes with SELinux contexts, and
doing all of this from a browser against a headless server — is not covered by any existing
tool.

## Requirements

- Python 3.11+
- Podman with Quadlet support (Podman 4.4+; build units require Podman 4.5+; bundle import/export requires Podman 5.8+)
- systemd (with `loginctl` and `machinectl`)
- Linux PAM development headers (`pam-devel` / `libpam0g-dev`)
- Optional: SELinux tools (`policycoreutils-python-utils`) for context management

## Installation

### Fedora / RHEL / AlmaLinux / Rocky Linux (RPM)

```bash
# Install build tools (once)
sudo dnf install -y rpm-build rpmdevtools python3 python3-pip
rpmdev-setuptree

# Build and install the RPM
bash packaging/build-rpm.sh
sudo dnf install ~/rpmbuild/RPMS/noarch/quadletman-*.noarch.rpm
```

### Ubuntu / Debian (DEB)

```bash
# Install build tools (once)
sudo apt-get install -y debhelper dh-python python3 python3-venv \
                        python3-pip devscripts build-essential

# Build and install the .deb
bash packaging/build-deb.sh
sudo apt install ./quadletman_*.deb
```

### Generic (any systemd Linux)

```bash
sudo bash install.sh
```

The web UI will be available at `http://<host>:8080`.

## Development

See **[docs/development.md](docs/development.md)** for setup, running locally, WSL2 notes,
contributing guidelines, testing, and database migrations.

Quick start:

```bash
uv sync --group dev        # install deps
sudo .venv/bin/quadletman  # run as root
uv run pytest              # run tests (not as root)
```

## Configuration

Environment variables (prefix: `QUADLETMAN_`):

| Variable | Default | Description |
|---|---|---|
| `QUADLETMAN_PORT` | `8080` | Listening port |
| `QUADLETMAN_HOST` | `0.0.0.0` | Listening address |
| `QUADLETMAN_LOG_LEVEL` | `INFO` | Log level (`DEBUG`, `INFO`, `WARNING`, `ERROR`) |
| `QUADLETMAN_DB_PATH` | `/var/lib/quadletman/quadletman.db` | SQLite database path |
| `QUADLETMAN_VOLUMES_BASE` | `/var/lib/quadletman/volumes` | Volume storage base directory |
| `QUADLETMAN_ALLOWED_GROUPS` | `["sudo","wheel"]` | OS groups permitted to log in |
| `QUADLETMAN_SECURE_COOKIES` | `false` | Set `true` when serving over HTTPS — adds `Secure` flag to session cookies and enables HSTS |
| `QUADLETMAN_TEST_AUTH_USER` | *(unset)* | **⚠ Never set in production.** When non-empty, bypasses PAM and returns this username for every request. For Playwright E2E tests only. |

## Architecture

See **[docs/architecture.md](docs/architecture.md)** for details on compartment roots, helper
users, UID/GID mapping, registry logins, Quadlet file generation, bundle export/import,
volumes, and systemd user commands.

## Further Reading

| Document | Contents |
|---|---|
| [docs/architecture.md](docs/architecture.md) | Compartment roots, helper users, UID/GID mapping, Quadlet files, volumes |
| [docs/development.md](docs/development.md) | Dev setup, running locally, WSL2, testing, contributing, migrations |
| [docs/ui-development.md](docs/ui-development.md) | UI state management, Alpine/HTMX patterns, macros, button styles, modals |
| [CLAUDE.md](CLAUDE.md) | AI/contributor conventions — code patterns, security checklist, version gating |

## Security Notes

- The application runs as `root` to manage system users and execute `sudo` commands
- It is recommended to put this behind a reverse proxy (nginx/caddy) with HTTPS
- Authentication uses the host's PAM stack — credentials are never stored by quadletman
- Only users in `sudo`/`wheel` groups are authorized, matching OS admin conventions
- Session cookies: HTTPOnly, SameSite=Strict; set `QUADLETMAN_SECURE_COOKIES=true` for the
  Secure flag (required when serving over HTTPS)
- CSRF protection: double-submit cookie pattern — every mutating request must include an
  `X-CSRF-Token` header matching the `qm_csrf` cookie
- Security headers on every response: `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  Content Security Policy, `Referrer-Policy: same-origin` (HSTS when `secure_cookies=True`)
- Container image references and bind-mount paths are validated server-side; sensitive host
  directories (`/etc`, `/proc`, `/sys`, etc.) cannot be bind-mounted into containers
- File writes use `O_NOFOLLOW` to prevent symlink-swap (TOCTOU) attacks inside volume directories
- `QUADLETMAN_TEST_AUTH_USER` bypasses PAM entirely — **never set this in production**; it exists
  solely for Playwright E2E tests running against a dev server
