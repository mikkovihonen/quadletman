# Open Source Governance

This document describes how quadletman aligns with the upstream Podman project and how
it tracks, models, and implements support for new Podman features.

---

## Upstream alignment

quadletman aims to follow the Podman project as closely as possible, supporting new
features as they are released.  This is achieved through three mechanisms:

1. **Automated release monitoring** via GitHub workflows
2. **Internal data modelling** of Podman version support via the VersionSpan system
3. **Conditional code branches** that adapt behaviour to the detected Podman version

### Monitoring Podman releases

A GitHub Actions workflow (`.github/workflows/podman-watch.yml`) runs weekly and checks
the latest Podman releases.  For each new release it runs
`scripts/podman_feature_check.py`, which:

- Diffs the Quadlet man page between the new and previous releases to detect added or
  removed unit-file keys.
- Scans the release notes for Quadlet-relevant entries using keyword matching.
- Cross-references the findings against the current `PodmanFeatures` flags in the
  codebase to highlight coverage gaps.

The script opens a GitHub issue labelled `podman-release` with the full report.
Developers then triage the issue and implement support for the new features.  See
[Product Development](product_development.md) for the detailed workflow.

### Modelling version support (VersionSpan)

Every Podman-version-sensitive property in the data model carries a `VersionSpan`
annotation that records when it was introduced, deprecated, and removed:

```python
from typing import Annotated
from quadletman.models.version_span import VersionSpan

apparmor_profile: Annotated[SafeStr, VersionSpan(
    introduced=(5, 8, 0),
    quadlet_key="AppArmor",
)] = SafeStr.trusted("", "default")
```

`VersionSpan` metadata drives three layers of version awareness automatically:

| Layer | Mechanism | Effect |
|---|---|---|
| **Route validation** | `validate_version_spans()` in HTTP handlers | Rejects requests that set unsupported fields (HTTP 400) |
| **Quadlet file generation** | `field_availability()` dicts passed to Jinja2 templates | Omits unsupported keys from generated unit files |
| **UI form gating** | Pre-computed availability globals in templates | Disables form inputs for features unavailable on the host |

For features that are not tied to a single model field (e.g. entire unit-file types like
`.pod` or `.build`), feature-level `VersionSpan` constants serve the same purpose:

```python
POD_UNITS = VersionSpan(introduced=(5, 0, 0))
BUILD_UNITS = VersionSpan(introduced=(5, 2, 0))
QUADLET_CLI = VersionSpan(introduced=(5, 6, 0))
```

These constants are pre-evaluated into boolean flags on the `PodmanFeatures` dataclass
at startup and used in route guards and conditional code paths.

### Conditional code branches

Where the same operation can be performed differently depending on Podman version,
quadletman uses version-gated branching rather than requiring a single minimum version.
For example, the Quadlet unit-file writer supports two backends:

- **Podman < 5.6.0** — direct file I/O (`host.write_text()` + `systemctl daemon-reload`)
- **Podman >= 5.6.0** — `podman quadlet install` CLI (official API)

The version gate is transparent to callers:

```python
def _persist_unit(service_id, filename, content):
    if get_features().quadlet_cli:
        _install_via_cli(service_id, filename, content)
    else:
        _write_to_disk(service_id, filename, content)
```

This pattern ensures quadletman works on the widest range of Podman versions while
automatically using the best available mechanism.

---

## Feature lifecycle

When a new Podman release is detected by the monitoring workflow, the typical response
follows this sequence:

1. **Triage** — review the `podman-release` issue; identify new Quadlet keys, new unit
   types, new CLI commands, and deprecations.
2. **Model** — add `VersionSpan` annotations to new or existing model fields; add
   feature-level constants for new unit types or CLI capabilities.
3. **Schema** — add DB columns (Alembic migration) and ORM definitions for new fields.
4. **Templates** — update Quadlet Jinja2 templates to render new keys with version gates.
5. **Routes** — add route-level validation (`validate_version_spans()`) and feature guards.
6. **Tests** — add version boundary tests and extraction count tests.
7. **Documentation** — update CLAUDE.md, Key Files table, and feature docs.

---

## Supported Podman versions

quadletman supports Podman from version **4.1.0** (pasta network driver) onward.  Core
Quadlet functionality requires **4.4.0**.  The following table shows when major
capabilities became available:

| Version | Capability |
|---|---|
| 4.1.0 | Pasta network driver |
| 4.4.0 | Quadlet (.container, .volume, .network, .kube) |
| 4.8.0 | .image unit files |
| 5.0.0 | .pod unit files |
| 5.2.0 | .build unit files |
| 5.6.0 | `podman quadlet` CLI (install, list, rm, print) |
| 5.7.0 | .artifact unit files |
| 5.8.0 | .quadlets bundle format |

The full per-field version mapping is maintained in `models/version_span.py` and
`models/api/__init__.py` via `VersionSpan` annotations.
