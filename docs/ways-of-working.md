# Ways of Working

This document describes the branch strategy, pull request process, CI pipeline, and
release procedure for quadletman. It applies to all contributors — human and AI alike.

---

## Branch Strategy

The repository uses a **trunk-based development** model with short-lived feature branches.

| Branch | Purpose |
|---|---|
| `main` | Stable, always-green trunk. Every commit on `main` is releasable. |
| `feature/<slug>` | New functionality. Branched from and merged back to `main`. |
| `fix/<slug>` | Bug fixes. Same lifetime as feature branches. |
| `chore/<slug>` | Dependency updates, docs, CI, refactoring with no behaviour change. |

Rules:
- **No direct pushes to `main`** — all changes land via pull requests.
- Branch names must be lower-kebab-case: `feature/connection-monitor`, `fix/timer-next-run`.
- Delete the branch after the PR is merged.

---

## Pull Request Process

### Opening a PR

1. Branch off `main`:
   ```bash
   git checkout main && git pull
   git checkout -b feature/my-feature
   ```
2. Work in small, logical commits. Commit messages follow the project convention:
   ```
   ADD short description of what was added
   FIX short description of what was fixed
   CHORE short description of the housekeeping change
   ```
3. Before pushing, run the pre-commit suite locally:
   ```bash
   uv run pre-commit run --all-files
   ```
4. Push and open a PR targeting `main`. The PR title becomes the squash-commit message
   on `main`, so it must follow the same `ADD / FIX / CHORE` prefix convention.

### Review checklist (reviewer)

- [ ] CI is green (all jobs pass)
- [ ] CLAUDE.md Doc Update Protocol has been followed for any trigger that fired
- [ ] Security Review Checklist in CLAUDE.md has been run for security-relevant files
- [ ] New user-visible strings have accompanying `.po` / `.mo` updates
- [ ] Tailwind changes are compiled and committed

### Merging

- Use **squash merge** — one commit per PR on `main`.
- The squash commit message = the PR title.
- Delete the source branch after merge.

---

## CI Pipeline

Every push to `main` and every pull request runs the following GitHub Actions jobs
(defined in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)):

| Job | What it checks |
|---|---|
| **lint** | `ruff check` + `ruff format --check` |
| **test-python** | `pytest --cov` (unit + router tests; E2E excluded) + Codecov upload |
| **test-js** | `npm test` (Vitest) |
| **tailwind-check** | Rebuilds Tailwind; fails if `tailwind.css` would change |
| **babel-check** | Recompiles `.mo` files; fails if they would change |

All jobs must be green before a PR can be merged. The branch protection rule on `main`
enforces this.

---

## Semantic Versioning

quadletman follows [Semantic Versioning 2.0.0](https://semver.org/):

```
MAJOR.MINOR.PATCH
```

| Segment | Increment when… |
|---|---|
| **MAJOR** | A backwards-incompatible change is made — removed API, changed DB schema without migration, changed config variable names, or a breaking change to the install/upgrade path. |
| **MINOR** | New functionality is added in a backwards-compatible way — new UI feature, new endpoint, new config option with a sensible default. |
| **PATCH** | A backwards-compatible bug fix — no new features, no schema changes. |

### Pre-release and dev builds

Between releases, `hatch-vcs` derives the version automatically from git:
- **On a tag** `v1.2.3` → version is `1.2.3`
- **After a tag** (e.g. 4 commits after `v1.2.3`) → version is `1.2.3.dev4+gabcdef0`

This means you never need to manually bump `version =` in `pyproject.toml`. The tag
is the single source of truth.

### What triggers a MAJOR bump

Since quadletman runs as a system service with a SQLite database managed by numbered
migrations, the following always require a MAJOR bump:

- Removing a migration (even a corrective one) — the upgrade path would break.
- Renaming or removing a `QUADLETMAN_*` environment variable without a deprecation cycle.
- Changing the path of the SQLite database or volumes base without providing an
  automatic migration.
- Dropping support for a Python or Podman version that was previously supported.

---

## Release Process

Releases are made by pushing an annotated tag to `main`. The
[`.github/workflows/release.yml`](../.github/workflows/release.yml) workflow runs the
full CI suite, builds the wheel, and creates a GitHub Release automatically.

### Step-by-step

```bash
# 1. Make sure main is clean and CI is green
git checkout main && git pull
git status  # must be clean

# 2. Update CHANGELOG.md
#    - Rename "## [Unreleased]" to "## [X.Y.Z] - YYYY-MM-DD"
#    - Add a new empty "## [Unreleased]" section above it
#    - Update the comparison links at the bottom of the file
git add CHANGELOG.md
git commit -m "CHORE prepare release vX.Y.Z"
git push origin main

# 3. Create and push the annotated tag
git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

Pushing the tag triggers the release workflow, which runs as parallel jobs:

1. **CI gate** — runs the full test suite; all downstream jobs depend on this.
2. **build-wheel** — builds the Python wheel via `uv build --wheel` (platform-independent).
3. **build-rpm** — builds an RPM inside a Fedora container using `packaging/build-rpm.sh`.
4. **build-deb** — builds a `.deb` on Ubuntu using `packaging/build-deb.sh`.
5. **publish** — downloads all artifacts, extracts the `## [X.Y.Z]` section from
   `CHANGELOG.md` as release notes, and creates a GitHub Release with the wheel, RPM,
   and DEB attached.

The `VERSION` env var is passed to each build script as the tag name with the leading `v`
stripped (e.g. tag `v0.3.1` → `VERSION=0.3.1`). Local builds fall back to `git describe`.

See **[docs/packaging.md](packaging.md)** for build prerequisites, package structure, and
how to build and upgrade packages locally.

### Pre-releases

Tag with a `-` suffix to publish a pre-release. Common conventions:

| Tag | Meaning |
|---|---|
| `v0.2.0-alpha.1` | Early preview, may be unstable |
| `v0.2.0-beta.1` | Feature-complete, needs testing |
| `v0.2.0-rc.1` | Release candidate, expected to ship unless bugs found |

```bash
git tag -a v0.2.0-beta.1 -m "Beta 1 for v0.2.0"
git push origin v0.2.0-beta.1
```

The release workflow runs identically to a full release (all packages are built) but GitHub
marks the release with the **Pre-release** label. Any tag containing a `-` triggers this
automatically — no workflow change needed.

Add a matching `## [0.2.0-beta.1]` section to `CHANGELOG.md` before tagging if you want
release notes; otherwise the release body falls back to a link to `CHANGELOG.md`.

### Hotfix releases

For urgent fixes against an already-released version:

```bash
git checkout -b fix/critical-bug vX.Y.Z   # branch from the tag
# … make the fix …
git commit -m "FIX critical bug description"
# Cherry-pick or merge back to main as well:
git checkout main && git cherry-pick <commit-sha>
git push origin main
# Tag the hotfix on the fix branch:
git checkout fix/critical-bug
git tag -a vX.Y.(Z+1) -m "Release vX.Y.(Z+1)"
git push origin vX.Y.(Z+1)
```

---

## GitHub Badges

The following badges appear at the top of [README.md](../README.md):

| Badge | Source |
|---|---|
| CI status | GitHub Actions workflow status |
| Release status | GitHub Actions release workflow status |
| Latest release | GitHub Releases API |
| License | Repository metadata |
| Python version | Static (from `pyproject.toml` `requires-python`) |
| Coverage | Codecov (populated by the `test-python` CI job) |

To enable the coverage badge, install the
[Codecov GitHub App](https://github.com/apps/codecov) on the repository. No token is
needed for public repositories.
