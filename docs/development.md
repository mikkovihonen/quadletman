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
TAILWINDCSS_VERSION=v4.2.2 uv run tailwindcss -i quadletman/static/src/app.css \
  -o quadletman/static/src/tailwind.css --minify
```

VS Code users: install the recommended [Ruff extension](https://marketplace.visualstudio.com/items?itemName=charliermarsh.ruff)
(prompted automatically via `.vscode/extensions.json`). Format-on-save and import
organisation are configured in `.vscode/settings.json`.

## Running Locally

The `scripts/run_dev.sh` helper handles dependency sync and launches the app with
dev-isolated data paths. It supports two modes:

### Root mode (default)

```bash
./scripts/run_dev.sh
```

Runs the app as root with a throwaway DB at `/tmp/qm-dev.db`. This is the simplest
option — all system operations work directly without sudoers configuration.

### Non-root mode (production-like)

```bash
./scripts/run_dev.sh --nonroot
```

Runs the app as a dedicated `qm-dev` system user, mirroring the production `quadletman`
user almost 1:1. The **first run** performs one-time setup (needs sudo):

1. Creates a `qm-dev` system user
2. Adds `qm-dev` to `shadow` and `systemd-journal` groups (PAM + journal access)
3. Installs `/etc/sudoers.d/qm-dev` (mirrors production `packaging/sudoers.d/quadletman`)
4. Creates `/tmp/qm-dev-data/` for the DB and volumes (owned by `qm-dev`)
5. Creates `/run/qm-dev/` for the agent API socket

Subsequent runs skip setup if the user already exists.

| Production | Dev (non-root) | Purpose |
|---|---|---|
| `quadletman` user | `qm-dev` user | Service account |
| `/etc/sudoers.d/quadletman` | `/etc/sudoers.d/qm-dev` | Privilege rules |
| `/var/lib/quadletman/` | `/tmp/qm-dev-data/` | DB + volumes |
| `/run/quadletman/agent.sock` | `/run/qm-dev/agent.sock` | Agent API socket |
| Double-sudo credential delegation | Same | Admin ops via logged-in user's sudo |
| Per-user monitoring agents | Same | qm-\* agents report to agent socket |

When you log in through the browser, admin operations (create compartment, start container,
etc.) escalate via your own sudo credentials — the same path as production.

### Manual invocation (without the helper)

`uv run quadletman` will fail under `sudo` because `uv` is installed in the user's
`~/.local/bin/` which is not on root's `PATH`. Use the virtualenv binary directly:

```bash
uv sync --group dev
sudo .venv/bin/quadletman    # root mode
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

# Optional: kernel keyring credential isolation (session passwords stored in
# kernel memory instead of process memory; auto-detected at startup)
sudo apt install keyutils
```

### Platform notes

| Concern | Notes |
|---|---|
| PAM authentication | Requires read access to `/etc/shadow`. Works as root or when the running user is in the `shadow` group. |
| SELinux context | Applied automatically when SELinux is active. Safe to ignore on Ubuntu/WSL2 (no-op). |
| systemd user units | Require a live `XDG_RUNTIME_DIR` (`/run/user/{uid}`). Only available after `loginctl enable-linger` succeeds with systemd running. |
| Rootless overlay on WSL2 | Requires `fuse-overlayfs` and `ignore_chown_errors = true` in `storage.conf`. Written automatically by quadletman on compartment creation. |
| UID/GID mapping | Requires `newuidmap`/`newgidmap` to be setuid-root (`apt install uidmap`). |
| Connection monitor | Uses `/proc/<pid>/net/tcp` to read the kernel's TCP socket table directly from each container's network namespace. Works for both pasta and slirp4netns. **slirp4netns limitation:** inbound connections (port-forwarded by the host) appear as self-connections (the container's own IP as both source and destination) and transition to TIME_WAIT quickly. Set `QUADLETMAN_CAPTURE_TIME_WAIT=true` to capture these. |

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
- **[Podman Version Gating](../CLAUDE.md#podman-version-gating)** — quick reference for adding version-gated fields
- **[Open Source Governance](governance.md)** — upstream Podman alignment, VersionSpan model, release monitoring
- **[UI development](ui-development.md)** — macros, component classes, button patterns, modals, state management
- **[Localization](localization.md)** — i18n workflow, Finnish vocabulary, adding new languages
- **[What NOT to Do](../CLAUDE.md#what-not-to-do)** — hard constraints

### Router file discipline

Files directly under `routers/` must contain **only** `@router`-decorated route functions.
No helper functions, no inline Pydantic models, no utility classes.

| What | Where |
|------|-------|
| Route functions (`@router.get`, `@router.post`, etc.) | `routers/*.py` — the only place `@router` decorators are allowed |
| Helper functions (context builders, formatting, dependencies) | `routers/helpers/` — split by domain (`common.py`, `volumes.py`, `compartments.py`, `host.py`, `ui.py`); re-exported via `__init__.py` |
| Pydantic request/response models | `models/api.py` — regardless of size; re-exported via `models/__init__.py` |

This keeps router files focused on route definitions and makes helpers and models
discoverable in their canonical locations.

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

### Localization

See [docs/localization.md](localization.md) for the full workflow. Quick reference:

```bash
# After adding or changing any user-visible string:
uv run pybabel extract -F babel.cfg -o quadletman/locale/quadletman.pot .
uv run pybabel update -i quadletman/locale/quadletman.pot -d quadletman/locale -D quadletman
# Edit .po files — translate new/fuzzy entries
uv run pybabel compile -d quadletman/locale -D quadletman
```

Commit `.pot`, `.po`, and `.mo` files in the same commit as the code change.

### Defense-in-depth input sanitization

quadletman uses **branded string types** (`quadletman/models/sanitized.py`) to enforce a five-layer
input sanitization contract. This prevents user-supplied strings from reaching critical host
operations (`host.run`, `host.write_text`, etc.) without proven validation.

#### The types

| Type | Validates | Used for |
|------|-----------|----------|
| `SafeStr` | No control chars (`\n \r \x00`) | General user-supplied strings |
| `SafeSlug` | Slug pattern `^[a-z0-9][a-z0-9-]{0,30}[a-z0-9]$` | `compartment_id` / `service_id` |
| `SafeImageRef` | Image reference pattern, max 255 chars | Container image names |
| `SafeUnitName` | `^[a-zA-Z0-9._@\-]+$` | systemd unit names (safe for journalctl) |
| `SafeSecretName` | `^[a-zA-Z0-9][a-zA-Z0-9._-]*$`, max 253 chars | Podman secret names |

Each type is a `str` subclass. The only way to construct one is:

- **`.of(value, field_name)`** — validates and raises `ValueError` on bad input. Use this for
  any value that originates from user input (HTTP body, path param, form field).
- **`.trusted(value, reason)`** — wraps without re-validating. The `reason` parameter is
  **required** and must describe why the value can be trusted without validation, e.g.
  `"DB-sourced compartment_id"` or `"internally constructed unit name"`. Use only for values
  from trusted internal sources (DB rows, internally constructed strings like
  `f"{name}.service"`). Never call `.trusted()` on a raw HTTP value.

Direct instantiation (`SafeSlug("foo")`) raises `TypeError` to prevent accidental bypass.

#### The five-layer contract

**Layer 1 — HTTP boundary** (`models.py`):
Every Pydantic model class is decorated with two import-time guards:
- `@enforce_model_version_gating(exempt={...})` (outermost) — rejects any field missing a
  `VersionSpan` annotation, ensuring new Quadlet fields always declare which Podman version
  introduced them.  The `exempt` dict maps each exempted field name to a reason string
  so that a code auditor can evaluate it inline without consulting external resources.
- `@sanitized.enforce_model_safety` — rejects any field annotated with plain `str`,
  forcing branded types throughout.

Field validators return the branded type, not plain `str`. At runtime, model fields carry the
branded subclass so the proof flows automatically:

```python
# models.py
@field_validator("id")
@classmethod
def validate_id(cls, v: str) -> SafeSlug:
    slug = SafeSlug.of(v, "id")
    if slug.startswith("qm-"):
        raise ValueError("Compartment ID must not start with 'qm-'")
    return slug
```

`_no_control_chars(v, field_name)` also returns `SafeStr` — all validators that call it
inherit the branded return type automatically.

**Layer 2 — ORM / DB boundary** (`compartment_manager.py`):
DB results are read via SQLAlchemy Core and deserialized with `_validate_row()` /
`_validate_rows()` from `models/api/common.py` (never raw `Model.model_validate(dict(row))`
— see Layer 5).  Branded fields in the response model are validated automatically by Pydantic
during deserialization. Raw mapping values passed directly to service functions are wrapped
explicitly: `SafeResourceName.of(row["name"], "db:table.col")`.

**Layer 3 — Service signatures** (`user_manager.py`, `systemd_manager.py`, etc.):
All `@host.audit`-decorated and other mutating public service functions declare `SafeSlug`
(and `SafeUnitName` / `SafeSecretName`) in their signatures. This makes the upstream
obligation explicit and catchable by mypy:

```python
# systemd_manager.py
@host.audit("UNIT_START", lambda sid, unit, *_: f"{sid}/{unit}")
def start_unit(service_id: SafeSlug, unit: SafeUnitName) -> None:
    ...
```

**Layer 4 — Runtime assertion** (same files):
`@sanitized.enforce` is applied as the innermost decorator on **every** function in
`services/`. It inserts `require()` checks automatically for every `SafeStr`-subclass
parameter, raising `TypeError` — not `ValueError` — at call time if a caller passes a
plain `str`. For functions with no branded-type parameters the decorator is a no-op.

Functions that legitimately take plain `str` parameters (text formatters, OS-path helpers)
go in `services/unsafe/` instead — they are exempt from the decorator rule but must never
receive user-supplied input directly:

```python
@host.audit("UNIT_START", lambda sid, unit, *_: f"{sid}/{unit}")
@sanitized.enforce
async def start_unit(service_id: SafeSlug, unit: SafeUnitName) -> None:
    ...  # no manual require() calls needed
```

**Layer 5 — DB row sanitization on read** (`models/api/common.py`):
When a branded type is tightened (e.g. `SafeStr` → `SafeAbsPathOrEmpty`), existing DB rows
may contain values that no longer pass validation.  Every response model's `_from_db`
validator calls `_sanitize_db_row(d, ModelClass)` which introspects branded-type fields and
resets invalid values to defaults.  `_validate_row()` / `_validate_rows()` persist
corrections back to the database via a `ContextVar` so errors don't recur.  A test in
`test_db_sanitize.py` enforces that no raw `model_validate(dict(...))` calls exist in
`services/` or `routers/`.

See [DB row sanitization](#db-row-sanitization) below for the full implementation guide.

#### Call-site pattern in compartment_manager.py

`compartment_manager.py` is the orchestration layer between routers and service functions.
It reads DB data via SQLAlchemy ORM Core (`select(XxxRow.__table__).where(...)`) and
deserializes results with `Model.model_validate(dict(row))` — Pydantic's
`__get_pydantic_core_schema__` on branded types automatically calls `.of()` for every
branded field during deserialization.

Values that are then passed **to lower-level service functions** (`systemd_manager`,
`quadlet_writer`, etc.) must still be explicitly validated with `.of()` when they are
extracted from the mapping dict:

```python
# DB-sourced value extracted from a mapping row
name = SafeResourceName.of(row["name"], "db:containers.name")
quadlet_writer.remove_container_unit(service_id, name)

# Internally constructed unit name
unit = SafeUnitName.of(f"{container.name}.service", "unit_name")
systemd_manager.restart_unit(sid, unit)

# DB-sourced secret name
name = SafeSecretName.of(row["name"], "secret_name")
secrets_manager.delete_podman_secret(sid, name)
```

When a full Pydantic model is constructed via `model_validate`, the branded-type field
validators run automatically — no manual `.of()` is needed on the resulting model
attributes.

#### Branded type reference

All branded types live in `quadletman/models/sanitized.py`:

| Type | Use for |
|---|---|
| `SafeStr` | Single-line free-text (descriptions, credentials, form fields) |
| `SafeSlug` | Compartment / volume / timer name (slug pattern) |
| `SafeUsername` | PAM-authenticated Linux username |
| `SafeUnitName` | systemd unit name / container name used as a unit |
| `SafeResourceName` | Container / volume / pod / image-unit / timer resource name |
| `SafeResourceNameOrEmpty` | Resource name or empty (optional reference, e.g. build_unit_name) |
| `SafeSecretName` | Podman secret name |
| `SafeImageRef` | Container image reference |
| `SafeImageRefOrEmpty` | Image reference or empty (optional image, e.g. vol_image) |
| `SafeWebhookUrl` | HTTP/HTTPS webhook URL |
| `SafePortMapping` | Port mapping string (`host:container/proto`) |
| `SafeUUID` | UUID row ID |
| `SafeSELinuxContext` | SELinux file context label |
| `SafeAbsPath` | Absolute filesystem path |
| `SafeAbsPathOrEmpty` | Absolute path or empty (optional path, e.g. environment_file, auth_file) |
| `SafeRedirectPath` | Redirect path (open-redirect safe, single `/` prefix) |
| `SafeIpAddress` | IPv4 / IPv6 / CIDR address (or empty = not set) |
| `SafeTimestamp` | ISO 8601 timestamp |
| `SafeFormBool` | HTML form boolean (`true`/`false`/`on`/`off`/`1`/`0`/empty) |
| `SafeOctalMode` | File permission octal string (`644`, `0755`) |
| `SafeTimeDuration` | systemd time duration (`5min`, `1h30s`) |
| `SafeCalendarSpec` | systemd OnCalendar expression (`daily`, `Mon *-*-* 00:00:00`) |
| `SafePortStr` | Port number as string (1–65535, or empty) |
| `SafeNetDriver` | Podman network driver (`bridge`/`macvlan`/`ipvlan`/empty) |
| `SafeMultilineStr` | Multi-line free-text (no null bytes or carriage returns) |

When none of these fit a new structured field, add a new subclass of `SafeStr` in
`sanitized.py` with the appropriate regex before wiring the route or service.

**`OrEmpty` variants** — when a field accepts both a validated value and empty string
(meaning "not set"), use the corresponding `OrEmpty` type: `SafeAbsPathOrEmpty`,
`SafeResourceNameOrEmpty`, `SafeImageRefOrEmpty`.  **Do not** use `SafeXxx | Literal[""]`
union types — they require manual `field_validator` boilerplate and bypass the
`_sanitize_db_row` introspection.  If no `OrEmpty` variant exists, create one in
`sanitized.py` following the existing pattern (empty → accept, non-empty → delegate to
the base type's validation logic).

**`model_validate` ban** — never use raw `Model.model_validate(dict(row))` in services or
routers.  Use `_validate_row()` / `_validate_rows()` from `models/api/common.py` to ensure
DB sanitization fixes are detected and persisted.  A test in `test_db_sanitize.py` enforces
this.

#### Adding a new mutating service function

1. Import `from quadletman.models import sanitized` and the needed branded types from `quadletman.models.sanitized`
2. Declare parameters with the tightest appropriate branded type in the signature
3. Add `@sanitized.enforce` as the innermost decorator — it inserts call-time `require()` checks automatically
4. At every call site wrap DB-sourced or internally constructed values with `.of(value, "field_name")`

See the full checklist in [CLAUDE.md § Host Mutation Tracking](../CLAUDE.md#host-mutation-tracking).

#### Provenance tracking in the audit log

Instances created via `.of()` are plain branded-type instances. Instances created via
`.trusted()` are instances of a private `_Trusted*` subclass that also inherits from
`_TrustedBase`. Both pass `isinstance` checks normally — the distinction is only visible
through `sanitized.provenance()`.

When Python's `logging` level is set to `DEBUG`, the `@host.audit` decorator emits an
additional `PARAMS` line after each `CALL` entry, showing the branded type and provenance
of every branded-type argument:

```
INFO  quadletman.host  CALL USER_CREATE                     my-service
DEBUG quadletman.host  PARAMS USER_CREATE                   service_id=SafeSlug(trusted:DB-sourced compartment_id)

INFO  quadletman.host  CALL UNIT_START                      my-service/mycontainer.service
DEBUG quadletman.host  PARAMS UNIT_START                    service_id=SafeSlug(validated) unit=SafeUnitName(trusted:DB-sourced container name)
```

- `validated` — the value was constructed via `.of()` at an HTTP boundary
- `trusted:<reason>` — the value was constructed via `.trusted()`, with the reason string
  showing exactly why validation was bypassed

At `INFO` level and above the `PARAMS` lines are suppressed — there is no runtime overhead
for production use. Enable `DEBUG` during development or incident investigation to get a
complete provenance trace of all branded-type parameters flowing through host-mutating calls.

#### Testing the pattern

Tests that call functions with `SafeSlug`/`SafeSecretName` parameters must use `.trusted()`.
Each test file that exercises service functions defines convenience aliases:

```python
from quadletman.models.sanitized import SafeSlug, SafeUnitName, SafeSecretName

_sid  = lambda v: SafeSlug.trusted(v, "test fixture")
_unit = lambda v: SafeUnitName.trusted(v, "test fixture")
_sec  = lambda v: SafeSecretName.trusted(v, "test fixture")

# Usage
systemd_manager.start_unit(_sid("testcomp"), _unit("mycontainer.service"))
```

`test_sanitized.py` covers the type construction, validation rejection, and `require()` logic
including the Pydantic model integration test that confirms validators return the correct
branded type at runtime.

### DB row sanitization

When a branded type is tightened (e.g. `SafeStr` → `SafeAbsPathOrEmpty`), existing DB values
may no longer pass validation.  Without a safety net, the app would crash on startup when
reading those rows.

The `_sanitize_db_row()` / `_validate_row()` / `_validate_rows()` pipeline in
`models/api/common.py` prevents this:

1. **Detect** — Every response model's `@model_validator(mode="before")` (`_from_db`) calls
   `_sanitize_db_row(d, ModelClass)`.  It introspects the model's `model_fields`, finds every
   branded `str` subclass with an `.of()` method, and validates each non-empty string value.
   If `.of()` raises, the value is reset to the field's default and the fix is stored in a
   `ContextVar`.

2. **Persist** — `_validate_row()` / `_validate_rows()` in `compartment_manager.py` replace
   raw `Model.model_validate(dict(row))`.  After validation, they read the `ContextVar` and
   issue an `UPDATE` to write corrected values back to the database, so the error doesn't
   recur.

3. **Cleanup** — The `ContextVar` is always reset in a `finally` block to prevent stale fixes
   leaking across rows if an exception is raised during validation or persistence.

**When tightening a field type:**

- No manual fix list needed — `_sanitize_db_row` introspects `model_fields` automatically.
- Ensure the response model has `_sanitize_db_row(d, ModelClass)` in its `_from_db`
  (all current models have this).
- Ensure `compartment_manager.py` uses `_validate_row` / `_validate_rows` (not raw
  `model_validate`) for the affected model.

Tests: `tests/test_db_sanitize.py` covers the full pipeline — detection, context var
propagation, DB persistence, and exception safety.

### Security review

See [CLAUDE.md § Security Review Checklist](../CLAUDE.md#security-review-checklist) for the
full trigger table and per-category checks. Run it before committing any security-relevant
change.

### CodeQL path sanitizers

CodeQL's `py/path-injection` rule cannot trace path validation through function boundaries.
A function that validates a path with `os.path.realpath()` + `str.startswith()` and raises
on traversal is invisible to CodeQL — it still considers the return value tainted and flags
every downstream `os.open()`, `os.path.isfile()`, `shutil.rmtree()`, etc.

The project addresses this with two mechanisms:

1. **Centralized sanitizer functions in `models/sanitized.py`** — all path-traversal
   validation logic lives here, next to the branded types and `log_safe()`.

2. **CodeQL model extensions in `.github/codeql/extensions/path-sanitizers.yml`** — declares
   each sanitizer as a `neutralModel`, telling CodeQL the function does not propagate taint
   from arguments to return value.

#### Current sanitizers

| Function | Purpose |
|---|---|
| `resolve_safe_path(base, path, *, absolute=False)` | Resolves a user-supplied path within a trusted base directory. Uses `os.path.realpath()` + prefix check. Raises `ValueError` on traversal. Set `absolute=True` when `path` is an absolute filesystem path rather than relative to `base`. |
| `volume_path(service_id, volume_name)` | Constructs paths from branded types rooted at a fixed base. Located in `services/volume_manager.py` (not `sanitized.py`) because it depends on app settings. |

#### Usage

```python
from quadletman.models.sanitized import resolve_safe_path

# Relative path (e.g. volume file browser — leading "/" is stripped)
target = resolve_safe_path(vol.host_path, user_path)

# Absolute path (e.g. envfile preview — verified within home dir)
target = resolve_safe_path(home, user_path, absolute=True)
```

#### Adding a new path sanitizer

1. Add the function to `models/sanitized.py`.
2. Add a `neutralModel` entry to `.github/codeql/extensions/path-sanitizers.yml`:
   ```yaml
   - ["quadletman.models.sanitized", "Member[function_name]", "summary", "manual"]
   ```
3. Use it at every call site where user-supplied paths reach filesystem operations.

**Important:** do not scatter path validation helpers across router or service files. Keep
all sanitizer functions in `models/sanitized.py` so the CodeQL extensions file remains the
single reference point. If a sanitizer needs app-specific context (like `volume_path` needs
`settings.volumes_base`), it may live in its service module but must still be declared in
the extensions YAML.

## Database Migrations

The database layer uses **SQLAlchemy 2.x async** (`AsyncSession`) with the `aiosqlite`
dialect. Schema changes are managed by **Alembic**; revisions live in
`quadletman/alembic/versions/`. Migrations run automatically on startup via `init_db()`
in `quadletman/db/engine.py`.

ORM table definitions (the single source of truth for the schema) live in
`quadletman/db/orm.py`. Alembic's `autogenerate` compares these against the live DB to
produce new revisions.

### Adding a schema change

```bash
# 1. Edit the ORM class in quadletman/db/orm.py
# 2. Auto-generate a revision
alembic -c quadletman/alembic/alembic.ini revision --autogenerate -m "short description"
# 3. Review the generated file in quadletman/alembic/versions/ — check for unwanted drops
# 4. Apply to a local DB
alembic -c quadletman/alembic/alembic.ini upgrade head
# 5. Commit orm.py + the new revision file together
```

### Existing revisions

| Revision | Description |
|---|---|
| `0001_baseline_schema_from_migration_009` | Full baseline schema — all tables as of the aiosqlite era |
| `0007_add_process_patterns` | Process pattern matching — `process_patterns` table + `pattern_id` FK on `processes` |

### AsyncSession error handling

**Never use `contextlib.suppress(Exception)` around `db.execute` or `db.commit` calls.**
If a SQLAlchemy operation raises, the session's transaction is left in a failed state.
Any subsequent use of the same session will raise
`"Can't reconnect until invalid transaction is rolled back"`.

Always use explicit `try/except` with rollback:

```python
# WRONG
with contextlib.suppress(Exception):
    await db.execute(insert(FooRow).values(...))

# CORRECT
try:
    await db.execute(insert(FooRow).values(...))
except Exception as exc:
    await db.rollback()
    logger.debug("Insert failed: %s", exc)
```

This is especially important in background loops (`notification_service.py`) where a single
`AsyncSession` is reused across multiple compartments per poll cycle. A suppressed DB error in
one compartment would poison the session for all subsequent compartments in the same iteration.
