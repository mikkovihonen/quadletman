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


[0.0.5-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.0.5-alpha
[0.0.6-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.0.6-alpha
[0.1.0-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.1.0-alpha
[0.1.1-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.1.1-alpha
[0.2.0-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.2.0-alpha
[0.2.1-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.2.1-alpha
[0.2.2-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.2.2-alpha
[0.3.0-alpha]: https://github.com/mikkovihonen/quadletman/releases/tag/v0.3.0-alpha