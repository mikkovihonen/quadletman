# Changelog

All notable changes to quadletman are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) — see
[docs/ways-of-working.md](docs/ways-of-working.md) for the version number scheme and
release process.

## [0.0.5-alpha] - 2026-03-18

### Added
- Initial version.

## [0.0.6-alpha] - 2026-03-18

### Added
- FEATURE: Web UI over SSH tunnel only.

## [0.1.0-alpha] - 2026-03-20

### Added
- CHANGE: Migrated to SQLAlchemy 2.0 and Alembic.
- IMPROVE: Use branded strings and adopt stricter security checks.
- ADD: Ubuntu smoke tests

## [0.1.1-alpha] - 2026-03-20

### Added
- FIX: Regression fixes: errors on unsanitized values.
- FIX: Regression fixes: form data handling.

## [0.2.0-alpha] - 2026-03-21

### Added
- ADD: Version gating support by version spans.
- FIX: Package distribution

## [0.2.1-alpha] - 2026-03-22

### Added
- ADD: Support for unstable releases in distribution.

## [0.2.2-alpha] - 2026-03-23

### Added
- ADD: Improved internal data model support for Podman version feature gating.

## [0.3.0-alpha] - 2026-03-24 

### Added
- ADD: Non-root quadletman service user.
- ADD: Removed conntrack dependency and replaced it with proc/<pid>/net/tcp monitoring instead.
- ADD: Regex grouping to process monitoring.
- ADD: Podman quadlet datatypes alignment

## [0.3.1-alpha] - 2026-03-25 

### Added
- FIX: release 0.3.0-alpha errors (release pulled)
- ADD: Podman quadlet datatypes alignment

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

[0.0.5-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.0.5-alpha
[0.0.6-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.0.6-alpha
[0.1.0-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.1.0-alpha
[0.1.1-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.1.1-alpha
[0.2.0-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.2.0-alpha
[0.2.1-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.2.1-alpha
[0.2.2-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.2.2-alpha
[0.3.0-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.3.0-alpha
[0.3.1-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.3.1-alpha
[0.4.0-beta]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.4.0-beta