# Changelog

All notable changes to quadletman are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) — see
[docs/ways-of-working.md](docs/ways-of-working.md) for the version number scheme and
release process.

## [0.5.5-beta] - 2026-04-13

### Fixed
- RPM package upgrade failed when pip tried to install both the old and new
  quadletman wheels simultaneously — during RPM upgrade `%post` runs before old
  package files are removed, so the `quadletman-*.whl` glob matched two wheels;
  fixed by using a version-specific glob (`quadletman-%{pkg_version}-*.whl`)

## [0.5.4-beta] - 2026-04-13

### Fixed
- Container fields added in Podman 4.4.0 through 5.7.0 (43 fields) were silently
  discarded on create and update — the UI accepted values and validation passed, but
  `add_container` and `update_container` never included them in the DB insert/update
  statements; affected fields include `expose_host_port`, `annotation`, `tmpfs`, `mount`,
  `global_args`, `group_add`, `add_host`, `timezone`, `memory`, `pull`, `pids_limit`,
  `shm_size`, `ulimits`, `stop_timeout`, `stop_signal`, `container_name`, `run_init`,
  `reload_cmd`, `reload_signal`, `http_proxy`, all SELinux label fields, all startup
  health check fields, and more
- Volume fields added in Podman 4.4.0 through 5.3.0 (12 fields) were similarly
  discarded on create — `add_volume` was missing `gid`, `uid`, `user`, `image`, `type`,
  `label`, `volume_name`, `containers_conf_module`, `global_args`, `podman_args`, and
  `service_name`

## [0.5.3-beta] - 2026-04-12

### Fixed
- RPM package installation failed with "The package is not signed" — `publish-repo.sh`
  only signed repository metadata (`repomd.xml`) but not the individual `.rpm` files;
  DNF's `gpgcheck=1` verifies per-package signatures, not repo metadata
- Added `rpm --addsign` step to `publish-repo.sh` so each `.rpm` carries an embedded
  GPG signature before the repository is built
- Added `repo_gpgcheck=1` to the RPM repo configuration for defense-in-depth verification
  of both individual packages and repository metadata
- Environment file `/etc/quadletman/quadletman.env` documented in the runbook was never
  loaded — the systemd unit was missing the `EnvironmentFile=` directive

### Added
- Default `/etc/quadletman/quadletman.env` with commented-out defaults is now shipped
  in both RPM and DEB packages; marked as a config file so user edits survive upgrades

## [0.5.2-beta] - 2026-03-31

### Added
- Light theme with full semantic CSS theming support
- Per-user theme preference (Dark / Light / System) persisted in database,
  selectable from the session modal; System mode follows the OS preference
  via `prefers-color-scheme` media query
- `QUADLETMAN_PODMAN_VERSION_OVERRIDE` setting to simulate a different Podman
  version for UI testing (`--podman-version=X.Y.Z` in `run_dev.sh`)
- Jinja2 macros: `section_card` (card with header + list + empty state),
  `delete_btn` (hx-delete confirmation button), `empty_state` (placeholder)

### Changed
- All visual styling migrated from inline Tailwind utilities to semantic
  `qm-*` CSS classes in `app.css` — templates, macros, and JS files no longer
  contain raw color, font, or typography Tailwind tokens; the entire UI is
  now themeable by editing a single CSS file
- Modal structure unified: all modals follow the same header / scrollable
  content / fixed footer pattern with `qm-modal-body`, `qm-modal-scroll`,
  `qm-modal-footer` classes; `display: contents` wrapper (`qm-modal-data`)
  keeps Alpine scope without breaking flex layout
- `app.css` reorganized into 11 semantic sections (design tokens, page shell,
  modals, cards, buttons, forms, badges, tabs, tables, metrics, domain
  components); duplicate and near-duplicate classes consolidated
- Container and volume form routes no longer shell out to `podman info` for
  driver lists — uses startup-cached globals instead, reducing form load time
  by 0.5-3 seconds
- `run_dev.sh` now compiles Tailwind CSS before syncing and detects/stops
  existing dev instances on startup
- JS files (`polling.js`, `logs.js`, `modals.js`, `app.js`, `navigation.js`)
  migrated from raw Tailwind to semantic classes for full light-theme support
- Pod section in compartment detail opens modal directly from card header
  button instead of requiring a two-step disclosure form flow

### Fixed
- Modal footer buttons scrolled away with content on image unit, network,
  volume, and artifact forms — footer now stays fixed at the bottom
- `pair_list` macro called with unsupported `cn` keyword argument in pod form
- `qm-metrics-grid-4` class accidentally dropped during CSS consolidation,
  causing compartment metrics to stack vertically

## [0.5.1-beta] - 2026-03-28

### Fixed
- Fedora 43: PAM authentication failed due to broken `pam_lastlog2.so` module
  in the default `login` PAM stack — added a dedicated `/etc/pam.d/quadletman`
  service config that only loads `pam_unix.so`
- Fedora: `shadow` group does not exist by default, preventing the `quadletman`
  user from reading `/etc/shadow` for PAM authentication — the RPM `%pre` and
  DEB `postinst` scripts now create the group if missing
- Use absolute paths (`/usr/bin/env`, `/usr/bin/systemctl`, `/usr/bin/podman`,
  `/usr/bin/cat`, etc.) in all sudo commands — bare names resolved to
  `/usr/sbin/` on Fedora 43 via sudo's `secure_path`, breaking sudoers matching
- `~/.config` directory created with root ownership when setting up compartment
  users — podman and systemd refuse to use a `.config` not owned by the user;
  now creates each intermediate directory with correct ownership
- `quadletman-agent` not found when running from a venv — now resolves the
  binary from the same directory as the running Python interpreter
- Sudoers file missing entries for read helpers (`cat`, `test`, `ls`, `stat`,
  `head`, `readlink`) and interactive terminal (`/bin/bash`) — volume browser
  and host shell were broken in non-root mode
- Sudoers `(qm-*)` RunAs wildcard not supported by sudo 1.9.17 on Fedora 43 —
  replaced with `(%quadletman)` group-based matching
- `host.chown()` passed `-1` (no-change sentinel) to shell `chown` in non-root
  mode — now resolves to current owner/group via `os.stat`
- `admin=True` stdin conflict: when a command both pipes content (secret create,
  volume import, registry login) and needs sudo password, the password was
  dropped — now prepends password line before caller's input
- `DefaultDependencies` placed in Quadlet `[Container]` section — Quadlet
  rejects unknown keys; moved to `[Unit]` section where systemd expects it

### Changed
- All compartment commands (systemctl, podman, secrets, metrics) now route
  through the authenticated user's sudo (`admin=True`) instead of NOPASSWD
  sudoers entries; sudoers reduced to only PTY terminals, streaming
  subprocesses, and read-only file access that cannot pipe a password
- All `run_in_executor(None, ...)` calls in routers replaced with
  `run_blocking()` which propagates ContextVars — required for `admin=True`
  credential access in thread pool workers
- Use absolute paths (`/usr/bin/systemctl`, `/usr/bin/podman`, `/usr/bin/cat`,
  etc.) in all subprocess commands for consistent sudoers matching on Fedora
- Dev sudoers (`scripts/sudoers.d/qm-dev`) mirrors production
- Removed unused `host.run_as_user()` (replaced by `admin=True` path)
- Removed `podman quadlet install/rm` CLI path — incompatible with `admin=True`
  escalation model; unit files now always written directly via `host.write_text`

## [0.5.0-beta] - 2026-03-28

### Changed
- Packages now ship a Python wheel and build the virtualenv at install time —
  C extension dependencies (pydantic-core, psutil) are compiled against the
  target system's Python version, fixing "No module named" errors when the
  installed Python differs from the build host (e.g. Fedora 43 with Python 3.14)
- RPM is now `BuildArch: noarch`; DEB is now `Architecture: all` — single
  package works on any CPU architecture
- RPM and DEB build pipelines simplified to one build per format (no per-arch
  matrix)

### Fixed
- Fedora 43: `SupplementaryGroups=shadow systemd-journal` in the systemd unit
  caused "Failed to determine supplementary groups: No such process" when the
  `shadow` group does not exist — removed the directive; groups are already
  assigned via `usermod` in post-install scripts and picked up by systemd
  automatically

## [0.4.4-beta] - 2026-03-27

### Added
- Per-username login rate limiting (half the per-IP budget) to block distributed
  credential-stuffing against a single account
- WebSocket connection limiter — max concurrent terminals per client IP
  (`QUADLETMAN_WS_MAX_CONNECTIONS_PER_IP`, default 10)
- WebSocket message size cap (`QUADLETMAN_WS_MAX_MESSAGE_BYTES`, default 64 KiB)
- Periodic session re-validation on open WebSocket terminals
  (`QUADLETMAN_WS_SESSION_RECHECK_INTERVAL`, default 60 s)
- Terminal open/close audit logging with client IP
- `SECURITY.md` with vulnerability reporting instructions

### Fixed
- CSRF timing leak: `secrets.compare_digest` now always runs even when tokens
  are empty, preventing response-time side-channel
- Session cookie missing `path=/` — cookie now scoped to the entire application
- Session timestamps use `time.monotonic()` instead of `time.time()` — immune
  to system clock adjustments
- Fernet encryption keys stored in a separate dict from session data — a memory
  dump of `_sessions` no longer reveals both key and ciphertext
- `QUADLETMAN_TEST_AUTH_USER` blocked when `QUADLETMAN_SECURE_COOKIES=true` to
  prevent auth bypass in production-like environments

## [0.4.3-beta] - 2026-03-27

### Added
- Config file upload UI for Quadlet path fields: environment files, seccomp
  profiles, containers.conf modules, auth files, decryption keys, ignore files
- Content validation on upload: JSON+key checks for seccomp/auth, TOML for
  containers.conf
- `host.py` read helpers (`path_isdir`, `path_isfile`, `listdir`, `stat_entry`,
  `read_bytes`, `write_bytes`) for non-root privilege escalation

### Fixed
- Non-root mode: all filesystem operations on qm-* paths use `host.*` wrappers
  — volume browser, config file upload/preview, envfile management, unit masking,
  and quadlet CLI install all work without root
- `run_blocking()` propagates ContextVars to executor threads — fixes "admin
  credentials required" errors in non-root mode

### Changed
- `QUADLETMAN_MAX_ENVFILE_BYTES` → `QUADLETMAN_MAX_CONFIG_FILE_BYTES` (old name
  still accepted)
- Envfile routes deprecated in favour of generic configfile routes

## [0.4.2-beta] - 2026-03-27

### Added
- 23 configurable `QUADLETMAN_*` environment variables for all timeouts,
  intervals, and limits with minimum-value clamping
- Per-compartment locking for all 29 CRUD and lifecycle functions
- `ServiceCondition` / `FileWriteFailed` exceptions for DB-filesystem atomicity
  feedback (rolled-back or resync-recommended toasts)
- `_loop_session` context manager for background loop DB sessions with
  guaranteed rollback
- 34 architecture regression tests

### Fixed
- Blocking file I/O in volume routes moved to thread pool executors
- All `subprocess.run()` calls now have timeouts
- WebSocket PTY cleanup: terminate → wait → kill → wait pattern with hard limit
- SSE generators close source in `finally` — no orphaned subprocesses
- DB session rollback in background monitoring loops
- DB-filesystem atomicity: rollback DB inserts on unit file write failure
- Error-level log calls corrected from `debug` to `warning`
- Agent API per-request timeout; webhook dedup dict bounded to 10k entries
- Volume routes require `Depends(require_compartment)`

### Changed
- Finnish: Build → Koonti; inline imports moved to top-level across 12 files
- Changelog reordered newest-first

## [0.4.1-beta] - 2026-03-26

### Fixed
- Race condition: `GET /api/compartments/{id}/disk-usage` returned 500 when a
  compartment was deleted while the request was in flight
- Race conditions in background monitoring loops (`metrics_loop`,
  `process_monitor_loop`, `connection_monitor_loop`, `image_update_monitor_loop`,
  `_check_once`) — now skip compartments whose Linux user has been deleted
- Race condition in `get_metrics` and `get_metrics_disk` routes when a compartment
  user is deleted between the UID check and the metrics call
- TOCTOU in volume file operations (`volume_get_file`, `volume_delete_entry`,
  `volume_chmod`) — replaced check-then-act with try/except
- `metrics.py` functions (`get_disk_breakdown`, `get_container_ips`,
  `_get_container_pids`) now return safe empty defaults when the compartment user
  is missing instead of crashing with `KeyError`

### Added
- Per-compartment locking for all resource CRUD operations (containers, volumes,
  networks, pods, images, artifacts, builds, timers, secrets) and lifecycle
  operations (start, stop, enable, disable, resync) — prevents concurrent
  mutations on the same compartment
- Lock acquisition timeout (30 s) with `CompartmentBusy` exception → HTTP 409
  error toast in the UI
- `ServiceCondition` base exception class — service-layer exceptions that must
  propagate through router catch-all blocks to app-level handlers
- `require_compartment` dependency now verifies the Linux user exists (not just
  the DB record), protecting all routes that use it
- User-existence guards on WebSocket routes (`container_terminal`,
  `compartment_shell`)
- Dev server (`run_dev.sh`) now recompiles `.mo` translation files on every start

### Changed
- Finnish translations: Build → Koonti (noun), rakentaa → koota (verb),
  rakennettu → koottu (past participle) throughout all UI strings

## [0.4.0-beta] - 2026-03-26

First beta release. All features listed below have been available since the alpha
series and are now considered stable enough for testing in non-production environments.

### Compartments and isolation
- Compartment-based container management — each compartment gets a dedicated Linux
  system user (`qm-{id}`) for OS-level process isolation
- `loginctl linger` enabled per compartment so systemd --user units persist after
  logout and survive reboots
- Service templates — snapshot a compartment's full configuration and clone it into
  new compartments

### Container configuration
- Form-based UI for defining containers, pods, images, and networks; quadletman
  writes the Quadlet unit files
- Full Quadlet key coverage — every container field from the Podman Quadlet spec is
  exposed, including SELinux labels, health probes, reload commands, pull retries,
  user namespace mappings, and resource weights
- Pod editing — multi-tab modal form for pods with ports, volumes, DNS, networking,
  user namespace mappings, and advanced settings
- Image unit editing — modal form for image units with registry auth, platform
  targeting, tags, retry settings, and advanced options
- OCI artifact units for OCI artifact distribution (Podman 5.7+)
- Named networks with driver, subnet, gateway, IPv6, and DNS settings
- Network mode selection — host, none, slirp4netns, pasta, or named network per
  container with network aliases
- Build from Containerfile — use a local Containerfile/Dockerfile instead of a
  registry image (Podman 4.5+)
- AppArmor profile per container (Podman 5.8+)
- Host device passthrough (GPUs, serial ports, etc.)
- OCI runtime selection (crun, runc, kata, custom)
- Init process support (tini as PID 1)
- Log rotation configuration for json-file and k8s-file drivers
- Extra `[Service]` directives for advanced systemd configuration

### Volumes, secrets, and credentials
- Managed volumes at `/var/lib/quadletman/volumes/` with automatic SELinux
  `container_file_t` context
- In-browser volume file browser with archive/restore and chmod support
- Helper users for UID mapping — non-root container UIDs map to dedicated host users
- Podman secrets management — create, list, and delete secrets per compartment
- Per-compartment registry login credential storage

### Scheduling and automation
- Scheduled timers — systemd `.timer` units with `OnCalendar=` or `OnBootSec=`
- Timer last-run and next-run status display
- Notification webhooks for `on_start`, `on_stop`, `on_failure`, `on_restart`,
  `on_unexpected_process`, `on_unexpected_connection`, and `on_image_update` events
  with exponential backoff retry

### Operations and monitoring
- Live log streaming via SSE
- WebSocket terminal into running containers
- Image management — list, prune dangling, and re-pull images per compartment
- CPU/memory/disk metrics history sampled every 5 minutes
- Per-container restart and failure analytics with timestamps
- Process monitor — records every process under a compartment's Linux user; unknown
  processes trigger webhooks; regex pattern matching auto-marks known processes
- Connection monitor — records connections by reading `/proc/<pid>/net/tcp` from each
  container's network namespace; classifies direction via LISTEN port matching;
  supports pasta and slirp4netns rootless networking
- Host kernel settings (sysctl) management from the UI with persistent configuration
- SELinux boolean management for Podman-relevant booleans
- Database backup download via API

### Import / export
- Export compartments as portable `.quadlets` bundle files (Podman 5.8+)
- Import `.quadlets` bundle files to recreate compartments

### Authentication and security
- PAM-based HTTP Basic Auth — login with existing Linux OS credentials
- Access restricted to `sudo`/`wheel` group members
- Kernel keyring credential isolation (via `libkeyutils`) with Fernet-encrypted
  in-memory fallback
- CSRF protection (double-submit cookie), HTTPOnly/SameSite=Strict session cookies,
  and security response headers on every request
- Defense-in-depth input sanitization with branded string types
- Path traversal protection via `resolve_safe_path()` and `O_NOFOLLOW` file writes

### Podman version support
- Version-gated UI — fields and features are shown or hidden based on detected Podman
  version using `VersionSpan` annotations
- Supports Podman 4.4+ through 5.8+; newer features degrade gracefully on older versions

### Privilege model
- Runs as a dedicated `quadletman` system user (backward compatible with root)
- Admin operations escalate via the authenticated user's sudo credentials
- Per-user monitoring agents for rootless read-only operations

### Localization
- Finnish (fi) translation
- Gettext-based i18n framework ready for additional languages

## [0.3.1-alpha] - 2026-03-25

### Added
- FIX: release 0.3.0-alpha errors (release pulled)
- ADD: Podman quadlet datatypes alignment

## [0.3.0-alpha] - 2026-03-24

### Added
- ADD: Non-root quadletman service user.
- ADD: Removed conntrack dependency and replaced it with proc/<pid>/net/tcp monitoring instead.
- ADD: Regex grouping to process monitoring.
- ADD: Podman quadlet datatypes alignment

## [0.2.2-alpha] - 2026-03-23

### Added
- ADD: Improved internal data model support for Podman version feature gating.

## [0.2.1-alpha] - 2026-03-22

### Added
- ADD: Support for unstable releases in distribution.

## [0.2.0-alpha] - 2026-03-21

### Added
- ADD: Version gating support by version spans.
- FIX: Package distribution

## [0.1.1-alpha] - 2026-03-20

### Added
- FIX: Regression fixes: errors on unsanitized values.
- FIX: Regression fixes: form data handling.

## [0.1.0-alpha] - 2026-03-20

### Added
- CHANGE: Migrated to SQLAlchemy 2.0 and Alembic.
- IMPROVE: Use branded strings and adopt stricter security checks.
- ADD: Ubuntu smoke tests

## [0.0.6-alpha] - 2026-03-18

### Added
- FEATURE: Web UI over SSH tunnel only.

## [0.0.5-alpha] - 2026-03-18

### Added
- Initial version.

[0.5.1-beta]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.5.1-beta
[0.5.0-beta]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.5.0-beta
[0.4.4-beta]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.4.4-beta
[0.4.3-beta]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.4.3-beta
[0.4.2-beta]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.4.2-beta
[0.4.1-beta]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.4.1-beta
[0.4.0-beta]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.4.0-beta
[0.3.1-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.3.1-alpha
[0.3.0-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.3.0-alpha
[0.2.2-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.2.2-alpha
[0.2.1-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.2.1-alpha
[0.2.0-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.2.0-alpha
[0.1.1-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.1.1-alpha
[0.1.0-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.1.0-alpha
[0.0.6-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.0.6-alpha
[0.0.5-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.0.5-alpha
