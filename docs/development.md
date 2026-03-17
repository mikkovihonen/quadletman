# Development

## Setup

The project uses [uv](https://docs.astral.sh/uv/) for dependency management and
[ruff](https://docs.astral.sh/ruff/) for linting and formatting.

```bash
# Install uv (once)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install all dependencies including dev tools
uv sync --group dev

# Run the app (uses dev paths when not root)
uv run quadletman

# Lint
uv run ruff check quadletman/

# Format
uv run ruff format quadletman/

# Run tests (must NOT run as root)
uv run pytest

# Run all checks (lint + format + tests)
uv run pre-commit run --all-files

# Rebuild Tailwind CSS (re-run after adding new utility classes to any template; commit the output)
uv run tailwindcss -i quadletman/static/src/app.css \
  -o quadletman/static/src/tailwind.css --minify
```

VS Code users: install the recommended [Ruff extension](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff)
(prompted automatically via `.vscode/extensions.json`). Format-on-save and import
organisation are configured in `.vscode/settings.json`.

## Running Locally

quadletman must run as **root** because it creates system users (`useradd`), manages
`loginctl linger`, reads `/etc/shadow` via PAM, and writes to `/var/lib/quadletman/`.

`uv run quadletman` will fail under `sudo` because `uv` is installed in the user's
`~/.local/bin/` which is not on root's `PATH`. Use the virtualenv binary directly:

```bash
uv sync --group dev          # install deps as your normal user first
sudo .venv/bin/quadletman
```

To keep dev data isolated from any production installation:

```bash
sudo env \
  QUADLETMAN_DB_PATH=/tmp/qm-dev.db \
  QUADLETMAN_VOLUMES_BASE=/tmp/qm-volumes \
  .venv/bin/quadletman
```

### WSL2

systemd is **not** enabled by default in WSL2. Without it, `loginctl enable-linger` is a
no-op and the app will hang for 10 seconds on every compartment creation (the
`_wait_for_runtime_dir` timeout in `user_manager.py`).

Enable systemd by adding the following to `/etc/wsl.conf` and restarting WSL:

```ini
[boot]
systemd=true
```

Additional packages required on WSL2/Ubuntu:

```bash
# Rootless Podman user namespace helpers (must be setuid-root)
sudo apt install uidmap

# fuse-overlayfs — required for overlay mounts without kernel idmap support
sudo apt install fuse-overlayfs
```

### Platform notes

| Concern | Notes |
|---|---|
| PAM authentication | Requires root to read `/etc/shadow`. Works correctly when run as root. |
| SELinux context | Applied automatically when SELinux is active. Safe to ignore on Ubuntu/WSL2 (no-op). |
| systemd user units | Require a live `XDG_RUNTIME_DIR` (`/run/user/{uid}`). Only available after `loginctl enable-linger` succeeds with systemd running. |
| Rootless overlay on WSL2 | Requires `fuse-overlayfs` and `ignore_chown_errors = true` in `storage.conf`. Written automatically by quadletman on compartment creation. |
| UID/GID mapping | Requires `newuidmap`/`newgidmap` to be setuid-root (`apt install uidmap`). |
| Connection monitor on WSL2 | The `nf_conntrack` kernel module is not loaded in the default WSL2 kernel. `conntrack -L` will fail with `Protocol not supported`. Additionally, Podman on WSL2 may use `slirp4netns` or `pasta` for container networking, which bypasses the kernel netfilter stack entirely — so conntrack would see no container traffic even if the module were loaded. The connection monitor degrades silently to an empty list in both cases. This is not a concern for production deployments on standard Linux hosts. |

## Contributing

### Pre-commit hooks

Hooks run automatically on `git commit` and auto-fix what they can. To install and run
manually:

```bash
uv run pre-commit install          # install into .git/hooks/ (once per clone)
uv run pre-commit run --all-files  # run all checks manually
```

Never skip hooks with `--no-verify`.

### Conventions and constraints

All contributor conventions live in [CLAUDE.md](../CLAUDE.md), which is the authoritative
reference:

- **[Key source files](../CLAUDE.md#key-files)** — what each file does
- **[Code Patterns](../CLAUDE.md#code-patterns)** — async, HTMX dual-path, error handling, style
- **[Podman Version Gating](../CLAUDE.md#podman-version-gating)** — how to gate features behind version checks
- **[UI development](ui-development.md)** — macros, component classes, button patterns, modals, state management
- **[What NOT to Do](../CLAUDE.md#what-not-to-do)** — hard constraints

### Testing

```bash
uv run pytest              # Python unit + integration tests (must NOT be run as root)
uv run pytest tests/e2e    # Playwright E2E tests — start a live server, requires browsers:
                           #   uv run playwright install chromium  (once)
npm test                   # JavaScript unit tests via Vitest (requires Node 20+)
```

Test layout under `tests/`:
- `test_models.py`, `test_bundle_parser.py`, `test_podman_version.py` — pure logic, no mocks needed
- `services/` — service-layer tests with all subprocess/os calls mocked via `pytest-mock`
- `routers/` — HTTP route tests using `httpx.AsyncClient` + `ASGITransport`; auth and DB are
  overridden via FastAPI `dependency_overrides`
- `e2e/` — Playwright browser tests against a live server; run with `uv run pytest tests/e2e`
  (excluded from the default `uv run pytest` run to avoid event loop conflicts with pytest-asyncio)
- `js/` — Vitest unit tests for pure JS logic; run with `npm test` (requires Node 20+)

**Key rule:** every test that touches code which would call `subprocess.run`, `os.chown`,
`pwd.getpwnam`, or similar system APIs must mock those calls. Tests must not create Linux
users, touch `/var/lib/`, call `systemctl`, or write outside `/tmp`.

**JS tests:** source files are loaded into the jsdom global context via `window.eval` — no
source changes needed. Add tests for any pure function in `static/src/`. DOM-heavy code
(HTMX handlers, modal wiring) is covered by E2E tests instead.

### Security review

See [CLAUDE.md § Security Review Checklist](../CLAUDE.md#security-review-checklist) for the
full trigger table and per-category checks. Run it before committing any security-relevant
change.

## Database Migrations

Schema changes are applied automatically on startup from numbered SQL files in
`quadletman/migrations/`. Each file is tracked in a `schema_migrations` table and applied
exactly once — files already recorded are skipped on subsequent startups.

| Migration | Description |
|---|---|
| `001_initial.sql` | Full schema: compartments, containers, volumes, pods, image units, events |
