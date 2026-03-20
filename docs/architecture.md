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
| Session management | Custom in-memory store (`quadletman/session.py`) | Cookie-backed sessions with absolute + idle TTL |
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
| Privilege helper | `sudo` | Routes systemd user-instance commands from the root-running app to each compartment's system user |
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

> **Note — `podman quadlet install` path conflict:** When running as root,
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
| **ORM / DB boundary** | `compartment_manager.py` | DB results read via SQLAlchemy Core and deserialized with `Model.model_validate(dict(row))`. Branded fields in the response model are validated automatically by Pydantic during deserialization. Raw mapping values passed directly to service functions are wrapped explicitly: `SafeResourceName.of(row["name"], "db:table.col")`. |
| **Service signatures** | `systemd_manager.py`, `quadlet_writer.py`, `user_manager.py`, etc. | Mutating service functions declare branded types (`SafeSlug`, `SafeUnitName`, `SafeSecretName`) in their signatures. Passing a plain `str` is a static type error. |
| **Runtime assertion** | Every `@host.audit`-decorated function | `@sanitized.enforce` (innermost decorator) reads type annotations at decoration time and raises `TypeError` at call time if any branded-type parameter receives a plain `str`. Enforced mechanically — `@host.audit` raises `TypeError` at import time if `@sanitized.enforce` is absent. |

See [docs/development.md § Defense-in-depth input sanitization](development.md#defense-in-depth-input-sanitization)
for the full implementation guide, call-site patterns, and the `.trusted()` policy.

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

## systemd User Commands

Commands are run as the compartment root via:

```bash
sudo -u qm-{compartment-id} env XDG_RUNTIME_DIR=/run/user/{uid} \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus \
  systemctl --user ...
```
