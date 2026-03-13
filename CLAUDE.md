# CLAUDE.md — quadletman

## What This Is
quadletman is a FastAPI web UI (HTMX + Tailwind) for managing Podman Quadlet container
services on a Linux host. It runs as root via a systemd service and uses PAM-based HTTP
Basic Auth restricted to sudo/wheel users. See README.md for full user-facing documentation.

## Dev Commands
```bash
uv sync --group dev               # install all deps including dev tools
uv run quadletman                 # run app (uses dev paths when not root)
uv run ruff check quadletman/     # lint
uv run ruff format quadletman/    # format
uv run pytest                     # run test suite (must NOT run as root)
uv run pre-commit run --all-files # run all checks (lint + format + tests)
```

Pre-commit hooks run automatically on `git commit` and auto-fix what they can. Never use
`--no-verify` to skip them.

## Architecture
- Each managed service gets a dedicated Linux user: `qm-{service-id}`
- Quadlet unit files live at: `/home/qm-{id}/.config/containers/systemd/`
- systemd --user commands run via:
  `sudo -u qm-{name} env XDG_RUNTIME_DIR=/run/user/{uid} DBUS_SESSION_BUS_ADDRESS=... systemctl --user ...`
- `loginctl linger` is enabled per service user so units persist after logout
- SQLite DB: `/var/lib/quadletman/quadletman.db` — schema managed by numbered migrations in
  `quadletman/migrations/`
- Volumes: `/var/lib/quadletman/volumes/{service-id}/{volume-name}/` with SELinux `container_file_t`

## Key Files
| File | Purpose |
|------|---------|
| `quadletman/main.py` | App entrypoint, lifespan, exception handlers |
| `quadletman/routers/api.py` | All HTTP routes (REST + HTMX) |
| `quadletman/routers/ui.py` | HTML page routes (login, index) |
| `quadletman/models.py` | Pydantic models for all data |
| `quadletman/services/service_manager.py` | Service lifecycle orchestration — use this, not lower layers directly |
| `quadletman/services/systemd_manager.py` | systemctl --user commands via sudo |
| `quadletman/services/user_manager.py` | Linux user creation, Podman config, loginctl linger |
| `quadletman/services/quadlet_writer.py` | Generates and diffs Quadlet unit files |
| `quadletman/services/metrics.py` | Per-service CPU/memory/disk metrics |
| `quadletman/auth.py` | PAM-based HTTP Basic Auth, sudo/wheel group check |
| `quadletman/database.py` | aiosqlite setup and migration runner |

## Code Patterns

**HTMX-aware responses** — routes check `_is_htmx(request)` and return either a Jinja2
template partial or a JSON response. Always maintain both paths when adding or modifying routes.

**Async everywhere** — all routes and service methods are async. Use `aiosqlite` for DB
access. Run blocking calls with `asyncio.get_event_loop().run_in_executor(None, fn)`.

**Error handling** — raise `HTTPException` with the appropriate status code. Inside `except`
clauses, always chain the original exception:
```python
except ValueError as exc:
    raise HTTPException(400, "Invalid input") from exc
```

**Suppress instead of pass** — use `contextlib.suppress()` instead of `try/except/pass`:
```python
with suppress(KeyError):
    uid = get_uid(service_id)
```

**File I/O** — always use context managers:
```python
with open(path) as f:
    content = f.read()
```

**Style** — 100-char line limit, double quotes, space indentation. Enforced by ruff.
Imports must be at the top of each file, sorted (stdlib → third-party → first-party).

## What NOT to Do
- Do not write to the DB directly — always go through `service_manager.py`
- Do not skip pre-commit hooks (`--no-verify`)
- Do not use bare `open(path).read()` without a context manager
- Do not use `try/except/pass` — use `contextlib.suppress()`
- Do not add `from __future__ import annotations` — the project targets Python 3.11+ natively
- Do not place imports inside functions or conditionally — all imports belong at the top of the file

## Security Notes
- The app runs as root (required for managing system users and SELinux contexts)
- Auth is PAM-based; only users in the `sudo` or `wheel` group are permitted
- All user-supplied strings that touch the filesystem are validated against control characters
  and path traversal before use
- Volume paths are resolved and checked to be within the allowed base directory
- Sessions use HTTPOnly, SameSite=Lax cookies

## Testing
Run `uv run pytest` (never as root — the suite guards against this).

Test layout under `tests/`:
- `test_models.py`, `test_bundle_parser.py`, `test_podman_version.py` — pure logic, no mocks needed
- `services/` — service-layer tests with all subprocess/os calls mocked via `pytest-mock`
- `routers/` — HTTP route tests using `httpx.AsyncClient` + `ASGITransport`; auth and DB are
  overridden via FastAPI `dependency_overrides`

**Key rule:** every test that touches code which would call `subprocess.run`, `os.chown`,
`pwd.getpwnam`, or similar system APIs must mock those calls. Tests must not create Linux
users, touch `/var/lib/`, call `systemctl`, or write outside `/tmp`.

## Doc Update Protocol

Before committing any change, run through this checklist. These docs must be kept accurate;
AI assistants are the primary developers and are responsible for updating them.

### Triggers — update docs when you change any of the following

| What changed | Update these files |
|---|---|
| New source file added or renamed/deleted | CLAUDE.md Key Files table + README.md Contributing → Key source files |
| File purpose significantly changed | CLAUDE.md Key Files table + README.md Contributing → Key source files |
| New service added under `quadletman/services/` | CLAUDE.md Key Files table + README.md Features |
| Architecture changed (file paths, user model, DB location, volume paths, systemd invocation) | CLAUDE.md Architecture + README.md (relevant sections) |
| New or changed dev command | CLAUDE.md Dev Commands + README.md Development Setup |
| Test suite added, removed, or conventions changed | CLAUDE.md Testing + README.md Contributing → Testing |
| New code pattern established or existing pattern changed | CLAUDE.md Code Patterns + README.md Contributing → Code conventions |
| New "do not do" constraint | CLAUDE.md What NOT to Do + README.md Contributing → Constraints |
| Security model change (auth, groups, cookie settings, validation) | CLAUDE.md Security Notes + README.md Security Notes |
| New end-user-visible feature | README.md Features |
| Installation procedure changed | README.md Installation |
| New requirement (Python version, system dep, Podman version) | README.md Requirements |
| New env var, config file, or runtime path | README.md Configuration + CLAUDE.md Architecture if internal |

### Pre-commit checklist

1. Is any row in the trigger table above affected? If no, skip the rest.
2. Open each doc listed for the triggered rows and read the relevant section.
3. Edit the doc to reflect the current state of the code. Remove stale information.
4. Stage the updated doc files in the same commit as the code change.

### Source of truth

- `CLAUDE.md` — primary reference for AI developers. All other AI files defer to it.
- `README.md` — reference for human developers and users. The Contributing section (Pre-commit
  hooks, Key source files, Code conventions, Constraints, Testing) must mirror CLAUDE.md exactly.
  **Never leave README.md stale** — a discrepancy between these two files is a bug.
- `AGENTS.md` — pointer to CLAUDE.md. Only update if the pointer itself is wrong.
- `.github/copilot-instructions.md` — coding hints. Update only if a core pattern changes.

### Mirror map — CLAUDE.md → README.md Contributing

| CLAUDE.md section | README.md Contributing subsection |
|---|---|
| Dev Commands | Development Setup code block |
| Testing | Contributing → Testing |
| Code Patterns | Contributing → Code conventions |
| What NOT to Do | Contributing → Constraints |
| Key Files | Contributing → Key source files |
