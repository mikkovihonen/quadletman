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

## Development Setup

The project uses [uv](https://docs.astral.sh/uv/) for dependency management and
[ruff](https://docs.astral.sh/ruff/) for linting and formatting.

```bash
# Install uv (once)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all dependencies including dev tools
uv sync --group dev

# Run the app (uses dev paths when not root)
uv run quadletman

# Lint
uv run ruff check quadletman/

# Format
uv run ruff format quadletman/

# Run tests (must NOT run as root)
uv run pytest

# Run all checks (lint + format + tests)
uv run pre-commit run --all-files

# Rebuild Tailwind CSS (re-run after adding new utility classes to any template; commit the output)
uv run tailwindcss -i quadletman/static/vendor/app.css \
  -o quadletman/static/vendor/tailwind.css --minify
```

VS Code users: install the recommended [Ruff extension](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff)
(prompted automatically via `.vscode/extensions.json`). Format-on-save and import organisation
are configured in `.vscode/settings.json`.

## Contributing

### Pre-commit hooks

Hooks run automatically on `git commit` and auto-fix what they can. To install and run manually:

```bash
uv run pre-commit install          # install into .git/hooks/ (once per clone)
uv run pre-commit run --all-files  # run all checks manually
```

Never skip hooks with `--no-verify`.

### Conventions and constraints

All contributor conventions live in [CLAUDE.md](CLAUDE.md), which is the authoritative reference:

- **[Key source files](CLAUDE.md#key-files)** — what each file does
- **[Code Patterns](CLAUDE.md#code-patterns)** — async, HTMX dual-path, error handling, style
- **[Podman Version Gating](CLAUDE.md#podman-version-gating)** — how to gate features behind version checks
- **[UI Conventions](CLAUDE.md#ui-conventions)** — macros, component classes, button patterns, modals
- **[What NOT to Do](CLAUDE.md#what-not-to-do)** — hard constraints
- **[Testing](CLAUDE.md#testing)** — test layout and mocking rules

### Testing

```bash
uv run pytest   # must NOT be run as root
```

### Security review

See [CLAUDE.md § Security Review Checklist](CLAUDE.md#security-review-checklist) for the full
trigger table and per-category checks. Run it before committing any security-relevant change.

## Running in Development

quadletman must run as **root** because it creates system users (`useradd`), manages
`loginctl linger`, reads `/etc/shadow` via PAM, and writes to `/var/lib/quadletman/`.

### Correct invocation

`uv run quadletman` will fail under `sudo` because `uv` is installed in the user's
`~/.local/bin/` which is not on root's `PATH`. Use the virtualenv binary directly:

```bash
uv sync --group dev          # install deps as your normal user first
sudo .venv/bin/quadletman
```

To keep dev data isolated from any production installation:

```bash
sudo env \
  QUADLETMAN_DB_PATH=/tmp/qm-dev.db \
  QUADLETMAN_VOLUMES_BASE=/tmp/qm-volumes \
  .venv/bin/quadletman
```

### WSL2

systemd is **not** enabled by default in WSL2. Without it, `loginctl enable-linger`
is a no-op and the app will hang for 10 seconds on every compartment creation
(the `_wait_for_runtime_dir` timeout in `user_manager.py`).

Enable systemd by adding the following to `/etc/wsl.conf` and restarting WSL:

```ini
[boot]
systemd=true
```

Additional packages required on WSL2/Ubuntu:

```bash
# Rootless Podman user namespace helpers (must be setuid-root)
sudo apt install uidmap

# fuse-overlayfs — required for overlay mounts without kernel idmap support
sudo apt install fuse-overlayfs
```

### Platform notes

| Concern | Notes |
|---|---|
| PAM authentication | Requires root to read `/etc/shadow`. Works correctly when run as root. |
| SELinux context | Applied automatically when SELinux is active. Safe to ignore on Ubuntu/WSL2 (no-op). |
| systemd user units | Require a live `XDG_RUNTIME_DIR` (`/run/user/{uid}`). Only available after `loginctl enable-linger` succeeds with systemd running. |
| Rootless overlay on WSL2 | Requires `fuse-overlayfs` and `ignore_chown_errors = true` in `storage.conf`. Written automatically by quadletman on compartment creation. |
| UID/GID mapping | Requires `newuidmap`/`newgidmap` to be setuid-root (`apt install uidmap`). |

## Configuration

Environment variables (prefix: `QUADLETMAN_`):

| Variable | Default | Description |
|---|---|---|
| `QUADLETMAN_PORT` | `8080` | Listening port |
| `QUADLETMAN_HOST` | `0.0.0.0` | Listening address |
| `QUADLETMAN_LOG_LEVEL` | `INFO` | Log level |
| `QUADLETMAN_DB_PATH` | `/var/lib/quadletman/quadletman.db` | SQLite database path |
| `QUADLETMAN_VOLUMES_BASE` | `/var/lib/quadletman/volumes` | Volume storage base |
| `QUADLETMAN_ALLOWED_GROUPS` | `["sudo","wheel"]` | Groups allowed to access UI |

## Architecture

### Compartment Roots

For each compartment named `my-app`, a system user and group `qm-my-app` are created:

```bash
groupadd --system qm-my-app
useradd --system --create-home --shell /usr/sbin/nologin --gid qm-my-app qm-my-app
loginctl enable-linger qm-my-app
```

A subUID/subGID range of 65536 entries is allocated in `/etc/subuid` and `/etc/subgid` for rootless Podman user namespace mapping.

After user creation, quadletman writes `~/.config/containers/storage.conf` to:
- Pin `graphRoot` to the user's home directory (avoids tmpfs `/run/user/{uid}` which breaks overlay UID remapping)
- Enable `fuse-overlayfs` as the overlay mount program when available
- Set `ignore_chown_errors = true` (required on WSL2 and kernels without unprivileged idmap support)

Then runs `podman system reset --force` and `podman system migrate` as the compartment root to initialise storage with the new config.

### Helper Users

When a container is configured with explicit **UID Map** entries for non-root container UIDs, quadletman creates dedicated *helper users* (`qm-{compartment-id}-{container-uid}`) for each mapped UID:

- Helper users belong to the shared `qm-{compartment-id}` group
- Their host UID is `subuid_start + container_uid` (within the compartment root's subUID range, so `newuidmap` accepts the mapping)
- Volumes are created with mode `770`, owned by the compartment root and `qm-{compartment-id}` group, so helper users have write access via group membership
- When a volume's **Owner UID** is set to a non-root container UID N, the directory is owned by the helper user for that UID (`qm-{compartment_id}-N`) so the container process has direct owner access without needing world-readable permissions

### UID/GID Mapping

When explicit UID/GID map entries are configured for a container, quadletman generates full 65536-entry `UIDMap`/`GIDMap` blocks in the Quadlet `.container` file. Values are expressed in **rootless user-namespace coordinates** (not real host UIDs):

| Rootless NS UID/GID | Real host UID/GID |
|---|---|
| 0 | compartment root/group UID/GID |
| 1 | `subuid_start + 0` |
| N | `subuid_start + (N-1)` |

The generated mapping formula:
- Container 0 → NS 0 (→ compartment root/group)
- Container N > 0 → NS N+1 (→ `subuid_start + N` = helper user UID)
- Gap-fill entries cover the full 0..65535 range so every container UID has a valid mapping

Both `UIDMap` and `GIDMap` are always emitted together — omitting either causes crun to fail writing `/proc/{pid}/gid_map`.

> **WSL2 note:** `newuidmap` and `newgidmap` must be setuid-root (`-rwsr-xr-x`). Verify with `ls -la /usr/bin/new{u,g}idmap`. Install via `apt install uidmap` if missing.

### Registry Logins

Each compartment has a **Registry Logins** panel in the UI. Credentials are stored in `~/.config/containers/auth.json` (the compartment root's home directory) using `podman login --authfile`. This persists across reboots, unlike the default `$XDG_RUNTIME_DIR/containers/auth.json` location which lives on tmpfs.

### Quadlet Files

Container definitions are written directly to the compartment root's systemd config directory:

```
/home/qm-{compartment-id}/.config/containers/systemd/{container-name}.container
/home/qm-{compartment-id}/.config/containers/systemd/{container-name}-build.build  ← only when building from a Containerfile
/home/qm-{compartment-id}/.config/containers/systemd/{compartment-id}.network
```

Example generated `.container` file for a compartment `myapp`, container `web`:

```ini
[Unit]
Description=quadletman myapp/web

[Container]
Image=docker.io/library/nginx:latest
ContainerName=myapp-web
Network=host
PublishPort=8080:80
Environment=ENV=production
AppArmor=localhost/my-profile

[Service]
Restart=always

[Install]
WantedBy=default.target
```

### Build from Containerfile (Podman 4.5+)

When a container is configured with a **Build Context Directory**, quadletman generates
a `.build` unit alongside the `.container` unit. The `Image` field is used as the local
image tag assigned to the built image.

Example pair for a container named `app` with build context `/srv/myapp`:

```ini
# app-build.build
[Build]
ImageTag=localhost/myapp:latest
SetWorkingDirectory=/srv/myapp
```

```ini
# app.container
[Unit]
Description=quadletman myapp/app
After=app-build.service
Requires=app-build.service

[Container]
Image=localhost/myapp:latest
...
```

systemd ensures `app-build.service` (which runs `podman build`) always completes before
`app.service` starts. The `Image` field in the container form doubles as the local image
tag — use the `localhost/` prefix to make it unambiguous.

> **Note — `podman quadlet install` path conflict:** When running as root,
> `podman quadlet install` places files in `/etc/containers/systemd/`, whereas
> quadletman writes to each compartment root's `~/.config/containers/systemd/`.
> Do not mix both workflows on the same host, as the units will not be visible
> to each other.

### Bundle Export / Import (Podman 5.8+)

Compartments can be exported as a single `.quadlets` bundle file — the multi-unit format
introduced in Podman 5.8.0. Use the **↓ Export** button on any compartment detail page.

The resulting file contains all `.container` and `.network` units separated by `---`
delimiters, for example:

```ini
# FileName=web
[Unit]
Description=quadletman myapp/web

[Container]
Image=nginx:latest
...
---
# FileName=myapp
[Network]
NetworkName=myapp
```

To create a compartment from an existing `.quadlets` bundle, click **↑ Import** in the
sidebar. Volume mounts defined in the bundle are skipped during import (Podman
named volumes and bind-mounts cannot be auto-mapped to quadletman's managed
volumes); add volumes through the UI after import.

### Volumes

Volumes are stored outside the user home directory for SELinux compatibility:

```
/var/lib/quadletman/volumes/{compartment-id}/{volume-name}/
```

The `container_file_t` SELinux context is applied automatically when SELinux is active.
Use the `:Z` mount option in volume configuration (default) for private relabeling.

### systemd User Commands

Commands are run as the compartment root via:

```bash
sudo -u qm-{compartment-id} env XDG_RUNTIME_DIR=/run/user/{uid} \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus \
  systemctl --user ...
```

## Database Migrations

Schema changes are applied automatically on startup from numbered SQL files in
`quadletman/migrations/`. Each file is tracked in a `schema_migrations` table and applied
exactly once — files already recorded are skipped on subsequent startups.

| Migration | Description |
|---|---|
| `001_initial.sql` | Full schema: compartments, containers, volumes, pods, image units, events |

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
