# Changelog

All notable changes to quadletman are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html) — see
[docs/ways-of-working.md](docs/ways-of-working.md) for the version number scheme and
release process.

## [Unreleased]

## [0.1.0] - 2026-03-17

### Added
- Compartment lifecycle management (create, delete, start, stop, restart)
- Container, pod, image-unit, and network CRUD via Quadlet unit files
- Volume management with SELinux `container_file_t` context and file browser
- Secret management via `podman secret` store
- Timer (scheduled task) support with last-run / next-run status
- Service template library (save, clone, delete)
- Log streaming (SSE), journal view, and in-browser WebSocket terminal
- Host settings: sysctl knobs and SELinux booleans
- Registry credential management per compartment
- Connection and process monitoring per compartment
- Per-compartment CPU / memory / disk metrics with history graphs
- Webhook notification hooks (on_start, on_stop, on_failure, on_restart)
- PAM-based HTTP Basic Auth restricted to sudo/wheel users
- CSRF protection, security headers, and session management
- Finnish (fi) localisation
- Podman version gating for features requiring specific Podman releases
- Multi-unit `.quadlets` bundle import/export (Podman 5.8+)

[Unreleased]: https://github.com/mikkovihonen/quadletman/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.1.0
