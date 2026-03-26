# Changelog

All notable changes to quadletman are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) — see
[docs/ways-of-working.md](docs/ways-of-working.md) for the version number scheme and
release process.

## [0.4.2-beta] - 2026-03-27

### Fixed
- Blocking file I/O in volume routes (save, upload, delete, chmod, mkdir, browse)
  now runs in thread pool executors instead of blocking the async event loop
- All `subprocess.run()` calls now have timeouts — `host.run()` defaults to
  `settings.subprocess_timeout`; `secrets_manager` calls get explicit timeouts
- WebSocket PTY cleanup uses terminate → wait(5s) → kill → wait(2s) pattern with
  a configurable session hard limit instead of unbounded `asyncio.wait`
- SSE streaming generators explicitly close the source async generator in a
  `finally` block, preventing orphaned `journalctl`/`podman logs` processes on
  client disconnect
- Background monitoring loops use `_loop_session` context manager guaranteeing
  DB session rollback on exception — prevents "Can't reconnect until invalid
  transaction is rolled back" errors
- DB-filesystem atomicity: `add_*` functions delete the DB row if the subsequent
  unit file write fails (`FileWriteFailed` with `rolled_back=True`); `update_*`
  functions log an error recommending resync (`rolled_back=False`)
- `FileWriteFailed` exception surfaces as a localized error toast in the UI —
  "rolled back" or "resync recommended" depending on operation type
- All `logger.debug` calls logging error conditions changed to `logger.warning`
  (8 in `notification_service.py`, 2 in `metrics.py`)
- `suppress(Exception)` blocks in cleanup paths annotated with `# Best-effort:`
  comments explaining why suppression is safe
- WebSocket PTY error paths use explicit `try/except` with `logger.warning`
  instead of `suppress(Exception)` for diagnostics
- Agent API per-request timeout prevents slow-loris attacks on the Unix socket
- Image update webhook deduplication dict bounded to 10,000 entries
- Agent process/connection upsert failures logged at `warning` level with error
  counting instead of being silently swallowed
- All volume file operation routes now require `Depends(require_compartment)` —
  prevents races with concurrent compartment deletion
- One hardcoded error message in `secrets.py` localized with `_t()`
- Session cookie `max_age` reads from `settings.session_ttl` instead of
  hardcoded `8 * 3600`
- Hardcoded `_VOLUMES_BASE` in `metrics.py` and `agent.py` replaced with
  `settings.volumes_base` / `QUADLETMAN_VOLUMES_BASE` environment variable

### Added
- 23 configurable settings via `QUADLETMAN_*` environment variables — all
  timeouts, intervals, limits, and thresholds are now operator-tunable with
  minimum-value clamping (`_clamp_bounds` model validator)
- Settings bounds validation prevents misconfiguration (e.g., `timeout=0`)
- Environment Variables section in the help page with localized descriptions
  for all timeout/interval settings
- `ServiceCondition` base exception for service-layer conditions that must
  propagate through router catch-all blocks to app-level exception handlers
- `FileWriteFailed(ServiceCondition)` exception with `rolled_back` flag for
  DB-filesystem atomicity feedback
- Per-compartment locking (`_compartment_lock`) for all 29 resource CRUD and
  lifecycle functions — prevents concurrent mutations on the same compartment
- Lock cleanup in `delete_compartment` — prevents memory leak from orphaned
  lock entries
- `_loop_session` async context manager for background loop DB session
  management with guaranteed rollback
- Systemctl status cache bounded to configurable `_MAX_CACHE_SIZE`
- Per-user agent intervals now read from `QUADLETMAN_*` environment variables
  (inherited from the systemd unit) with fallback defaults
- Dev server (`run_dev.sh`) recompiles `.mo` translation files on every start
- `validate_version_spans` moved from model layer to router helpers — keeps
  `models/version_span.py` free from FastAPI dependency
- `require_auth` and `_user_in_allowed_group` moved from `security/auth.py` to
  `routers/helpers/common.py` — `security/auth.py` is now framework-free
- `init_db` extracted to `db/migrate.py` — `db/engine.py` no longer imports
  alembic
- 34 new architecture regression tests in `tests/test_architecture.py` covering
  settings bounds, cache bounds, subprocess timeouts, lock behavior, SSE
  cleanup, volume route protection, agent socket timeout, upsert error
  counting, WebSocket error logging, logging severity, and suppress comments
- Intentionally short `timeout=10`/`15` values annotated with comments
  explaining why they differ from `settings.subprocess_timeout`

### Changed
- Finnish translations: Build → Koonti, rakentaa → koota, rakennettu → koottu
  throughout all UI strings; "Compartment" consistently translated as "Osasto"
- All inline imports in router files moved to top-level (4 files: `volumes.py`,
  `containers.py`, `compartments.py`, `api.py`)
- All unnecessary inline imports in service files moved to top-level (6 files:
  `metrics.py`, `volume_manager.py`, `compartment_manager.py`,
  `user_manager.py`, `quadlet_writer.py`, `agent.py`)
- `notification_service.py` and `agent_api.py` inline imports moved to
  top-level by deferring their import in `main.py` lifespan (breaks circular
  chain)
- Stdlib imports (`json`, `shutil`, `grp`, `ipaddress`, `uuid`) moved from
  function bodies to file tops across 4 files
- Module-level constants moved from mid-file positions to after imports/logger
  in 5 files (`main.py`, `podman_version.py`, `systemd_manager.py`,
  `quadlet_writer.py`, `user_manager.py`)
- CLAUDE.md updated with 13 new development rules preventing architecture
  regressions
- Runbook configuration table expanded with all timeout/interval environment
  variables
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
