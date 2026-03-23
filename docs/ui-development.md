# UI Development

The frontend is built with Jinja2 templates, [Tailwind CSS](https://tailwindcss.com/)
(vendored, pre-built), [HTMX](https://htmx.org/), and [Alpine.js](https://alpinejs.dev/).
Third-party JS/CSS assets live in `quadletman/static/vendor/`; first-party project assets
live in `quadletman/static/src/`. No external hosts are referenced at runtime.

## JavaScript modules

Load order in `base.html`:

| Module | Purpose |
|---|---|
| `metrics.js` | `drawSparkline`, `fmtBytes`, `setText` — sparkline charts and byte formatting |
| `requests.js` | `getCsrfToken`, `jsonFetch`, HTMX CSRF injection, 401 redirect handler |
| `modals.js` | `showModal`, `hideModal`, `showToast`, `_htmxModal`, all named modal openers |
| `navigation.js` | `loadDashboard`, `loadCompartment`, `loadHelp`, `loadEvents`, `initFromUrl`, popstate handler |
| `logs.js` | `showLogs`, `showJournalXE`, `stopLogs` — SSE-based log streaming |
| `terminal.js` | `showTerminal`, `_openTerminal`, xterm.js + WebSocket terminal |
| `container-form.js` | `containerForm` Alpine component for container create/edit modal |
| `app.js` | `t()` i18n helper, `chmodEditor` Alpine component, DOMContentLoaded event handlers |

## UI State Management

The frontend uses three layers with distinct responsibilities:

**URL state** — the canonical record of which main view is active. Navigation functions
(`loadCompartment`, `loadDashboard`, etc.) in `navigation.js` call `history.pushState` then
use `htmx.ajax()` to load the view partial into `#main-content`. Hard reloads work because
each navigable URL has a corresponding SPA-fallback route in `routers/ui.py`; on load,
`initFromUrl()` reads the path and fires the right navigation function. Browser back/forward
is handled by a `popstate` listener. Ephemeral overlays (modals, log viewer, terminal) are
**not** encoded in the URL — on reload the user lands on the underlying view without the
overlay.

| URL | View |
|-----|------|
| `/` | Dashboard (compartment list) |
| `/compartments/{id}` | Compartment detail |
| `/help` | Help page |
| `/events` | Event log |

**Alpine.js component state** — ephemeral state that lives only as long as the DOM element
it is bound to. Simple sections use inline `x-data="{ showForm: false }"` for disclosure
toggles. Complex forms (e.g. the container create/edit modal) use a factory function defined
in `container-form.js` that manages tab selection, image source, and dynamic field lists.
State is hydrated from a server-rendered `data-init` JSON attribute so no extra XHR is
needed. When HTMX replaces a DOM element the Alpine instance on it is destroyed and a new
one initialises on the incoming HTML — form state is therefore ephemeral within a view load.

**HTMX** — declarative server-driven partial updates. `#main-content` is the swap target
for navigable views; `#compartment-list` in the sidebar is reloaded after CRUD operations;
modal content wrappers are loaded just-in-time before the modal is revealed. Lifecycle
buttons (Start / Stop / Restart) POST to the server and receive a fresh compartment partial
that replaces `#main-content`.

Summary:

| State | Layer | Lifetime |
|-------|-------|----------|
| Which main view is active | URL + `history.pushState` | Persistent — survives reload |
| Form fields, tab selection, toggles | Alpine `x-data` | Ephemeral — lost on DOM swap |
| Modal open / closed | JS `hidden` class toggle | Ephemeral |
| Sidebar compartment list | HTMX `hx-trigger` reload | Server-managed |

## Semantic component classes

Recurring utility combinations are extracted into named `@layer components` classes in
`static/src/app.css`. Each class has an inline comment describing when to use it. **Always
use these instead of repeating the raw Tailwind utilities.**

After changing `app.css` or adding new utility classes to any template, rebuild:

```bash
TAILWINDCSS_VERSION=v4.2.2 uv run tailwindcss -i quadletman/static/src/app.css \
  -o quadletman/static/src/tailwind.css --minify
```

The `TAILWINDCSS_VERSION` env var must match the version pinned in `ci.yml` and
`.pre-commit-config.yaml`. When upgrading Tailwind, update all three places together.

Commit both `app.css` and `tailwind.css` together.

**When to add a new component class** — if the same utility combination appears in three or
more places (even across different templates), extract it into `app.css` with a `qm-` prefix
and a `/* ... */` use-case comment.

**When reviewing existing templates** — if you find a repeated non-semantic utility string
that matches a `qm-*` class, replace it. If you find a repeated pattern that has no `qm-*`
class yet, first add the class, then use it. Do not leave raw utility repetition when a
semantic name exists.

## Macros (`quadletman/templates/macros/ui.html`)

Import shared macros at the top of any template that needs them:

```jinja2
{% from "macros/ui.html" import modal_shell, form_field %}
```

All macros are documented inline in the macro file. Quick-reference index:

| Macro | Use for |
|---|---|
| `modal_shell(modal_id, title, max_width, extra_panel_classes, z_index)` | Every new dialog modal — renders backdrop, panel, header, × button |
| `modal_header(title, modal_id)` | Header bar only, for modals whose body is loaded via HTMX into a pre-existing shell |
| `form_field(label, name, type, ...)` | Standard `<label> + <input/textarea/select>` groups in forms |
| `fade_attrs()` | Inline `x-transition` attributes for implicit-reveal `x-show` blocks |
| `disclosure_card(title, description, add_text, ...)` | Section card with toggle button + collapsible inline form (replaces raw Alpine `x-show` pattern) |
| `string_list(label, array_var, ...)` | Dynamic single-value list managed by Alpine `x-for` |
| `pair_list(label, array_var, ...)` | Dynamic key=value pair list managed by Alpine `x-for` |
| `config_entry(key, description, on_submit, range_hint)` | Key-value settings row with inline edit form |
| `dot_color(state)` | Maps a systemd `active_state` string to a Tailwind `bg-*` color class |
| `tab_button(number, label)` | Single tab navigation button inside a fixed-height modal |
| `tab_panel(number)` | Wrapper `<div>` for a tab panel body inside a fixed-height modal |
| `select_choices(choices, current_value)` | Renders `<option>` elements from a template-ready choices list produced by `choices_for_template()` or `field_choices_for_template()` — use inside `form_field(..., type="select")` call blocks or bare `<select>` elements |

**`modal_shell`** — use for every new dialog modal:

```jinja2
{% call modal_shell("my-modal", "My Title", max_width="max-w-md") %}
  <div class="p-6 space-y-4">...body...</div>
  <div class="flex items-center justify-end gap-3 px-5 py-3 border-t border-gray-700">
    <button onclick="hideModal('my-modal')" class="qm-btn-cancel">Cancel</button>
    <button class="qm-btn-confirm">Confirm</button>
  </div>
{% endcall %}
```

Exception: `log-modal` is a bottom sheet (`bg-gray-900`, `items-end`, `h-96`) — do NOT use
`modal_shell` for it.

**`form_field`** — use for standard `<label> + <input>` groups in forms. For `type="select"`,
pass `<option>` elements in the `{% call %}` block. See macro file for full parameter list.

## Button sizes

Four contexts — inline Tailwind, no macro:

| Context | Classes |
|---|---|
| Compact — sidebar + section-header action buttons | `text-xs px-2 py-1 rounded transition` |
| Action — service lifecycle buttons (Start/Stop/Restart/Delete) | `px-3 py-1.5 text-sm rounded transition` |
| Modal-footer — dialog confirm/cancel | `px-4 py-2 text-sm rounded transition` |
| List row — neutral inline actions (Logs, Edit, Files) | `text-xs text-gray-400 hover:text-white border border-gray-600 hover:border-gray-400 px-2 py-1 rounded transition` |
| List row — destructive inline action (Remove, Delete) | `text-xs text-red-400 hover:text-red-300 border border-red-800 hover:border-red-600 px-2 py-1 rounded transition` |

## Inline disclosure forms

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

## Form inputs — always use labels

Every form input must have a visible `<label>` element. Placeholders alone are not sufficient
— they disappear when the user starts typing. In compact inline forms (e.g. registry logins)
use `text-xs text-gray-400 mb-1` for the label; placeholder text may be retained as an
additional hint.

## Destructive actions — confirmation required

Every action that is irreversible or disruptive must carry `hx-confirm` or an equivalent
confirmation step. This applies to:
- Deleting any resource (service, container, volume, file)
- Stopping all running containers
- Logging out from a container registry (may interrupt image pulls)

Reversible actions (Start, Restart, Enable/Disable autostart) do not require confirmation.

## `x-show` / `x-cloak` rule

Whether to add fade transitions depends on whether the reveal is **implicit** or **explicit**:

**Implicit reveal** — content appears as a side-effect of a state change the user didn't aim
at the content directly (disclosure toggle, inline form expand, conditional helper text).
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

## Alpine `:class` pre-boot flash rule

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

## Scrollbar gutter rule

Every `overflow-y-auto` container that can grow to viewport-fraction height must carry
`style="scrollbar-gutter: stable"` to prevent content shift when the scrollbar appears.

## Modal sizing

Choose the height strategy based on whether the modal content can change height after opening:

| Strategy | Classes | When to use |
|---|---|---|
| Content-fit | *(no height class)* | Small, predictable forms — `create-compartment`, `add-volume`, `import` |
| Bounded-scroll | `max-h-[92vh]` on panel + `overflow-y-auto` + `scrollbar-gutter:stable` on scroll body | Large forms or HTMX-loaded content that scrolls vertically but doesn't swap panels |
| Fixed | `h-[88vh]` on panel + `overflow-y-auto` + `scrollbar-gutter:stable` on scroll body | Modals with tabs or swapped panels — fixed height prevents jumping when panels have different heights |
| Bottom-sheet | `h-96` fixed, full-width, `items-end` backdrop | Log viewer only — do not use for dialog modals |

Rule of thumb: if the user can trigger a height change *after* the modal is open (by clicking
a tab, expanding a section, or via an HTMX update), use **Fixed**. If content only scrolls
vertically without layout-affecting changes, use **Bounded-scroll**. If the form is short
and static, use **Content-fit**.

## Fixed-height modal internal scrolling

For **Fixed** modals (tabs or swapped panels), the scrollable body must use `flex-1 min-h-0
overflow-y-auto` so it expands into the panel's fixed height rather than sizing to its own
content. Never place `overflow-y-auto` on the HTMX content-target wrapper itself — only on
the innermost scroll region inside the loaded partial.

## State-aware compartment action buttons

Compartment lifecycle buttons in `compartment_detail.html` are conditionally shown based on
the aggregate running state of the service's containers. Use the `ns` namespace pattern to
compute `any_running` / `none_running` from `statuses` at render time:

| Button | Show when |
|---|---|
| Start All | `has_containers and none_running` |
| Stop All | `has_containers and any_running` — add `hx-confirm` |
| Restart | `has_containers` — always valid |

Buttons cause a full `#main-content` reload, so state is always fresh after any action.

## Action button hierarchy

Three tiers in the service detail action row, separated by `<span class="w-px h-5
bg-gray-700 self-center">` dividers:

1. **Primary** (lifecycle): Start All / Stop All / Restart — green/yellow/blue backgrounds
2. **Secondary** (config/debug): Enable autostart / Disable autostart / Files — `bg-gray-700`
3. **Destructive**: Delete — `bg-red-800`, always last

## List row button order

Buttons in list rows (Containers, Volumes) follow a fixed left-to-right order:

1. **Primary action** — modifies the item (Edit)
2. **Secondary read action** — inspects the item without changing it (Logs, Files)
3. **Destructive action** — removes the item (Remove, Delete) — always last

This order keeps the most commonly used action closest to the item label and puts the
dangerous action furthest away, reducing accidental clicks. Apply this order consistently
across all list rows regardless of how many buttons are present.

## Disabled button state

Use `<button disabled>` — never `<span>` — for conditionally unavailable actions. Disabled
buttons remain in the accessibility tree and allow `title` tooltip explanations. Style: add
`opacity-50 cursor-not-allowed` to the normal button classes; do not change the border/text
color (preserves color-coded meaning).

```html
<button disabled
        title="Reason it is disabled"
        class="text-xs text-red-400 border border-red-800 px-2 py-1 rounded opacity-50 cursor-not-allowed">
  Delete
</button>
```

## Section header descriptions

Section headers may include a one-line description for technically complex or
quadletman-specific concepts. Add it as `<p class="text-xs text-gray-500 mt-0.5">` below
the `<h3>` inside the header bar, wrapping both in a `<div>`.

Add a description when either condition is true:
1. The concept is quadletman-specific and non-obvious (e.g. Registry Logins, Helper Users).
2. The section name is a generic computing term with multiple common meanings and the
   quadletman-specific meaning needs anchoring.

Use descriptions for: **Registry Logins**, **Helper Users**, **Volumes**.
Do not add for: Containers (universally understood in this context).

**Tone of voice:** Describe the concrete effect on the user's containers, not the underlying
mechanism. Avoid specialist terms (namespace, IPC, cgroup, unit file) unless there is no
plain-English substitute.

```html
<div class="flex items-center justify-between px-5 py-3 border-b border-gray-700">
  <div>
    <h3 class="font-medium">Section Title</h3>
    <p class="text-xs text-gray-500 mt-0.5">One-line explanation of what this section does.</p>
  </div>
  <!-- optional action button or badge here -->
</div>
```

## Section visibility rule

- Show a section with an empty-state CTA when the user can take an action to populate it
  (Containers, Volumes).
- Always show sections that have a user-facing add/manage workflow regardless of whether
  they have content (Registry Logins, Containers, Volumes).
- Hide a section entirely when it is auto-populated and has no user-initiated action
  (Helper Users — shown only when `helper_users` is non-empty).
- Auto-managed sections (Helper Users) carry an `auto-managed` badge in their header to
  signal that no actions are available.

## Modal close button rule

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
