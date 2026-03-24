# Features

quadletman is a browser-based admin UI for running Podman containers on a headless Linux
server. Instead of talking to the Podman socket at runtime, it generates and manages
**Quadlet unit files** — the systemd-native way to declare containers as persistent
services. Each group of containers lives in a **compartment**: an isolated environment
backed by a dedicated Linux system user, its own volume storage, and its own Podman secret
and registry-credential store.

You point a browser at the server, log in with your existing OS credentials, and get a
full lifecycle UI: create compartments, define containers and pods, manage volumes and
secrets, schedule timers, watch live logs, and monitor resource usage — all without
touching the command line.

## Compartments and isolation

- Each **compartment** is a named group of containers that run together as a unit
- Every compartment gets a dedicated Linux system user (`qm-{id}`) so container processes
  are isolated from each other at the OS level
- `loginctl linger` is enabled per compartment so user-level systemd units persist after
  logout and survive reboots
- **Service templates** — snapshot a compartment's full configuration as a reusable
  template; clone it into a new compartment with one action

## Container configuration

- Define containers, pods, images, and networks via form-based UI; quadletman writes the
  Quadlet unit files
- **Build from Containerfile** — use a local Containerfile/Dockerfile instead of a
  registry image (Podman 4.5+)
- **AppArmor profile** per container (Podman 5.8+)
- **Host device passthrough** — pass GPUs, serial ports, and other devices via `AddDevice=`
- **Named networks** — define multiple Podman networks per compartment with driver, subnet,
  gateway, IPv6, and DNS settings; containers select which network to join
- **Network mode** — choose host, none, slirp4netns, pasta, or a named network per
  container; add network aliases
- **OCI runtime selection** — specify crun, runc, kata, or any custom runtime per container
- **Init process** — run tini as PID 1 for correct signal handling and zombie reaping
- **Resource weights** — set `CPUWeight=`, `IOWeight=`, and `MemoryLow=` per container
- **Log rotation** — configure max log size and file count for json-file and k8s-file drivers
- **Extra [Service] directives** — inject raw systemd `[Service]` entries for advanced cases
- **Full Quadlet key coverage** — every container field from the Podman Quadlet spec is
  exposed in the form UI, including SELinux labels, startup health probes, reload commands,
  pull retries, user namespace mappings, and infrastructure settings
- **Pod editing** — full multi-tab modal form for creating and editing pods with ports,
  volumes, DNS, networking, user namespace mappings, and advanced settings
- **Image unit editing** — full modal form for creating and editing image units with
  registry auth, platform targeting, tags, retry settings, and advanced options
- **OCI artifact units** — manage `.artifact` Quadlet units for OCI artifact distribution
  (Podman 5.7+); create, edit, and delete with image reference and content digest

## Volumes, secrets, and credentials

- Volumes stored at `/var/lib/quadletman/volumes/{compartment-id}/{volume-name}/` with
  SELinux `container_file_t` context applied automatically
- **Helper users** for UID mapping — non-root container UIDs map to dedicated host users
  for correct volume ownership
- **Secrets management** — create Podman secrets per compartment; inject them into
  containers via `Secret=` in unit files
- **Registry login** — store per-compartment Docker/OCI registry credentials persistently
  in the compartment root's auth file

## Scheduling and automation

- **Scheduled timers** — create systemd `.timer` units that run a container on a calendar
  schedule (`OnCalendar=`) or after boot (`OnBootSec=`)
- **Timer last-run status** — see last trigger time and next scheduled run for each timer
- **Notification webhooks** — register HTTP callbacks for `on_start`, `on_stop`,
  `on_failure`, `on_restart`, `on_unexpected_process`, and `on_unexpected_connection`
  events; delivery retried with exponential backoff

## Operations and monitoring

- **Live log streaming** — tail container journals in the browser via SSE
- **WebSocket terminal** — interactive shell into running containers
- **Image management** — list, prune dangling, and re-pull images per compartment
- **Metrics history** — CPU/memory/disk snapshots sampled every 5 minutes; queryable via API
- **Restart analytics** — per-container restart and failure counts with timestamps
- **Process monitor** — records every unique process observed under a compartment's Linux
  user; unknown processes trigger `on_unexpected_process` webhooks; each process can be
  marked known to suppress future alerts. Supports **pattern matching** — marking a process
  as known creates a regex pattern (initially exact match) that can be edited inline to
  replace literal segments with character classes. New processes matching existing patterns
  are auto-marked known without triggering webhooks. Overlapping patterns are rejected
- **Connection monitor** — records every unique connection `(container, proto,
  dst_ip, dst_port, direction)` by reading `/proc/<pid>/net/tcp` from each container's
  network namespace; classifies direction via LISTEN port matching (inbound = local port
  is a listening port); unknown connections trigger `on_unexpected_connection` webhooks;
  each connection can be marked known to suppress future alerts. Works with both pasta
  and slirp4netns rootless networking. On slirp4netns, inbound connections are short-lived
  and appear as TIME_WAIT — set `QUADLETMAN_CAPTURE_TIME_WAIT=true` to capture them.
  See [Platform notes](development.md#platform-notes) for details.
- **Host kernel settings** — view and apply sysctl settings (port range, IP forwarding,
  user namespaces, inotify limits) from the top bar; changes persist via
  `/etc/sysctl.d/99-quadletman.conf`
- **SELinux boolean management** — toggle SELinux booleans relevant to Podman from the UI
- **Database backup** — download a consistent hot backup of the SQLite DB via the API

## Import / export

- **Export** any compartment as a portable `.quadlets` bundle file (Podman 5.8+)
- **Import** `.quadlets` bundle files to recreate compartments from saved configurations

## Authentication and security

- Login uses the host's **Linux PAM** stack — no separate password database
- Only users in the `sudo` or `wheel` group (configurable) are permitted
- CSRF protection, HTTPOnly session cookies, and security response headers on every request
