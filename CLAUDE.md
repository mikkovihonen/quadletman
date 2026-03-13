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
uv run tailwindcss -i quadletman/static/vendor/input.css \
  -o quadletman/static/vendor/tailwind.css --minify
                                  # rebuild Tailwind CSS — re-run after adding new utility
                                  # classes to any template; commit the output file
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
| `quadletman/templates/macros/ui.html` | Jinja2 macros: `modal_shell`, `form_field` — use for all new modals and form inputs |
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

## Podman Version Gating

Every feature with a minimum Podman version requirement must be guarded at all three layers:

1. **Flag in `PodmanFeatures`** (`podman_version.py`): Add a boolean field with a comment
   stating the minimum version. Set it in `get_features()`:
   ```python
   new_feature: bool  # >= X.Y.0 — short description
   # in get_features():
   new_feature=version is not None and version >= (X, Y, 0),
   ```

2. **Server-side guard** (`routers/api.py`): At the top of every route that uses the feature,
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

## UI Conventions

All UI components are Jinja2 templates using Tailwind CSS (vendored, pre-built), HTMX, and
Alpine.js. All JS/CSS assets are vendored in `quadletman/static/vendor/` — no external hosts
are referenced at runtime. Import shared macros at the top of any template that needs them:

```jinja2
{% from "macros/ui.html" import modal_shell, form_field %}
```

### Macros (`quadletman/templates/macros/ui.html`)

**`modal_shell(modal_id, title, max_width, extra_panel_classes, z_index)`**
Use for every new dialog modal. Renders the backdrop, panel, header, and close button.
The `{% call %}` block provides the modal body and footer.

```jinja2
{% call modal_shell("my-modal", "My Title", max_width="max-w-md") %}
  <div class="p-6 space-y-4">...body...</div>
  <div class="flex justify-end gap-3 px-5 py-3 border-t border-gray-700">
    <button onclick="hideModal('my-modal')"
            class="px-4 py-2 text-sm text-gray-400 hover:text-white transition">Cancel</button>
    <button class="px-4 py-2 text-sm bg-blue-600 hover:bg-blue-500 text-white rounded transition">
      Confirm
    </button>
  </div>
{% endcall %}
```

Exception: `log-modal` is a bottom sheet (`bg-gray-900`, `items-end`, `h-96`) — do NOT use
`modal_shell` for it.

**`form_field(label, name, type, placeholder, required, help_text, extra_input_classes,
x_model, value, disabled)`**
Use for standard `<label> + <input>` groups in forms. For `type="select"`, pass `<option>`
elements in the `{% call %}` block. See macro file for full parameter list.

### Button sizes (four contexts — inline Tailwind, no macro)

| Context | Classes |
|---|---|
| Compact — sidebar + section-header action buttons | `text-xs px-2 py-1 rounded transition` |
| Action — service lifecycle buttons (Start/Stop/Restart/Delete) | `px-3 py-1.5 text-sm rounded transition` |
| Modal-footer — dialog confirm/cancel | `px-4 py-2 text-sm rounded transition` |
| List row — neutral inline actions (Logs, Edit, Files) | `text-xs text-gray-400 hover:text-white border border-gray-600 hover:border-gray-400 px-2 py-1 rounded transition` |
| List row — destructive inline action (Remove, Delete) | `text-xs text-red-400 hover:text-red-300 border border-red-800 hover:border-red-600 px-2 py-1 rounded transition` |

### Inline disclosure forms (section-body expandable)

Use Alpine `x-show` with the standard fade transition — **never `<details>`**. The native
`<details>` element opens without animation, causing an abrupt layout shift.

The `+ Add …` / `– Cancel` toggle button belongs in the **section header bar** (consistent
with Containers and Volumes sections). Keep the Alpine state (`showForm`) on the root element
of the HTMX-loaded partial so the whole card re-initialises correctly after a swap.

```html
<div id="my-section" class="bg-gray-800 rounded-xl border border-gray-700" x-data="{ showForm: false }">
  <div class="flex items-center justify-between px-5 py-3 border-b border-gray-700">
    <h3 class="font-medium">Section Title</h3>
    <button type="button" @click="showForm = !showForm"
            class="text-xs bg-blue-600 hover:bg-blue-500 text-white px-2 py-1 rounded transition"
            x-text="showForm ? '– Cancel' : '+ Add item'"></button>
  </div>
  <div class="px-5 py-4 space-y-3">
    <!-- list content -->
    <div x-show="showForm" x-cloak
         x-transition:enter="transition ease-out duration-150"
         ...>
      <form ...>...</form>
    </div>
  </div>
</div>
```

When the partial owns its section header, load it with `hx-swap="outerHTML"` so the card
(including header) is replaced atomically. The placeholder in the parent template must carry
the same `id` so the swap target resolves before the partial arrives.

### Form inputs — always use labels

Every form input must have a visible `<label>` element. Placeholders alone are not
sufficient — they disappear when the user starts typing. In compact inline forms (e.g.
registry logins) use `text-xs text-gray-400 mb-1` for the label; placeholder text may be
retained as an additional hint.

### Destructive actions — confirmation required

Every action that is irreversible or disruptive must carry `hx-confirm` or an equivalent
confirmation step. This applies to:
- Deleting any resource (service, container, volume, file)
- Stopping all running containers
- Logging out from a container registry (may interrupt image pulls)

Reversible actions (Start, Restart, Enable/Disable autostart) do not require confirmation.

### `x-show` / `x-cloak` rule

Whether to add fade transitions depends on whether the reveal is **implicit** or **explicit**:

**Implicit reveal** — content appears as a side-effect of a state change the user didn't
aim at the content directly (disclosure toggle, inline form expand, conditional helper text).
Always add fade transitions:

```html
x-show="flag" x-cloak
x-transition:enter="transition ease-out duration-150"
x-transition:enter-start="opacity-0"
x-transition:enter-end="opacity-100"
x-transition:leave="transition ease-in duration-100"
x-transition:leave-start="opacity-100"
x-transition:leave-end="opacity-0"
```

**Explicit switch** — the user directly selected the content to display (tab panels, wizard
steps). No transitions — use only `x-show` and `x-cloak`. Animating an explicit selection
delays feedback and adds visual noise:

```html
x-show="activeTab === N" x-cloak
```

### Alpine `:class` pre-boot flash rule

`x-show`/`x-cloak` suppresses an element before Alpine boots. `:class` bindings have no
equivalent — the static `class` is all that exists until Alpine initialises. If an element
is hidden in its initial Alpine state via a `:class` binding (e.g. `opacity-0`), that class
**must also appear in the static `class`** so the pre-boot render matches the post-boot
initial state and avoids a visible flash.

```html
<!-- BAD: opacity-0 only in :class — element flashes visible before Alpine boots -->
<button :class="active ? 'opacity-100' : 'opacity-0'"
        class="...">

<!-- GOOD: opacity-0 also in static class — starts hidden, Alpine takes over immediately -->
<button :class="active ? 'opacity-100' : 'opacity-0'"
        class="... opacity-0">
```

This applies to any CSS property used to hide an element: `opacity-0`, `hidden`, `invisible`, etc.

### Scrollbar gutter rule

Every `overflow-y-auto` container that can grow to viewport-fraction height must carry
`style="scrollbar-gutter: stable"` to prevent content shift when the scrollbar appears.

### Modal sizing

Choose the height strategy based on whether the modal content can change height after opening:

| Strategy | Classes | When to use |
|---|---|---|
| Content-fit | *(no height class)* | Small, predictable forms — `create-service`, `add-volume`, `import` |
| Bounded-scroll | `max-h-[92vh]` on panel + `overflow-y-auto` + `scrollbar-gutter:stable` on scroll body | Large forms or HTMX-loaded content that scrolls vertically but doesn't swap panels |
| Fixed | `h-[88vh]` on panel + `overflow-y-auto` + `scrollbar-gutter:stable` on scroll body | Modals with tabs or swapped panels — fixed height prevents jumping when panels have different heights |
| Bottom-sheet | `h-96` fixed, full-width, `items-end` backdrop | Log viewer only — do not use for dialog modals |

Rule of thumb: if the user can trigger a height change *after* the modal is open (by clicking
a tab, expanding a section, or via an HTMX update), use **Fixed**. If content only scrolls
vertically without layout-affecting changes, use **Bounded-scroll**. If the form is short
and static, use **Content-fit**.

### Fixed-height modal internal scrolling

For **Fixed** modals (tabs or swapped panels), the scrollable body must use `flex-1 min-h-0
overflow-y-auto` so it expands into the panel's fixed height rather than sizing to its own
content. Never place `overflow-y-auto` on the HTMX content-target wrapper itself — only on
the innermost scroll region inside the loaded partial.

### State-aware service action buttons

Service lifecycle buttons in `service_detail.html` are conditionally shown based on the
aggregate running state of the service's containers. Use the `ns` namespace pattern to
compute `any_running` / `none_running` from `statuses` at render time:

| Button | Show when |
|---|---|
| Start All | `has_containers and none_running` |
| Stop All | `has_containers and any_running` — add `hx-confirm` |
| Restart | `has_containers` — always valid |

Buttons cause a full `#main-content` reload, so state is always fresh after any action.

### Action button hierarchy

Three tiers in the service detail action row, separated by `<span class="w-px h-5
bg-gray-700 self-center">` dividers:

1. **Primary** (lifecycle): Start All / Stop All / Restart — green/yellow/blue backgrounds
2. **Secondary** (config/debug): Enable autostart / Disable autostart / Files — `bg-gray-700`
3. **Destructive**: Delete — `bg-red-800`, always last

### List row button order

Buttons in list rows (Containers, Volumes) follow a fixed left-to-right order:

1. **Primary action** — modifies the item (Edit)
2. **Secondary read action** — inspects the item without changing it (Logs, Files)
3. **Destructive action** — removes the item (Remove, Delete) — always last

This order keeps the most commonly used action closest to the item label and puts the
dangerous action furthest away, reducing accidental clicks. Apply this order consistently
across all list rows regardless of how many buttons are present.

### Disabled button state

Use `<button disabled>` — never `<span>` — for conditionally unavailable actions.
Disabled buttons remain in the accessibility tree and allow `title` tooltip explanations.
Style: add `opacity-50 cursor-not-allowed` to the normal button classes; do not change
the border/text color (preserves color-coded meaning).

```html
<button disabled
        title="Reason it is disabled"
        class="text-xs text-red-400 border border-red-800 px-2 py-1 rounded opacity-50 cursor-not-allowed">
  Delete
</button>
```

### Section header descriptions

Section headers may include a one-line description for technically complex or
quadletman-specific concepts. Add it as `<p class="text-xs text-gray-500 mt-0.5">` below
the `<h3>` inside the header bar, wrapping both in a `<div>`. The slight height variation
between described and plain headers is acceptable.

Add a description when either condition is true:
1. The concept is quadletman-specific and non-obvious (e.g. Registry Logins, Helper Users).
2. The section name is a generic computing term with multiple common meanings and the
   quadletman-specific meaning needs anchoring (e.g. "Volumes" — could mean Docker-managed
   volumes, cloud block storage, or filesystem mounts; here it means host directories managed
   by this service).

Use descriptions for: **Registry Logins**, **Helper Users**, **Volumes**.
Do not add for: Containers (universally understood in this context).

```html
<div class="flex items-center justify-between px-5 py-3 border-b border-gray-700">
  <div>
    <h3 class="font-medium">Section Title</h3>
    <p class="text-xs text-gray-500 mt-0.5">One-line explanation of what this section does.</p>
  </div>
  <!-- optional action button or badge here -->
</div>
```

### Section visibility rule

- Show a section with an empty-state CTA when the user can take an action to populate it
  (Containers, Volumes).
- Always show sections that have a user-facing add/manage workflow regardless of whether
  they have content (Registry Logins, Containers, Volumes).
- Hide a section entirely when it is auto-populated and has no user-initiated action
  (Helper Users — shown only when `helper_users` is non-empty).
- Auto-managed sections (Helper Users) carry an `auto-managed` badge in their header to
  signal that no actions are available.

### Modal close button rule

Every modal **must** have a × close button in the top-right corner of the header:

```html
<button onclick="hideModal('my-modal-id')"
        class="text-gray-400 hover:text-white text-xl leading-none">&times;</button>
```

- Modals using `modal_shell` get this automatically.
- Modals whose headers live in HTMX-loaded partials (`container_form.html`, `volume_form.html`)
  include the button directly in the partial — the modal ID is fixed and known.
- Form modals with a footer Cancel button must **still** include the × button — users expect
  to close dialogs from the top-right regardless of footer controls.

## What NOT to Do
- Do not write to the DB directly — always go through `service_manager.py`
- Do not skip pre-commit hooks (`--no-verify`)
- Do not use bare `open(path).read()` without a context manager
- Do not use `try/except/pass` — use `contextlib.suppress()`
- Do not add `from __future__ import annotations` — the project targets Python 3.11+ natively
- Do not place imports inside functions or conditionally — all imports belong at the top of the file
- Do not add `<script src="...">` or `<link href="...">` pointing to any external host — all
  JS/CSS assets must be vendored in `quadletman/static/vendor/` and referenced as `/static/vendor/...`

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
- Container image references validated against `_IMAGE_RE` in `models.py`
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
(`routers/`, `auth.py`, `main.py`, `models.py`, `services/`, `session.py`, `database.py`).

### Triggers — run the relevant checks when you change:

| What changed | Checks to run |
|---|---|
| New HTTP route (any method) | Auth dependency, CSRF for mutating methods, input validation |
| User-supplied value reaches filesystem | Path traversal (`_resolve_vol_path`), `O_NOFOLLOW` on writes |
| New Pydantic model field | `_no_control_chars`, format/length constraints |
| File upload or archive handling | Filename sanitisation, zip-slip guards, `_MAX_UPLOAD_BYTES` cap |
| `subprocess` call with any variable argument | List-form args, no `shell=True`, pre-validated input |
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
- Path resolved with `_resolve_vol_path()` before use?
- Final write uses `os.open(O_NOFOLLOW)` to block symlink-swap attacks?
- Filename from an HTTP client sanitised with `re.sub(r"[^\w.\-]", "_", ...)`?

**Pydantic models**
- Strings reaching unit files or shell commands: `_no_control_chars()` applied?
- Image references validated against `_IMAGE_RE`?
- Bind-mount `host_path` checked against `_BIND_MOUNT_DENYLIST`?

**subprocess**
- `cmd` always a list — never `shell=True` with user-controlled data?
- Variables pre-validated by the slug/control-char validators before reaching
  `systemd_manager.py`?

**Archive extraction**
- Using `_extract_zip` / `_extract_tar` helpers in `api.py`, not raw `extractall`?

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
| Security model change (auth, CSRF, headers, cookie settings, validation, file ops) | CLAUDE.md Security Notes + Security Review Checklist + README.md Security Notes |
| New end-user-visible feature | README.md Features |
| Installation procedure changed | README.md Installation |
| New requirement (Python version, system dep, Podman version) | README.md Requirements |
| New env var, config file, or runtime path | README.md Configuration + CLAUDE.md Architecture if internal |
| New Podman version requirement added | `podman_version.py` + CLAUDE.md Podman Version Gating + README.md Features |
| New modal added to `base.html` or any template | Use `modal_shell` macro; update CLAUDE.md UI Conventions if new variant needed |
| New `x-show` / `x-cloak` section added | Add `x-transition` attributes per UI Conventions |
| New form input group added | Use `form_field` macro if it's a standard label+input |

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
| Security Review Checklist | Contributing → Security review |
| UI Conventions | Contributing → UI conventions |
