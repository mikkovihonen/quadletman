# quadletman

A lightweight web UI for managing Podman Quadlet container services via user-level systemd.

## Features

- Create and manage "services" consisting of one or more containers
- Each service runs under a dedicated Linux system user (`qm-{service-id}`)
- Containers run as user-level systemd services with `loginctl linger` enabled
- Volumes stored at `/var/lib/quadletman/volumes/{service}/{volume}/` with SELinux contexts
- Authentication via Linux PAM — no separate credential store
- Only users in the `sudo` or `wheel` group can access the UI
- **Export** any service as a portable `.quadlets` bundle file (Podman 5.8+)
- **Import** `.quadlets` bundle files to create services from existing configurations
- **AppArmor profile** support per container (Podman 5.8+)
- **Build from Containerfile** — define containers using a local Containerfile/Dockerfile instead of a registry image (Podman 4.5+)
- **Helper users** for container UID mapping — non-root container UIDs are mapped to dedicated host users for correct volume ownership
- **Registry login** — per-service Docker/OCI registry credentials stored persistently in the service user's auth file

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

### Key source files

| File | Purpose |
|------|---------|
| `quadletman/main.py` | App entrypoint, lifespan, exception handlers |
| `quadletman/routers/api.py` | All HTTP routes (REST + HTMX) |
| `quadletman/routers/ui.py` | HTML page routes (login, index) |
| `quadletman/models.py` | Pydantic models for all data |
| `quadletman/services/service_manager.py` | Service lifecycle orchestration — use this, not lower layers directly |
| `quadletman/services/systemd_manager.py` | systemctl --user commands via sudo |
| `quadletman/services/user_manager.py` | Linux user creation, Podman config, loginctl linger |
| `quadletman/services/quadlet_writer.py` | Generates and diffs Quadlet unit files |
| `quadletman/services/metrics.py` | Per-service CPU/memory/disk metrics |
| `quadletman/auth.py` | PAM-based HTTP Basic Auth, sudo/wheel group check |
| `quadletman/database.py` | aiosqlite setup and migration runner |

### Code conventions

- **Async everywhere** — all routes and service methods are `async`. Use `aiosqlite` for DB access.
  Run blocking calls with `asyncio.get_event_loop().run_in_executor(None, fn)`.
- **HTMX dual-path** — routes check `_is_htmx(request)` and return either a Jinja2 template
  partial or a JSON response. Always maintain both paths.
- **Error handling** — raise `HTTPException` with the appropriate status code. Always chain the
  original exception: `raise HTTPException(400, "Invalid input") from exc`
- **Suppress instead of pass** — use `contextlib.suppress()` instead of `try/except/pass`
- **File I/O** — always use context managers: `with open(path) as f:`
- **Style** — 100-char line limit, double quotes, space indentation. Enforced by ruff.
  Imports at top of file, sorted: stdlib → third-party → first-party.

### Constraints

- Do not write to the DB directly — always go through `service_manager.py`
- Do not skip pre-commit hooks (`--no-verify`)
- Do not use bare `open(path).read()` without a context manager
- Do not add `from __future__ import annotations` — project targets Python 3.11+ natively
- Do not place imports inside functions or conditionally

### Testing

Run the test suite with:

```bash
uv run pytest
```

Tests must **not** be run as root — the suite guards against this. Every test that touches
code which would call `subprocess.run`, `os.chown`, `pwd.getpwnam`, or similar system APIs
must mock those calls. Tests must not create Linux users, touch `/var/lib/`, call
`systemctl`, or write outside `/tmp`.

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
is a no-op and the app will hang for 10 seconds on every service creation
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
| Rootless overlay on WSL2 | Requires `fuse-overlayfs` and `ignore_chown_errors = true` in `storage.conf`. Written automatically by quadletman on service creation. |
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

### Service Users

For each service named `my-app`, a system user and group `qm-my-app` are created:

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

Then runs `podman system reset --force` and `podman system migrate` as the service user to initialise storage with the new config.

### Helper Users

When a container is configured with explicit **UID Map** entries for non-root container UIDs, quadletman creates dedicated *helper users* (`qm-{service-id}-{container-uid}`) for each mapped UID:

- Helper users belong to the shared `qm-{service-id}` group
- Their host UID is `subuid_start + container_uid` (within the service user's subUID range, so `newuidmap` accepts the mapping)
- Volumes are created with mode `770`, owned by the service user and `qm-{service-id}` group, so helper users have write access via group membership
- When a volume's **Owner UID** is set to a non-root container UID N, the directory is owned by the helper user for that UID (`qm-{service_id}-N`) so the container process has direct owner access without needing world-readable permissions

### UID/GID Mapping

When explicit UID/GID map entries are configured for a container, quadletman generates full 65536-entry `UIDMap`/`GIDMap` blocks in the Quadlet `.container` file. Values are expressed in **rootless user-namespace coordinates** (not real host UIDs):

| Rootless NS UID/GID | Real host UID/GID |
|---|---|
| 0 | service user/group UID/GID |
| 1 | `subuid_start + 0` |
| N | `subuid_start + (N-1)` |

The generated mapping formula:
- Container 0 → NS 0 (→ service user/group)
- Container N > 0 → NS N+1 (→ `subuid_start + N` = helper user UID)
- Gap-fill entries cover the full 0..65535 range so every container UID has a valid mapping

Both `UIDMap` and `GIDMap` are always emitted together — omitting either causes crun to fail writing `/proc/{pid}/gid_map`.

> **WSL2 note:** `newuidmap` and `newgidmap` must be setuid-root (`-rwsr-xr-x`). Verify with `ls -la /usr/bin/new{u,g}idmap`. Install via `apt install uidmap` if missing.

### Registry Logins

Each service has a **Registry Logins** panel in the UI. Credentials are stored in `~/.config/containers/auth.json` (the service user's home directory) using `podman login --authfile`. This persists across reboots, unlike the default `$XDG_RUNTIME_DIR/containers/auth.json` location which lives on tmpfs.

### Quadlet Files

Container definitions are written directly to the service user's systemd config directory:

```
/home/qm-{service-id}/.config/containers/systemd/{container-name}.container
/home/qm-{service-id}/.config/containers/systemd/{container-name}-build.build  ← only when building from a Containerfile
/home/qm-{service-id}/.config/containers/systemd/{service-id}.network
```

Example generated `.container` file:

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
Description=quadletman myservice/app
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
> quadletman writes to each service user's `~/.config/containers/systemd/`.
> Do not mix both workflows on the same host, as the units will not be visible
> to each other.

### Bundle Export / Import (Podman 5.8+)

Services can be exported as a single `.quadlets` bundle file — the multi-unit format
introduced in Podman 5.8.0. Use the **↓ Export** button on any service detail page.

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

To create a service from an existing `.quadlets` bundle, click **↑ Import** in the
sidebar. Volume mounts defined in the bundle are skipped during import (Podman
named volumes and bind-mounts cannot be auto-mapped to quadletman's managed
volumes); add volumes through the UI after import.

### Volumes

Volumes are stored outside the user home directory for SELinux compatibility:

```
/var/lib/quadletman/volumes/{service-id}/{volume-name}/
```

The `container_file_t` SELinux context is applied automatically when SELinux is active.
Use the `:Z` mount option in volume configuration (default) for private relabeling.

### systemd User Commands

Commands are run as the service user via:

```bash
sudo -u qm-{service} env XDG_RUNTIME_DIR=/run/user/{uid} \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus \
  systemctl --user ...
```

## Database Migrations

Schema changes are applied automatically on startup from numbered SQL files in
`quadletman/migrations/`. Migrations are idempotent (`CREATE TABLE IF NOT EXISTS`,
`ALTER TABLE ... ADD COLUMN`).

| Migration | Description |
|---|---|
| `001_initial.sql` | Initial schema (services, containers, volumes, events) |
| `002_apparmor.sql` | Adds `apparmor_profile` column to containers |
| `003_build.sql` | Adds `build_context` and `build_file` columns to containers |
| `004_run_user.sql` | Adds `run_user` column to containers |
| `005_containerfile.sql` | Adds `containerfile` content column to containers |
| `006_bind_mounts.sql` | Adds `bind_mounts` support to containers |
| `007_user_ns.sql` | Adds `user_ns` column to containers |
| `008_uid_gid_map.sql` | Adds `uid_map` and `gid_map` columns for explicit UID/GID mapping |
| `009_volume_owner_uid.sql` | Adds `owner_uid` column to volumes (default 0 = service user) |

## Security Notes

- The application runs as `root` to manage system users and execute `sudo` commands
- It is recommended to put this behind a reverse proxy (nginx/caddy) with HTTPS
- Authentication uses the host's PAM stack — credentials are never stored by quadletman
- Only users in `sudo`/`wheel` groups are authorized, matching OS admin conventions
