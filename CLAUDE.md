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
TAILWINDCSS_VERSION=v4.2.2 uv run tailwindcss -i quadletman/static/src/app.css \
  -o quadletman/static/src/tailwind.css --minify
                                  # rebuild Tailwind CSS — re-run after adding new utility
                                  # classes to any template; commit the output file
                                  # TAILWINDCSS_VERSION must match ci.yml and .pre-commit-config.yaml
uv run pybabel extract -F babel.cfg -o quadletman/locale/quadletman.pot .
                                  # re-extract translatable strings after adding/changing strings
uv run pybabel update -i quadletman/locale/quadletman.pot -d quadletman/locale -D quadletman
                                  # update existing .po files with new/changed strings
uv run pybabel compile -d quadletman/locale -D quadletman
                                  # compile .po → .mo for runtime use (run after updating)
                                  # See docs/localization.md for the full localization workflow
npm test                          # run JavaScript unit tests (Vitest, Node 20+ required)
```

Pre-commit hooks run automatically on `git commit` and auto-fix what they can. Never use
`--no-verify` to skip them.

## Architecture
- Each managed compartment gets a dedicated Linux user: `qm-{compartment-id}`
- Quadlet unit files live at: `/home/qm-{id}/.config/containers/systemd/`
- systemd --user commands run via:
  `sudo -u qm-{name} env XDG_RUNTIME_DIR=/run/user/{uid} DBUS_SESSION_BUS_ADDRESS=... systemctl --user ...`
- `loginctl linger` is enabled per compartment root so units persist after logout
- SQLite DB: `/var/lib/quadletman/quadletman.db` — schema managed by Alembic migrations in
  `quadletman/alembic/versions/`; ORM table definitions (single source of truth) in
  `quadletman/db/orm.py`; accessed via `AsyncSession` yielded by `quadletman/db/engine.py`
- Volumes: `/var/lib/quadletman/volumes/{compartment-id}/{volume-name}/` with SELinux `container_file_t`

## Key Files
| File | Purpose |
|------|---------|
| `quadletman/main.py` | App entrypoint, lifespan, exception handlers |
| `quadletman/routers/api.py` | Shared helpers + logout/dashboard/help routes + DB backup download; wires sub-routers |
| `quadletman/routers/compartments.py` | Compartment CRUD, lifecycle, sync, metrics, metrics-history, restart-stats, status routes |
| `quadletman/routers/containers.py` | Container/pod/image-unit CRUD, envfile, form routes; image list/prune/pull endpoints |
| `quadletman/routers/secrets.py` | Secret CRUD routes; delegates to `secrets_manager` for podman store operations |
| `quadletman/routers/timers.py` | Timer (scheduled task) CRUD + last-run/next-run status endpoint; writes `.timer` unit files via `quadlet_writer` |
| `quadletman/routers/templates.py` | Service template save/list/delete and clone-from-template routes |
| `quadletman/routers/volumes.py` | Volume CRUD, file browser, chmod, archive/restore routes |
| `quadletman/routers/logs.py` | Log streaming (SSE), journal, WebSocket terminal, podman-info |
| `quadletman/routers/host.py` | Host settings (sysctl), SELinux booleans, registry logins, events |
| `quadletman/routers/ui.py` | HTML page routes (login, index) |
| `quadletman/models/api.py` | Pydantic request/response models for all data; response models include `@model_validator(mode="before")` for JSON column deserialization |
| `quadletman/config/settings.py` | Pydantic `BaseSettings`; loads all `QUADLETMAN_*` env vars |
| `quadletman/session.py` | In-memory session store; `create_session` / `get_session` / `delete_session` with absolute + idle TTL |
| `quadletman/podman_version.py` | Podman version detection; `PodmanFeatures` dataclass with per-feature boolean flags |
| `quadletman/services/compartment_manager.py` | Compartment lifecycle orchestration — use this, not lower layers directly |
| `quadletman/services/systemd_manager.py` | systemctl --user commands via sudo |
| `quadletman/services/user_manager.py` | Linux user creation, Podman config, loginctl linger |
| `quadletman/services/quadlet_writer.py` | Generates and diffs Quadlet unit files (containers, pods, volumes, images, timers, networks) |
| `quadletman/services/secrets_manager.py` | Wrappers for `podman secret ls/create/rm` run as the compartment user |
| `quadletman/services/notification_service.py` | Background monitor that polls container states and fires webhooks (with retry) on on_start/on_stop/on_failure/on_restart events; also samples and stores periodic metrics |
| `quadletman/services/bundle_parser.py` | Parser for `.quadlets` multi-unit bundle files (Podman 5.8+) |
| `quadletman/services/metrics.py` | Per-compartment CPU/memory/disk metrics |
| `quadletman/services/archive.py` | Safe archive extraction helpers (ZIP/TAR) with zip-slip guards |
| `quadletman/services/volume_manager.py` | Volume directory management, helper user ownership |
| `quadletman/i18n.py` | Thin gettext wrapper; `set_translations(lang)` called by middleware; `gettext as _` imported by routers |
| `quadletman/routers/_helpers.py` | Shared helpers used across all domain routers: HTMX detection, formatting, compartment context utilities |
| `quadletman/config/templates.py` | Shared `Jinja2Templates` instance with i18n extension; both routers import `TEMPLATES` from here |
| `quadletman/locale/` | Gettext catalogs — `quadletman.pot` (source), `{lang}/LC_MESSAGES/quadletman.po/.mo` |
| `babel.cfg` | Babel extraction config; maps `.py` and `.html` files to extractors |
| `scripts/podman_feature_check.py` | Checks new Podman releases for Quadlet-relevant changes; diffs man page keys and filters release notes |
| `.github/workflows/podman-watch.yml` | Weekly scheduled workflow that runs the feature check script and creates GitHub issues for new Podman releases |
| `quadletman/models/sanitized.py` | Centralized branded string types (`SafeStr`, `SafeSlug`, `SafeUnitName`, `SafeSecretName`, `SafeResourceName`, `SafeImageRef`, `SafeWebhookUrl`, `SafePortMapping`, `SafeUUID`, `SafeSELinuxContext`, `SafeMultilineStr`, `SafeAbsPath`, `SafeTimestamp`, `SafeIpAddress`) + `@sanitized.enforce` / `@sanitized.enforce_model` decorators + `resolve_safe_path()` path-traversal sanitizer + `log_safe()` log-injection sanitizer — defense-in-depth input proof; only constructable via `.of()` in production |
| `.github/codeql/extensions/path-sanitizers.yml` | CodeQL model extensions declaring `resolve_safe_path` and `volume_path` as path sanitizers (neutralModel) so CodeQL does not flag their return values for `py/path-injection` |
| `quadletman/services/host.py` | Wrappers for all host-mutating operations + `@host.audit` decorator; all mutations log to `quadletman.host` |
| `quadletman/services/host_settings.py` | Read/write host kernel (sysctl) settings; persists to `/etc/sysctl.d/99-quadletman.conf` |
| `quadletman/services/selinux.py` | SELinux file-context helpers (`apply_context`, `relabel`); no-ops when SELinux inactive |
| `quadletman/services/selinux_booleans.py` | Read/set SELinux boolean values relevant to Podman containers; uses `getsebool`/`setsebool -P` |
| `quadletman/auth.py` | PAM-based HTTP Basic Auth, sudo/wheel group check |
| `quadletman/templates/macros/ui.html` | Jinja2 macros — see Macros section below; use for all new modals, form inputs, list editors, and tab panels |
| `quadletman/db/engine.py` | SQLAlchemy async engine, WAL pragma, `AsyncSessionLocal` factory, `get_db()` FastAPI dependency, `init_db()` Alembic runner |
| `quadletman/db/orm.py` | SQLAlchemy ORM table definitions (15 tables) — single source of truth for schema |
| `quadletman/alembic/` | Alembic migration environment; revisions in `versions/` |

## Code Patterns

**HTMX-aware responses** — routes check `_is_htmx(request)` and return either a Jinja2
template partial or a JSON response. Always maintain both paths when adding or modifying routes.

**URL-reflected navigation** — the browser URL must reflect the active main-content view so
that reloading the page restores the same view. The canonical URL scheme is:
- `/` → dashboard
- `/compartments/{compartment_id}` → compartment detail
- `/help` → help page
- `/events` → event log

Navigation is driven by `loadDashboard()`, `loadCompartment(id)`, `loadHelp()`, and
`loadEvents()` in `navigation.js` — these call `history.pushState` and load the HTMX partial. Each navigable
view also has a corresponding SPA-fallback route in `ui.py` that serves `index.html` so
hard refreshes work. Ephemeral overlays (modals, log viewer, terminal) are **not** encoded
in the URL — on reload the user lands on the underlying view without the overlay.

**Async everywhere** — all routes and service methods are async. Use SQLAlchemy `AsyncSession`
(injected via `Depends(get_db)`) for DB access. Run blocking calls with
`asyncio.get_event_loop().run_in_executor(None, fn)`.

**Error handling** — raise `HTTPException` with the appropriate status code. Inside `except`
clauses, always chain the original exception:
```python
except ValueError as exc:
    raise HTTPException(400, "Invalid input") from exc
```

**Suppress instead of pass** — use `contextlib.suppress()` instead of `try/except/pass`:
```python
with suppress(KeyError):
    uid = get_uid(compartment_id)
```

**Never suppress exceptions around `AsyncSession` operations** — `contextlib.suppress(Exception)`
must not wrap any `await db.execute(...)` or `await db.commit()` call. If an exception escapes
from a SQLAlchemy operation, the session's internal transaction is left in a failed state and any
subsequent use of the same session raises
`"Can't reconnect until invalid transaction is rolled back"`. Always use an explicit `try/except`
with `await db.rollback()` before suppressing:
```python
# WRONG — poisons the session on any DB error
with contextlib.suppress(Exception):
    await db.execute(insert(FooRow).values(...))

# CORRECT
try:
    await db.execute(insert(FooRow).values(...))
except Exception as exc:
    await db.rollback()
    logger.debug("Insert failed: %s", exc)
```
This applies everywhere a session is held across multiple operations — especially in background
loops (`notification_service.py`) where the same session is reused across compartments in a
single poll cycle.

**File I/O** — always use context managers:
```python
with open(path) as f:
    content = f.read()
```

**Style** — 100-char line limit, double quotes, space indentation. Enforced by ruff.
Imports must be at the top of each file, sorted (stdlib → third-party → first-party).

**Defense-in-depth input sanitization** — `quadletman/models/sanitized.py` defines branded string
types that are the only allowed form of user-supplied input at service layer boundaries.
Holding an instance proves validation has occurred; passing a raw `str` is a type error.

**Four-layer contract** (HTTP → ORM → service signature → runtime):

1. **HTTP boundary** (`models/api.py`) — Pydantic field validators call `SafeSlug.of(v)` /
   `SafeStr.of(v)` and return the branded instance. The field's runtime type is the branded
   subclass, not plain `str`. FastAPI also calls `__get_pydantic_core_schema__()` on branded
   types used as path/query/form parameters, so annotating the parameter is sufficient.

2. **ORM / DB boundary** (`compartment_manager.py`) — DB results are read via SQLAlchemy
   Core (`select(XxxRow.__table__).where(...)`) and deserialized with
   `Model.model_validate(dict(row))`. Because the response model fields are typed with
   branded types, Pydantic's `__get_pydantic_core_schema__` on those types calls `.of()`
   automatically during deserialization. No manual `.of()` is needed for values that flow
   directly into a model field. When a raw value from a `result.mappings()` dict is passed
   **directly to a service function** (not via a model), wrap it explicitly:
   ```python
   name = SafeResourceName.of(row["name"], "db:containers.name")
   quadlet_writer.remove_container_unit(service_id, name)
   ```

3. **Service signatures** (`systemd_manager.py`, `quadlet_writer.py`, etc.) — All
   `@host.audit`-decorated and other mutating public service functions accept `SafeSlug`
   (for `service_id` / `compartment_id`) and `SafeUnitName` / `SafeSecretName` / `SafeStr`
   for other user-supplied arguments. This makes the upstream obligation explicit in the
   type signature.

4. **Runtime assertion** — Add `@sanitized.enforce` to every service function that has
   `SafeStr`-subclass-typed parameters. The decorator reads type annotations at decoration
   time and calls `require()` for each branded parameter at every invocation, raising
   `TypeError` if a caller passes a plain `str`. Do **not** write manual
   `sanitized.require()` calls — `@sanitized.enforce` replaces them entirely.

```python
# In a new mutating service function:
from quadletman.models import sanitized
from quadletman.models.sanitized import SafeSlug, SafeUnitName

@host.audit("MY_ACTION", lambda sid, unit, *_: f"{sid}/{unit}")
@sanitized.enforce
def my_action(service_id: SafeSlug, unit: SafeUnitName) -> None:
    host.run(["systemctl", "--user", "...", unit], ...)
```

Decorator order: `@host.audit` outermost, `@sanitized.enforce` innermost (directly above
`def`). Works transparently on both sync and async functions.

**`@sanitized.enforce` is mandatory on every `@host.audit` function** — `@host.audit`
raises `TypeError` at decoration time (i.e. at import time) if `@sanitized.enforce` is
missing. This is enforced mechanically; forgetting it will break the import. The rule
applies even when the function has no string parameters — use `@sanitized.enforce` on
every audited function without exception. Any `str` parameter must be changed to the
tightest fitting branded type before `@sanitized.enforce` can be applied.

**`AsyncSession` and `@sanitized.enforce`** — `AsyncSession` has its own `__annotations__`
which would confuse the decorator. It is marked `AsyncSession._sanitized_enforce_model = True`
in `db/engine.py` (and again in `compartment_manager.py` before the project imports) so
`@sanitized.enforce` skips it correctly. Do not set this flag on any other third-party class.

**Wrapping raw mapping values** — When a value is extracted from a `result.mappings()` dict
to be passed directly to a service function (not going through `model_validate`), wrap it:

```python
name = SafeResourceName.of(row["name"], "db:containers.name")
unit = SafeUnitName.of(f"{name}.service", "unit_name")
```

**`.trusted()` is banned in production code with one exception** — `SafeXxx.trusted()` may
only appear in:
1. Test files (`tests/`) — as test fixtures.
2. Pydantic model field defaults — when the default is a hardcoded literal known at
   development time (e.g. `SafeStr.trusted("", "default")`). These are compile-time
   constants, not user input, so skipping regex validation is safe.
3. `compartment_manager.py` UUID generation — `SafeUUID.trusted(str(uuid.uuid4()), "reason")`
   where the value is machine-generated and structurally guaranteed correct.

All other production code must use `.of()`. If `.of()` raises on a DB-sourced value, that
is a data integrity problem that must be surfaced, not silenced with `.trusted()`.

**Router parameter types — no raw `str` allowed** — Every `@router.*` route function must
type all user-supplied path, query, and form parameters with a branded type from
`quadletman/models/sanitized.py`. Plain `str` is not permitted for any parameter that carries
user input. Choose the tightest type that fits:

| Input shape | Type |
|---|---|
| Compartment / volume / timer name (slug pattern) | `SafeSlug` |
| systemd unit name / container name used as unit | `SafeUnitName` |
| Container / volume / pod / image-unit / timer resource name | `SafeResourceName` |
| Podman secret name | `SafeSecretName` |
| Container image reference | `SafeImageRef` |
| HTTP/HTTPS webhook URL | `SafeWebhookUrl` |
| Port mapping string (`host:container/proto`) | `SafePortMapping` |
| UUID row ID (container_id, secret_id, etc.) | `SafeUUID` |
| SELinux file context label | `SafeSELinuxContext` |
| Absolute filesystem path | `SafeAbsPath` |
| IPv4 / IPv6 / CIDR address | `SafeIpAddress` |
| ISO 8601 timestamp | `SafeTimestamp` |
| Multi-line free-text (no null bytes or carriage returns) | `SafeMultilineStr` |
| Single-line free-text (descriptions, credentials, form fields) | `SafeStr` |

FastAPI calls `__get_pydantic_core_schema__()` on these types automatically, so annotating
the parameter is sufficient — no manual `.of()` call is needed in the route body.

If no existing branded type fits a new parameter (e.g. a new structured format), add a new
subclass of `SafeStr` in `models/sanitized.py` with the appropriate regex before wiring the route.
Proposing "use `SafeStr` for now" without a new type is acceptable only when the field is
genuinely free-text with no structural constraints. UUID-format row IDs (`container_id`,
`secret_id`, etc.) use `SafeStr` because `SafeSlug` caps at 32 chars and UUIDs are 36.

**Checklist when adding a new route:**
1. For every `str` parameter: pick the tightest branded type from the table above.
2. If none fits: add a new `SafeXxx` class to `models/sanitized.py` first, then use it.
3. Never leave a route parameter typed as plain `str`.

## CodeQL Path Sanitizers

CodeQL cannot trace path validation through function boundaries. When a function validates
that a resolved path stays within a trusted base directory, CodeQL still considers the
return value tainted and flags every downstream filesystem operation as `py/path-injection`.

To suppress these false positives without per-line comments (which break when lines shift),
the project uses **CodeQL model extensions** combined with **centralized sanitizer functions**.

### How it works

1. **Sanitizer functions live in `models/sanitized.py`** — alongside branded types and
   `log_safe()`. This is the single module CodeQL extensions reference; all path-sanitization
   logic must be here.

2. **`.github/codeql/extensions/path-sanitizers.yml`** declares each sanitizer as a
   `neutralModel`, telling CodeQL not to propagate taint through the function. The return
   value is considered clean regardless of input taint.

### Existing sanitizers

| Function | Module | Purpose |
|---|---|---|
| `resolve_safe_path(base, path, *, absolute=False)` | `models/sanitized.py` | Resolves a user-supplied path within a trusted base directory using `os.path.realpath()` + prefix check. Raises `ValueError` on traversal. Handles both relative paths (default) and absolute paths (`absolute=True`). |
| `volume_path(service_id, volume_name)` | `services/volume_manager.py` | Constructs volume paths from branded-type inputs (`SafeSlug`, `SafeResourceName`) rooted at a fixed base directory. No user-supplied raw string reaches the result. |

### Usage

```python
from quadletman.models.sanitized import resolve_safe_path

# Relative path (volume file browser — leading "/" is stripped)
target = resolve_safe_path(vol.host_path, user_path)

# Absolute path (envfile preview — verified to be within home dir)
target = resolve_safe_path(home, user_path, absolute=True)
```

### Adding a new path sanitizer

1. Add the function to `models/sanitized.py`.
2. Add a `neutralModel` entry to `.github/codeql/extensions/path-sanitizers.yml`:
   ```yaml
   - ["quadletman.models.sanitized", "Member[new_function]", "summary", "manual"]
   ```
3. Use the function at every call site where user-supplied paths reach filesystem operations.

**Do not** scatter path validation across router files — keep it in `models/sanitized.py` so
the CodeQL extensions file remains the single point of reference.

## Host Mutation Tracking

All code that changes the state of the Linux host — creating users, writing files, calling
system tools — must go through the wrappers in `quadletman/services/host.py`. This keeps every
host modification visible in one filterable log stream (`quadletman.host`) and enforces a
consistent audit trail.

### The two instruments

**`host.*` wrappers** — use instead of the standard library for mutating operations:

| Instead of | Use |
|---|---|
| `subprocess.run(mutating_cmd, ...)` | `host.run(mutating_cmd, ...)` |
| `open(path, "w") + os.chown + os.chmod` | `host.write_text(path, content, uid, gid)` |
| `open(path, "a") + f.write(...)` | `host.append_text(path, content)` |
| `open(path, "w") + f.writelines(...)` | `host.write_lines(path, lines)` |
| `os.makedirs(path, ...)` | `host.makedirs(path, ...)` |
| `os.unlink(path)` | `host.unlink(path)` |
| `os.symlink(src, dst)` | `host.symlink(src, dst)` |
| `os.chmod(path, mode)` | `host.chmod(path, mode)` |
| `os.chown(path, uid, gid)` | `host.chown(path, uid, gid)` |
| `os.rename(src, dst)` | `host.rename(src, dst)` |
| `shutil.rmtree(path, ...)` | `host.rmtree(path, ...)` |

**`@host.audit(action, target)`** — annotate every public service function that triggers host
mutations:

```python
@host.audit("USER_CREATE", lambda sid, *_: sid)
@sanitized.enforce
def create_service_user(service_id: SafeSlug) -> int:
    ...

@host.audit("UNIT_STOP", lambda sid, unit, *_: f"{sid}/{unit}")
@sanitized.enforce
def stop_unit(service_id: SafeSlug, unit: SafeUnitName) -> None:
    ...
```

`action` is a short ALL_CAPS label. `target` is a lambda over the function's positional
arguments that produces a human-readable identifier for the affected resource. Use `*_` to
absorb unused trailing args. The decorator works on both sync and async functions.

### What NOT to route through `host`

Read-only subprocess calls do not modify the host and must **not** use `host.run()`:

- `journalctl`, `systemctl show/status`, `podman info/images/logs`
- `getsebool`, `getenforce`, `stat`
- Any call where the sole purpose is reading state

Continue to use `subprocess.run()` directly for these.

### Where to use each instrument

- Use `host.*` wrappers at the call site of every mutating primitive inside a service function.
- Use `@host.audit` on the **public** service function that owns the operation — not on internal
  helpers that are only called from one place.
- When a single public function causes multiple different mutations internally (e.g.
  `create_service_user` calls `useradd`, `groupadd`, and writes `/etc/subuid`), the
  `@host.audit` entry captures the intent; the individual `host.run` / `host.write_text` calls
  capture the detail.

### Reading the audit log

All entries go to the `quadletman.host` logger. With the default `logging.basicConfig` they
appear in the main log alongside application messages. To isolate them:

```bash
journalctl -u quadletman | grep 'quadletman.host'
```

To route them to a dedicated file, add a handler in `main.py`:

```python
_audit_handler = logging.FileHandler("/var/log/quadletman/host.log")
_audit_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
logging.getLogger("quadletman.host").addHandler(_audit_handler)
```

Three log line prefixes:
- `CALL` — a public service function was invoked (from `@host.audit`)
- `CMD` — a subprocess was executed (from `host.run`)
- `MKDIR / WRITE / UNLINK / RMTREE / ...` — a filesystem operation ran

### Checklist when adding a new host-mutating operation

1. Is the operation mutating? If yes, use `host.*` — never `subprocess.run` or `os.*` directly.
2. Is the function public (called from outside the service file)? If yes, add `@host.audit`.
3. Does the `action` label follow the existing vocabulary? (see existing decorators in the
   service files for examples)
4. Is the `target` lambda extracting the right identifier (service id, path, or `sid/resource`)?
5. Add `@sanitized.enforce` as the innermost decorator (directly above `def`) — this is
   **mandatory on every `@host.audit` function**, with or without string parameters.
   `@host.audit` enforces this mechanically: it raises `TypeError` at import time if
   `@sanitized.enforce` is absent. Change any `str` parameter to the tightest branded
   type before applying `@sanitized.enforce`. Do **not** write manual `sanitized.require()`
   calls — the decorator handles them automatically. Import `sanitized` from `quadletman.models`.
6. At every **call site** in `compartment_manager.py` or routers:
   - Values extracted from `result.mappings()` dicts and passed directly to a service
     function must be wrapped: `SafeResourceName.of(row["name"], "db:table.column")`.
   - Internally constructed strings must be wrapped:
     `SafeUnitName.of(f"{name}.service", "unit_name")`.
   - Values that flow through `Model.model_validate(dict(row))` are validated automatically
     by Pydantic — no manual `.of()` needed for model attributes.
   - Never use `.trusted()` in production code; it is only permitted in test files under
     `tests/`, hardcoded Pydantic field defaults, and UUID generation in service functions.

## Podman Version Gating

Every feature with a minimum Podman version requirement must be guarded at all three layers:

1. **Flag in `PodmanFeatures`** (`podman_version.py`): Add a boolean field with a comment
   stating the minimum version. Set it in `get_features()`:
   ```python
   new_feature: bool  # >= X.Y.0 — short description
   # in get_features():
   new_feature=version is not None and version >= (X, Y, 0),
   ```

2. **Server-side guard** (the relevant sub-router in `routers/`): At the top of every route that uses the feature,
   call `get_features()` (lru-cached — no cost) and raise HTTP 400 if the flag is false:
   ```python
   features = get_features()
   if not features.new_feature:
       raise HTTPException(400, f"Requires Podman X.Y+ (detected: {features.version_str})")
   ```

3. **UI gate** (templates): Disable the relevant button/input with `<button disabled>`,
   `opacity-50 cursor-not-allowed`, and a `title` tooltip showing the required version and
   `{{ podman.version_str }}`. Follow the disabled button convention in UI Conventions exactly.

4. **Tests**: Add a test case in `tests/test_podman_version.py` asserting the flag is false
   one version below the threshold and true at the threshold. Add a route test in
   `tests/routers/` asserting the guarded route returns HTTP 400 when the flag is patched to
   false (see `tests/routers/test_version_gates.py` for the pattern).

### Quadlet template keys vs. route-level features

Not every version-gated feature maps to an HTTP route. Some features are keys inside
generated quadlet unit files (e.g. `PullPolicy=` in `.image` units). These require a
**template-level gate** instead of a server-side route guard:

- Pass `podman=get_features()` into the Jinja2 render call for the affected template.
- Wrap the key in `{% if feature_flag %}...{% endif %}` in the template.
- Disable the corresponding form input in the UI (disabled `<select>` or `<input>` with
  `title` tooltip) so users on older Podman cannot set a value that would break the
  generated unit file.
- No route-level HTTP 400 guard is needed — the feature degrades silently by omitting the
  key rather than by blocking the request.

### How to discover the minimum version for a feature

When the Quadlet generator or Podman CLI rejects a key with `unsupported key 'X'` or
`unknown flag`, that is the signal to add a version gate.

1. **Read the error.** The generator logs the exact unsupported key and the file it came
   from. That tells you precisely what to gate.
2. **Check the Podman changelog.** Search the `containers/podman` GitHub releases for the
   key name to find the version it was introduced.
3. **Verify with the man page.** `podman-systemd.unit(5)` documents which keys exist per
   section. The version that added the key is usually noted inline.
4. **Gate conservatively.** If you cannot confirm the exact minor version, use the next
   major version boundary (e.g. `5.0.0`) rather than a patch version. A disabled field on
   a slightly older minor release is better than a broken unit file.
5. **Test at the boundary.** Always assert the flag is `False` one version below the
   threshold (e.g. `(4, 9, 3)`) and `True` at the threshold (e.g. `(5, 0, 0)`).

**Scope of version gating:**
- Any key added to a quadlet unit file section (`.container`, `.image`, `.network`, etc.)
- Any `podman` CLI flag used in `systemd_manager.py` or `user_manager.py`
- Standard systemd `[Unit]` / `[Service]` / `[Install]` keys are **not** gated — they are
  systemd's responsibility, not Podman's.

## Localization

The full localization reference lives in **[docs/localization.md](docs/localization.md)**. It covers
the extract → update → translate → compile workflow, Finnish vocabulary, fuzzy entry handling,
and how to add a new language.

Quick rules to remember:
- Every user-visible string in Python must use `from quadletman.i18n import gettext as _` then `_("…")`.
- Every user-visible string in Jinja2 templates must use `{{ _("…") }}` or `{{ ngettext(…) }}`.
- Always run the full Babel cycle (extract → update → compile) and commit `.pot`, `.po`, and `.mo`
  files in the same commit as the code change.
- For Finnish: verify all new terms against the vocabulary table in `docs/localization.md`. Never
  use "säiliö" for Container — use **Kontti**.
- After `pybabel update`, review all `#, fuzzy` entries before committing — auto-guessed
  translations are often wrong.

## UI Conventions

The full UI reference lives in **[docs/ui-development.md](docs/ui-development.md)**. It covers
JS modules, state management (URL / Alpine / HTMX layers), semantic component classes,
macros, button sizes, modal sizing strategies, `x-show` transition rules, Alpine pre-boot
flash, disclosure forms, section visibility, and the modal close button rule.

Quick rules to remember:
- Use `modal_shell` macro for every new dialog modal (`{% from "macros/ui.html" import modal_shell %}`)
- Use `form_field` macro for every `<label> + <input>` group
- Use `qm-*` component classes from `app.css` instead of raw Tailwind utility repetition
- Rebuild Tailwind after adding new utility classes: `TAILWINDCSS_VERSION=v4.2.2 uv run tailwindcss -i quadletman/static/src/app.css -o quadletman/static/src/tailwind.css --minify`
- Implicit `x-show` reveals → add fade transitions; explicit tab switches → no transitions
- Every `overflow-y-auto` container that can grow to viewport height → `style="scrollbar-gutter: stable"`
- Destructive actions → `hx-confirm` required; reversible actions → no confirmation needed

## What NOT to Do
- Do not write to the DB directly — always go through `compartment_manager.py`
- Do not skip pre-commit hooks (`--no-verify`)
- Do not use bare `open(path).read()` without a context manager
- Do not use `try/except/pass` — use `contextlib.suppress()`
- Do not add `from __future__ import annotations` — the project targets Python 3.12+ natively.
  Exception: `models/sanitized.py` requires it because branded types have many self-referential
  return annotations (e.g. `SafeSlug.of() -> SafeSlug`) that would need string quoting otherwise
- Do not place imports inside functions or conditionally — all imports belong at the top of the file
- Do not add `<script src="...">` or `<link href="...">` pointing to any external host — all
  third-party JS/CSS assets must be in `quadletman/static/vendor/` (referenced as `/static/vendor/...`);
  first-party assets belong in `quadletman/static/src/` (referenced as `/static/src/...`)

## Security Notes
- The app runs as root (required for managing system users and SELinux contexts)
- Auth is PAM-based; only users in the `sudo` or `wheel` group are permitted
- All user-supplied strings that touch the filesystem are validated against control characters
  and path traversal before use
- Volume paths are resolved with `_resolve_vol_path()` and checked to stay within the base dir
- File-write operations use `os.open(O_NOFOLLOW)` to prevent symlink-swap (TOCTOU) attacks
- Session cookies: HTTPOnly, SameSite=Strict; set `QUADLETMAN_SECURE_COOKIES=true` for Secure flag
- CSRF protection: double-submit cookie (`qm_csrf`) validated by `CSRFMiddleware` in `main.py`
- Security headers on every response: `X-Frame-Options`, `X-Content-Type-Options`, CSP,
  `Referrer-Policy` (HSTS added when `secure_cookies=True`)
- CSP includes `unsafe-eval` (required by Alpine.js expression evaluation) and `unsafe-inline`
  (required by inline `<script>` blocks in templates); acceptable trade-off for an internal
  admin tool. No external hosts are permitted in the CSP — all assets are served from `'self'`.
- Container image references validated against `IMAGE_RE` in `models/sanitized.py`
- Bind-mount `host_path` checked against `_BIND_MOUNT_DENYLIST` (blocks `/etc`, `/proc`, etc.)

## Security Review Checklist

Run this before committing any change. The app runs as root; a missed security issue can
affect the host system.

### AI-assisted review (VS Code)

In addition to the manual checklist below, run an AI security review for any
security-relevant change before committing:

1. Open the VS Code Command Palette → **Tasks: Run Task** → choose a **Security Review**
   variant:
   - **Security Review (staged)** — reviews only staged changes (most common before commit)
   - **Security Review (HEAD)** — reviews last commit
   - **Security Review (branch vs main)** — reviews all changes on the current branch
2. The task runs `scripts/security_review.py` and prints a formatted prompt in the terminal.
3. Copy the entire prompt and paste it into a Claude Code chat in VS Code.
4. Review the findings. CRITICAL and HIGH findings must be resolved before committing;
   MEDIUM and LOW are advisory.

The script skips automatically when no security-relevant files are changed
(`routers/`, `auth.py`, `main.py`, `models.py`, `services/`, `session.py`, `db/`).

### Triggers — run the relevant checks when you change:

| What changed | Checks to run |
|---|---|
| New HTTP route (any method) | Auth dependency, CSRF for mutating methods, input validation |
| User-supplied value reaches filesystem | Path traversal (`resolve_safe_path`), `O_NOFOLLOW` on writes |
| New Pydantic model field | `_no_control_chars`, format/length constraints |
| File upload or archive handling | Filename sanitisation, zip-slip guards, `_MAX_UPLOAD_BYTES` cap |
| `subprocess` call with any variable argument | List-form args, no `shell=True`, pre-validated input |
| `logger.*` call with any user-supplied value | Wrap each user-supplied argument with `log_safe(v)` from `quadletman.models.sanitized` — prevents log-injection (CodeQL `py/log-injection`) |
| New service function that calls `host.*` | Parameter is `SafeSlug` / `SafeStr` / `SafeUnitName` / `SafeSecretName`; add `@sanitized.enforce` (innermost decorator); callers use `.of()` — never `.trusted()` in production |
| Cookie or session logic | `httponly`, `samesite="strict"`, `secure=settings.secure_cookies`, absolute TTL |
| New JS `fetch()` or HTMX mutating request | `X-CSRF-Token: getCsrfToken()` header included |
| New WebSocket endpoint | Origin header validated against `Host`; session cookie validated manually |

### Per-category checks

**New routes**
- Is the route protected by `Depends(require_auth)`?
- For POST/PUT/DELETE: does the JS caller send `X-CSRF-Token: getCsrfToken()`?
  Plain `fetch` calls → use the `jsonFetch` helper or add the header explicitly.
  HTMX requests → pass via `hx-headers`.

**WebSocket routes**
- `CSRFMiddleware` does NOT cover WebSocket upgrades (they use HTTP GET, a safe method).
- Validate the `Origin` header against the `Host` header — the browser always sets `Origin`
  on WebSocket upgrades and JavaScript cannot spoof it. Reject mismatches with close code 4403.
- Validate the `qm_session` cookie manually via `get_session()` (FastAPI's `Depends()` works
  differently for WebSocket routes).

**User input → filesystem**
- Path resolved with `resolve_safe_path()` before use?
- Final write uses `os.open(O_NOFOLLOW)` to block symlink-swap attacks?
- Filename from an HTTP client sanitised with `re.sub(r"[^\w.\-]", "_", ...)`?

**Pydantic models**
- Strings reaching unit files or shell commands: `_no_control_chars()` applied?
- Image references validated against `IMAGE_RE`?
- Bind-mount `host_path` checked against `_BIND_MOUNT_DENYLIST`?

**subprocess**
- `cmd` always a list — never `shell=True` with user-controlled data?
- Variables pre-validated by the slug/control-char validators before reaching
  `systemd_manager.py`?

**Archive extraction**
- Using `_extract_zip` / `_extract_tar` helpers in `services/archive.py`, not raw `extractall`?

## Testing
Run `uv run pytest` (never as root — the suite guards against this).

Test layout under `tests/`:
- `test_models.py`, `test_bundle_parser.py`, `test_podman_version.py` — pure logic, no mocks needed
- `services/` — service-layer tests with all subprocess/os calls mocked via `pytest-mock`
- `routers/` — HTTP route tests using `httpx.AsyncClient` + `ASGITransport`; auth and DB are
  overridden via FastAPI `dependency_overrides`
- `e2e/` — Playwright browser tests against a live server; run with `uv run pytest tests/e2e`
  (excluded from the default `uv run pytest` run to avoid event loop conflicts with pytest-asyncio)
- `js/` — Vitest unit tests for pure JS logic; run with `npm test` (requires Node 20+)

**Key rule (Python):** every test that touches code which would call `subprocess.run`, `os.chown`,
`pwd.getpwnam`, or similar system APIs must mock those calls. Tests must not create Linux
users, touch `/var/lib/`, call `systemctl`, or write outside `/tmp`.

**JS tests:** source files are loaded into the jsdom global context via `window.eval` — no
source changes needed. Add tests for any pure function in `static/src/`. DOM-heavy code
(HTMX handlers, modal wiring) is covered by E2E tests instead.

## Doc Update Protocol

Before committing any change, run through this checklist. These docs must be kept accurate;
AI assistants are the primary developers and are responsible for updating them.

### Triggers — update docs when you change any of the following

| What changed | Update these files |
|---|---|
| Release process, branch conventions, or versioning scheme changed | `docs/ways-of-working.md` |
| New source file added or renamed/deleted | CLAUDE.md Key Files table |
| File purpose significantly changed | CLAUDE.md Key Files table |
| New service added under `quadletman/services/` | CLAUDE.md Key Files table + README.md Features |
| Architecture changed (file paths, user model, DB location, volume paths, systemd invocation) | `docs/architecture.md` + README.md Architecture blurb |
| New or changed dev command | CLAUDE.md Dev Commands + `docs/development.md` |
| Test suite added, removed, or conventions changed | CLAUDE.md Testing + `docs/development.md` Testing + `docs/testing.md` |
| Vagrant VM or smoke-test script changed | `docs/packaging.md` Smoke testing section |
| New code pattern established or existing pattern changed | CLAUDE.md Code Patterns |
| New host-mutating operation added to a service file | CLAUDE.md Host Mutation Tracking checklist |
| New branded type added to `models/sanitized.py` or `require()` pattern added to a service | CLAUDE.md Defense-in-depth pattern + Key Files table |
| New path sanitizer added to `models/sanitized.py` | CLAUDE.md CodeQL Path Sanitizers + `.github/codeql/extensions/path-sanitizers.yml` + `docs/development.md` CodeQL path sanitizers section |
| New "do not do" constraint | CLAUDE.md What NOT to Do |
| Security model change (auth, CSRF, headers, cookie settings, validation, file ops) | CLAUDE.md Security Notes + Security Review Checklist + README.md Security Notes |
| New end-user-visible feature | docs/features.md + README.md blurb |
| Installation procedure changed | README.md Installation + `docs/runbook.md` After Installation |
| Operational procedure changed (start/stop/backup/upgrade/uninstall) | `docs/runbook.md` |
| New requirement (Python version, system dep, Podman version) | README.md Requirements |
| New env var, config file, or runtime path | README.md Configuration + `docs/architecture.md` if internal |
| New Podman version requirement added | `podman_version.py` + CLAUDE.md Podman Version Gating + README.md Features |
| ORM model changed or new table added | `quadletman/db/orm.py` + new Alembic revision + `docs/development.md` Existing revisions table |
| Defense-in-depth contract changed (new layer, new rule, new exception) | CLAUDE.md Code Patterns defense-in-depth section + `docs/development.md` defense-in-depth section |
| New user-visible string added or existing string changed | Run pybabel extract → update → compile; update Finnish `.po` per `docs/localization.md` vocabulary |
| Finnish vocabulary term added or corrected | `docs/localization.md` Finnish vocabulary table |
| New language added | `quadletman/i18n.py` `AVAILABLE_LANGS` + `docs/localization.md` |
| New modal added to `base.html` or any template | Use `modal_shell` macro; update `docs/ui-development.md` if new variant needed |
| New `x-show` / `x-cloak` section added | Add `x-transition` attributes per `docs/ui-development.md` |
| New form input group added | Use `form_field` macro if it's a standard label+input |
| Podman release monitor script or workflow changed | `docs/product_development.md` |

### Pre-commit checklist

1. Is any row in the trigger table above affected? If no, skip the rest.
2. Open each doc listed for the triggered rows and read the relevant section.
3. Edit the doc to reflect the current state of the code. Remove stale information.
4. Stage the updated doc files in the same commit as the code change.

### Source of truth

- `CLAUDE.md` — primary reference for AI developers. All other AI files defer to it.
- `README.md` — short user-facing overview: features, requirements, installation, configuration,
  security notes, and links to the `docs/` files.
- `docs/runbook.md` — operator guide: post-install setup, day-to-day operations, troubleshooting, upgrade, uninstall.
- `docs/architecture.md` — internal architecture detail (compartments, users, Quadlet files, volumes).
- `docs/development.md` — contributor guide: setup, running locally, testing, migrations.
- `docs/testing.md` — test suite conventions and smoke-test VMs (Vagrant + Fedora + Ubuntu).
- `docs/packaging.md` — package architecture, build scripts, CI release builds, upgrade instructions, smoke-test VMs.
- `docs/features.md` — full feature breakdown: compartments, containers, volumes, scheduling, monitoring.
- `docs/ways-of-working.md` — branch strategy, PR process, CI pipeline, versioning, release process.
- `docs/ui-development.md` — full UI reference: state management, macros, conventions, patterns.
- `docs/localization.md` — localization workflow, Finnish vocabulary, adding new languages.
- `docs/product_development.md` — Podman release monitor, community monitoring, product development tooling.
- `AGENTS.md` — pointer to CLAUDE.md. Only update if the pointer itself is wrong.
- `.github/copilot-instructions.md` — coding hints. Update only if a core pattern changes.
