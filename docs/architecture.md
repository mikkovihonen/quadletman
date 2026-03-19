# Architecture

This document describes the internal architecture of quadletman — how compartments map to
Linux users, how Quadlet unit files are generated, and how volumes and registry credentials
are managed.

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

quadletman enforces a three-layer input sanitization contract using **branded string types**
defined in `quadletman/sanitized.py`. This prevents user-supplied strings from reaching
host-mutating operations without proven validation.

The layers correspond to the application tiers:

| Layer | Where | What happens |
|-------|--------|--------------|
| HTTP boundary | `models.py` Pydantic validators | User input is validated and returned as a branded type (`SafeSlug`, `SafeSecretName`, etc.) — not plain `str` |
| Service signatures | `user_manager.py`, `systemd_manager.py`, etc. | Mutating functions declare branded types in their parameters, making the upstream obligation explicit |
| Runtime assertion | First statement of each mutating function | `sanitized.require(param, Type)` raises `TypeError` if a raw `str` is passed, catching bypasses at runtime |

Holding a `SafeSlug` is proof the slug pattern was validated; holding a `SafeSecretName` is
proof the secret-name pattern was validated. The type itself carries the proof — no re-checking
at the call site is needed.

The orchestration layer (`compartment_manager.py`) bridges between FastAPI path parameters
(plain `str`) and service functions using `.trusted()` for DB-sourced and internally
constructed values, and the Pydantic models carry the branded type through automatically for
HTTP-sourced values.

See [docs/development.md § Defense-in-depth input sanitization](development.md#defense-in-depth-input-sanitization)
for the full implementation guide and patterns.

## systemd User Commands

Commands are run as the compartment root via:

```bash
sudo -u qm-{compartment-id} env XDG_RUNTIME_DIR=/run/user/{uid} \
  DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/{uid}/bus \
  systemctl --user ...
```
