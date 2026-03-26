# Architecture

This document describes the internal architecture of quadletman — how compartments map to
Linux users, how Quadlet unit files are generated, and how volumes and registry credentials
are managed.

## Software Stack

### Backend

| Component | Library / version | Role |
|-----------|-------------------|------|
| Language | Python 3.12+ | Runtime |
| Web framework | [FastAPI](https://fastapi.tiangolo.com/) | ASGI application, routing, dependency injection, Pydantic integration |
| ASGI server | [Uvicorn](https://www.uvicorn.org/) | HTTP server (invoked via `uv run quadletman`) |
| Data validation | [Pydantic v2](https://docs.pydantic.dev/) | Request/response models, field validators, branded-type coercion |
| Database | SQLite (via `aiosqlite`) | Persistent storage; WAL mode + foreign keys enforced on every connection |
| ORM / query builder | [SQLAlchemy 2.x async](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html) | `AsyncSession`, Core `select/insert/update/delete`, `async_sessionmaker` |
| Migrations | [Alembic](https://alembic.sqlalchemy.org/) | Revision-based schema migrations; `autogenerate` from ORM models; applied at startup via `init_db()` |
| Authentication | [python-pam](https://github.com/FirefighterBlu3/python-pam) | PAM-based HTTP Basic Auth; restricted to `sudo`/`wheel` group |
| Session management | Custom store (`quadletman/security/session.py`) + kernel keyring (`quadletman/security/keyring.py`) | Cookie-backed sessions with absolute + idle TTL; credentials stored in kernel keyring when `libkeyutils` is available, Fernet-encrypted in-memory fallback |
| Internationalisation | [Babel](https://babel.pocoo.org/) + `gettext` | String extraction, `.po`/`.mo` compilation, runtime `_()` wrapper |
| Templates | [Jinja2](https://jinja.palletsprojects.com/) | Server-side HTML rendering via `Jinja2Templates` |

### Frontend

| Component | Library | Role |
|-----------|---------|------|
| Hypermedia | [HTMX](https://htmx.org/) | Partial page updates; HTML-over-the-wire; no JS build step |
| Reactive state | [Alpine.js](https://alpinejs.dev/) | Lightweight in-template reactivity (modals, tabs, toggles) |
| CSS | [Tailwind CSS v4](https://tailwindcss.com/) | Utility-first styling; compiled offline with the standalone CLI — no CDN |
| Icons | [Heroicons](https://heroicons.com/) (inlined SVG) | UI icons served as static files |

### Container runtime integration

| Component | Tool | Role |
|-----------|------|------|
| Container engine | [Podman](https://podman.io/) (rootless, per-compartment user) | Runs containers; quadletman manages Podman via Quadlet unit files |
| Unit file format | [Podman Quadlet](https://docs.podman.io/en/latest/markdown/podman-systemd.unit.5.html) | Declarative `.container`/`.network`/`.volume`/`.image`/`.timer` unit files consumed by systemd's Quadlet generator |
| Service manager | systemd (user instance per compartment) | Starts/stops/enables container services; accessed via `systemctl --user` as the compartment system user |
| Privilege helper | `sudo` | Routes systemd user-instance commands to each compartment's system user; admin operations escalate via the authenticated web user's sudo credentials |
| User namespaces | `newuidmap` / `newgidmap` (setuid-root) | Rootless UID/GID mapping for Podman; installed via `apt install uidmap` |
| Overlay mounts | `fuse-overlayfs` (optional) | Required on kernels without unprivileged idmap support (WSL2, older Fedora) |

### Tooling

| Tool | Role |
|------|------|
| [uv](https://docs.astral.sh/uv/) | Dependency management, virtual environment, script runner |
| [ruff](https://docs.astral.sh/ruff/) | Linting and formatting (replaces flake8 + black + isort) |
| [pre-commit](https://pre-commit.com/) | Git hook runner; enforces lint/format/test on every commit |
| tailwindcss CLI | Compiles `app.css` → `tailwind.css`; must be re-run after adding new utility classes |

### Testing

| Tool | Scope |
|------|-------|
| [pytest](https://pytest.org/) + [pytest-asyncio](https://pytest-asyncio.readthedocs.io/) | Python unit and integration tests |
| [httpx](https://www.python-httpx.org/) + `ASGITransport` | In-process HTTP route tests (no running server needed) |
| [pytest-mock](https://pytest-mock.readthedocs.io/) | Mock `subprocess`, `os`, `pwd` calls in service-layer tests |
| [Playwright](https://playwright.dev/python/) | End-to-end browser tests against a live server (`tests/e2e/`) |
| [Vitest](https://vitest.dev/) | JavaScript unit tests for pure functions in `static/src/` (`npm test`) |

## Compartment Roots

For each compartment named `my-app`, a system user and group `qm-my-app` are created:

```bash
groupadd --system qm-my-app
useradd --system --create-home --shell /usr/sbin/nologin --gid qm-my-app qm-my-app
loginctl enable-linger qm-my-app
```

A subUID/subGID range of 65536 entries is allocated in `/etc/subuid` and `/etc/subgid` for
rootless Podman user namespace mapping.

After user creation, quadletman writes `~/.config/containers/storage.conf` to:
- Pin `graphRoot` to the user's home directory (avoids tmpfs `/run/user/{uid}` which breaks
  overlay UID remapping)
- Enable `fuse-overlayfs` as the overlay mount program when available
- Set `ignore_chown_errors = true` (required on WSL2 and kernels without unprivileged idmap
  support)

Then runs `podman system reset --force` and `podman system migrate` as the compartment root
to initialise storage with the new config.

## Helper Users

When a container is configured with explicit **UID Map** entries for non-root container UIDs,
quadletman creates dedicated *helper users* (`qm-{compartment-id}-{container-uid}`) for each
mapped UID:

- Helper users belong to the shared `qm-{compartment-id}` group
- Their host UID is `subuid_start + container_uid` (within the compartment root's subUID
  range, so `newuidmap` accepts the mapping)
- Volumes are created with mode `770`, owned by the compartment root and `qm-{compartment-id}`
  group, so helper users have write access via group membership
- When a volume's **Owner UID** is set to a non-root container UID N, the directory is owned
  by the helper user for that UID (`qm-{compartment_id}-N`) so the container process has
  direct owner access without needing world-readable permissions

## UID/GID Mapping

When explicit UID/GID map entries are configured for a container, quadletman generates full
65536-entry `UIDMap`/`GIDMap` blocks in the Quadlet `.container` file. Values are expressed
in **rootless user-namespace coordinates** (not real host UIDs):

| Rootless NS UID/GID | Real host UID/GID |
|---|---|
| 0 | compartment root/group UID/GID |
| 1 | `subuid_start + 0` |
| N | `subuid_start + (N-1)` |

The generated mapping formula:
- Container 0 → NS 0 (→ compartment root/group)
- Container N > 0 → NS N+1 (→ `subuid_start + N` = helper user UID)
- Gap-fill entries cover the full 0..65535 range so every container UID has a valid mapping

Both `UIDMap` and `GIDMap` are always emitted together — omitting either causes crun to fail
writing `/proc/{pid}/gid_map`.

> **WSL2 note:** `newuidmap` and `newgidmap` must be setuid-root (`-rwsr-xr-x`). Verify with
> `ls -la /usr/bin/new{u,g}idmap`. Install via `apt install uidmap` if missing.

## Registry Logins

Each compartment has a **Registry Logins** panel in the UI. Credentials are stored in
`~/.config/containers/auth.json` (the compartment root's home directory) using
`podman login --authfile`. This persists across reboots, unlike the default
`$XDG_RUNTIME_DIR/containers/auth.json` location which lives on tmpfs.

## Quadlet Files

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

## Build from Containerfile (Podman 4.5+)

When a container is configured with a **Build Context Directory**, quadletman generates a
`.build` unit alongside the `.container` unit. The `Image` field is used as the local image
tag assigned to the built image.

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

> **Note — `podman quadlet install` path conflict:** When the app process runs as root,
> `podman quadlet install` places files in `/etc/containers/systemd/`, whereas
> quadletman writes to each compartment root's `~/.config/containers/systemd/`.
> Do not mix both workflows on the same host, as the units will not be visible
> to each other.

## Bundle Export / Import (Podman 5.8+)

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
sidebar. Volume mounts defined in the bundle are skipped during import (Podman named volumes
and bind-mounts cannot be auto-mapped to quadletman's managed volumes); add volumes through
the UI after import.

## Volumes

Volumes are stored outside the user home directory for SELinux compatibility:

```
/var/lib/quadletman/volumes/{compartment-id}/{volume-name}/
```

The `container_file_t` SELinux context is applied automatically when SELinux is active. Use
the `:Z` mount option in volume configuration (default) for private relabeling.

## Input Trust Boundaries

quadletman enforces a **four-layer** input sanitization contract using **branded string types**
defined in `quadletman/models/sanitized.py`. This prevents user-supplied strings from reaching
host-mutating operations without proven validation. Holding a branded instance is proof that
the value has been validated — no re-checking at the call site is needed.

| Layer | Where | What happens |
|-------|--------|--------------|
| **HTTP boundary** | `models/api.py` Pydantic validators; route parameter annotations | User input validated and returned as a branded type (`SafeSlug`, `SafeStr`, etc.) — not plain `str`. FastAPI invokes `__get_pydantic_core_schema__` on branded types used as path/query/form parameters automatically. |
| **ORM / DB boundary** | `compartment_manager.py` | DB results read via SQLAlchemy Core and deserialized with `_validate_row()` / `_validate_rows()` from `models/api/common.py` (never raw `model_validate`). Branded fields in the response model are validated automatically by Pydantic during deserialization. Raw mapping values passed directly to service functions are wrapped explicitly: `SafeResourceName.of(row["name"], "db:table.col")`. |
| **Service signatures** | `systemd_manager.py`, `quadlet_writer.py`, `user_manager.py`, etc. | Mutating service functions declare branded types (`SafeSlug`, `SafeUnitName`, `SafeSecretName`) in their signatures. Passing a plain `str` is a static type error. |
| **Runtime assertion** | Every `@host.audit`-decorated function | `@sanitized.enforce` (innermost decorator) reads type annotations at decoration time and raises `TypeError` at call time if any branded-type parameter receives a plain `str`. Enforced mechanically — `@host.audit` raises `TypeError` at import time if `@sanitized.enforce` is absent. |
| **DB row sanitization** | Every response model's `_from_db` validator + `_validate_row()` / `_validate_rows()` in `compartment_manager.py` | `_sanitize_db_row()` introspects branded-type fields, resets invalid legacy values to defaults, and persists corrections back to the DB. Prevents app crashes when a field type is tightened. Enforced by a test that bans raw `model_validate(dict(...))` in `services/` and `routers/`. |

See [docs/development.md § Defense-in-depth input sanitization](development.md#defense-in-depth-input-sanitization)
for the full implementation guide, call-site patterns, and the `.trusted()` policy.

### DB row sanitization on read

When a branded type is tightened (e.g. `SafeStr` → `SafeAbsPathOrEmpty`), existing DB rows
may contain values that no longer pass validation.  To prevent startup crashes, every response
model's `_from_db` validator calls `_sanitize_db_row(d, ModelClass)` which introspects the
model's branded-type fields and resets invalid values to their defaults.  The corrected values
are persisted back to the database via `_validate_row()` / `_validate_rows()` in
`compartment_manager.py`, so the error is logged once and doesn't recur.

See [docs/development.md § DB row sanitization](development.md#db-row-sanitization) for the
full implementation guide.

### Why `@validates` (SQLAlchemy) is not used

SQLAlchemy provides a `@validates` decorator for attribute-level validation on ORM model
classes. It is not used here because it only fires on ORM-instance attribute sets
(`CompartmentRow(id=…)`, direct assignment, `session.add()`). All writes in this codebase
go through SQLAlchemy Core statements (`insert(CompartmentRow).values(…)`) which bypass
`@validates` entirely.

| Mechanism | Fires on | quadletman uses |
|-----------|----------|-----------------|
| `@validates` (SQLAlchemy) | ORM instance attribute sets | ✗ — all writes use Core `insert/update` |
| Pydantic `@model_validator` / `@field_validator` | `model_validate(dict(row))` on read | ✓ — all reads go through this |
| `@sanitized.enforce` | Every call to a service function | ✓ — enforced at the service boundary |

Adding `@validates` to `orm.py` would only protect the unused ORM-instance construction
path and would create a false sense of completeness. The real enforcement happens at the
layers above.

## Execution Modes: Root vs Non-Root

quadletman supports two execution modes. The mode is auto-detected at startup
via `os.getuid()` and affects monitoring, privilege escalation, and file I/O
throughout the codebase.

### Root mode (`uid == 0`)

The original and backward-compatible mode. The app runs as `root` (typically via
`sudo .venv/bin/quadletman` or the systemd service with `User=root`).

| Area | Behaviour |
|------|-----------|
| **Monitoring** | Centralized async loops in the main process: `monitor_loop`, `metrics_loop`, `process_monitor_loop`, `connection_monitor_loop`, `image_update_monitor_loop` (`notification_service.py`) |
| **Per-user agents** | Not used — `deploy_agent_service` / `remove_agent_service` are no-ops; `start_agent_api` returns immediately |
| **File I/O** | Direct syscalls: `os.makedirs`, `os.chown`, `os.chmod`, `os.unlink`, `os.rename`, `shutil.rmtree`, `open()` (`host.py`) |
| **Admin commands** | Direct execution — no privilege escalation needed (`host._escalate_cmd` returns the command unchanged) |
| **Credential storage** | Session credentials are stored but the `AdminCredentialMiddleware` does not inject them into request context (not needed when already root) |
| **Unix socket** | Socket `chown`ed to `root:shadow` for PAM access |
| **containers.conf migration** | Runs at startup for all existing compartment users |
| **podman info env** | Uses inherited env directly (HOME/XDG_RUNTIME_DIR already set) |

### Non-root mode (`uid != 0`)

Production-recommended mode. The app runs as a dedicated `quadletman` system user.

| Area | Behaviour |
|------|-----------|
| **Monitoring** | Per-user agents (`quadletman-agent`) run as systemd `--user` services for each `qm-*` user; they report to the main app via a Unix socket API (`agent_api.py`). No centralized polling loops. |
| **Per-user agents** | Deployed as `.service` unit files by `quadlet_writer.deploy_agent_service`; started after compartment creation; restarted via `systemd_manager.ensure_agent_running` |
| **File I/O** | Escalated via `sudo -S` with the authenticated user's password piped to stdin: `host.run(cmd, admin=True)` shells out to `sudo`, `mkdir -p`, `rm -rf`, `ln -sf`, `chmod`, `chown`, `mv`, `tee` (`host.py`) |
| **Admin commands** | Double-sudo: `quadletman` user → authenticated web user's sudo → root. Password obtained from session credential store via `get_admin_credentials()` and piped to `sudo -S` |
| **Credential storage** | `AdminCredentialMiddleware` reads the `qm_session` cookie on every request and injects `(username, password)` into a `ContextVar` so `host.py` can escalate |
| **Unix socket** | Socket permissions set to `0o660`; new `qm-*` users added to app's group for agent API access |
| **containers.conf migration** | Skipped at startup (no sudo available); runs on next compartment create/update |
| **podman info env** | Explicitly sets `HOME` and `XDG_RUNTIME_DIR` from passwd entry for the app user |
| **User creation** | New `qm-*` users are also added to the app process's group (`usermod -aG`) so they can connect to the agent API socket |

### Where the mode check happens

| File | Check | Purpose |
|------|-------|---------|
| `main.py:107` | `os.getuid() == 0` | Start centralized monitoring loops (root) or agent API socket (non-root) |
| `main.py:146` | `os.getuid() != 0` | Skip `containers.conf` migration at startup in non-root mode |
| `main.py:238` | `os.getuid() != 0` | `AdminCredentialMiddleware`: inject session credentials into `ContextVar` only in non-root mode |
| `main.py:67` | `os.getuid() == 0` | Unix socket ownership: `chown` to `root:shadow` when root |
| `host.py:73` | `is_root()` (cached `os.getuid() == 0`) | Gate for all 14 file I/O wrappers + `_escalate_cmd` + `run(admin=True)` |
| `compartment_manager.py:187` | `os.getuid() != 0` | Start per-user agent after compartment creation (non-root only) |
| `user_manager.py:221` | `os.getuid() != 0` | Add new `qm-*` user to app's group for agent socket access (non-root only) |
| `quadlet_writer.py:756,802` | `os.getuid() == 0` | `deploy_agent_service` / `remove_agent_service` — no-op in root mode |
| `systemd_manager.py:617` | `os.getuid() == 0` | `agent_status` — returns `"not-applicable"` in root mode |
| `systemd_manager.py:795` | `os.getuid() == 0` | `ensure_agent_running` — no-op in root mode |
| `agent_api.py:492` | `os.getuid() == 0` | `start_agent_api` — returns `None` in root mode (no socket server) |
| `podman_version.py:141` | `os.getuid() != 0` | Set `HOME`/`XDG_RUNTIME_DIR` from passwd when not root |
| `routers/ui.py:21` | `os.getuid()` | Resolve app username for template globals |

## systemd User Commands

Commands are run as the compartment user via `sudo`. The `quadletman` system user has NOPASSWD sudoers for qm-\* user commands. Root-level operations (user creation, SELinux, sysctl) use the authenticated web user's sudo credentials:

```bash
sudo -u qm-{compartment-id} env XDG_RUNTIME_DIR=/run/user/{uid} \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus \
  systemctl --user ...
```
