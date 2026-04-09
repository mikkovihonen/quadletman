# Upstream Monitoring

This document describes the automated tooling that tracks upstream Podman changes and
gathers community feedback to support product development decisions for quadletman.

---

## Podman Release Monitor

quadletman's feature set is tightly coupled to the Podman version installed on the host.
New Podman releases regularly introduce new Quadlet unit-file keys, new unit types, and
CLI flags that quadletman may need to support. The release monitor automates the detection
of these changes so they don't get missed.

### How it works

A GitHub Actions workflow (`.github/workflows/podman-watch.yml`) runs every Monday at
09:00 UTC. It checks the latest 5 stable Podman releases and, for each release that hasn't
been seen before, runs `scripts/podman_feature_check.py` to generate a report and opens a
GitHub issue labeled `podman-release`.

The script performs three analyses for each release:

1. **Man page diff** — Downloads the Quadlet man page (`podman-systemd.unit.5.md`) for
   both the target release and its predecessor. Parses out all documented Quadlet keys
   (e.g. `AddCapability=`, `AutoUpdate=`) per unit type (`.container`, `.volume`, `.pod`,
   etc.) and reports new and removed keys.

2. **Release notes filter** — Scans the GitHub release body for Quadlet-relevant entries
   using keyword matching (quadlet, .container, .image, unit file, systemd generator, etc.).

3. **Feature coverage cross-reference** — Reads the local `quadletman/podman.py`
   to show which Podman features quadletman already tracks, giving context for triage.

### Issue format

Each generated issue includes:

- **New unit types** — entirely new Quadlet unit types (e.g. `.pod` added in Podman 5.0)
- **Quadlet-relevant release notes** — filtered entries from the upstream release notes
- **New Quadlet keys** — keys added to the man page, grouped by unit type, with checkboxes
- **Removed Quadlet keys** — keys that were dropped between releases
- **Current quadletman feature coverage** — table of existing `PodmanFeatures` flags
- **Action items** — standard checklist for implementing new features

### Deduplication

The workflow searches for existing issues (open or closed) with the `podman-release` label
and the release tag in the title before creating a new one. This means:

- Re-running the workflow is safe — it won't create duplicate issues.
- Closing an issue marks it as reviewed — it won't be reopened.
- The workflow can also be triggered manually via `workflow_dispatch` with a specific tag.

### Running locally

The script requires no external dependencies (Python stdlib only) and can be run outside
CI for ad-hoc checks:

```bash
# Check the latest Podman release against the previous one
python3 scripts/podman_feature_check.py

# Check a specific release
python3 scripts/podman_feature_check.py --tag v5.8.0

# Compare two specific versions
python3 scripts/podman_feature_check.py --tag v5.8.0 --previous v5.7.0

# JSON output (for scripting)
python3 scripts/podman_feature_check.py --output json
```

Set `GITHUB_TOKEN` or `GH_TOKEN` to avoid GitHub API rate limits (60 requests/hour
unauthenticated, 5000 with a token). In CI the workflow's `GITHUB_TOKEN` secret is used
automatically.

### Reviewing issues with Claude Code

When the workflow creates an issue, open a Claude Code conversation and provide the issue
content. Claude Code can:

1. Triage each new key — assess whether it's relevant to quadletman's feature set.
2. Check current coverage — verify which keys are already supported in `quadlet_writer.py`
   templates.
3. Implement — add the feature flag to `PodmanFeatures`, wire the version gate (server-side
   guard + UI disable + test), update templates, and update documentation, all following the
   patterns documented in CLAUDE.md.

### Workflow configuration

| Setting | Value | Where to change |
|---|---|---|
| Schedule | Monday 09:00 UTC | `cron` field in `podman-watch.yml` |
| Releases checked | Latest 5 stable | `per_page` and jq slice in workflow |
| Issue label | `podman-release` | `gh label create` and `gh issue create` in workflow |
| Man page path | `docs/source/markdown/podman-systemd.unit.5.md` | `MANPAGE_PATH` in script |
| Keyword filter | quadlet, .container, .image, .network, .volume, .pod, .build, .kube, unit file, systemd generator | `QUADLET_KEYWORDS` in script |

### Troubleshooting

**Script returns empty report** — The man page may not exist at the expected path for very
old Podman versions. The script degrades gracefully and still reports release notes. Check
stderr output for details.

**Rate limiting** — Without a token, the GitHub API allows 60 requests/hour. The script
makes 3-4 requests per run (releases list + 2 man page fetches + optional tag lookup). For
local batch checks, set `GITHUB_TOKEN`.

**Section headers not matched** — The man page format has evolved across Podman versions.
The parser supports both `[Container]` and `[.container]` styles. If a future version
changes the format, update `SECTION_RE` in the script.

**Key patterns not matched** — The script recognizes four formats for key definitions
(`### \`Key=\``, `**Key=**`, `| **Key=**`, `### **Key=**`). If Podman adopts a new format,
add a pattern to `KEY_PATTERNS`.

