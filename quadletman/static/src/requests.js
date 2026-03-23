// CSRF / fetch helpers and HTMX integration

// Read the CSRF token from the qm_csrf cookie (set by the server on login)
function getCsrfToken() {
  const entry = document.cookie.split('; ').find(r => r.startsWith('qm_csrf='));
  if (!entry) return '';
  try { return decodeURIComponent(entry.split('=')[1]); } catch { return ''; }
}

// Extract a human-readable error message from a FastAPI error response body.
// Handles both string detail (HTTPException) and array detail (422 validation).
// Returns an object {msg, html} — html is true when the message contains markup.
function formatApiError(data, fallback) {
  if (!data || !data.detail) return { msg: fallback || '', html: false };
  if (Array.isArray(data.detail)) {
    // Escape HTML in field names and messages to prevent injection.
    const esc = s => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
    const lines = data.detail.map(d => {
      // loc is e.g. ["body", "ports", 0] — build a readable label.
      // Skip generic prefixes ("body", "query", "path").
      // Convert numeric indices to 1-based: ["ports", 0] → "ports (item 1)".
      const parts = Array.isArray(d.loc)
        ? d.loc.filter(p => p !== 'body' && p !== 'query' && p !== 'path')
        : [];
      let field = '';
      if (parts.length) {
        const names = parts.filter(p => typeof p !== 'number');
        const idx = parts.find(p => typeof p === 'number');
        field = names.join('.');
        if (idx !== undefined) field += ' (item ' + (idx + 1) + ')';
      }
      return field
        ? '<b>' + esc(String(field)) + '</b>: ' + esc(d.msg)
        : esc(d.msg);
    });
    return { msg: lines.join('<br>'), html: true };
  }
  return { msg: data.detail, html: false };
}

// Show an API error as a toast, using HTML rendering for multi-field validation errors.
function showApiError(data, fallback) {
  const e = formatApiError(data, fallback);
  showToast(e.msg, 'error', { html: e.html });
}

// ---------------------------------------------------------------------------
// Inline field validation errors
// ---------------------------------------------------------------------------

// Remove all inline field errors and tab error indicators from a form.
function clearFieldErrors(formEl) {
  for (const el of formEl.querySelectorAll('.qm-field-error')) el.remove();
  for (const el of formEl.querySelectorAll('.qm-tab-error-dot')) el.classList.add('hidden');
}

// Show inline validation errors next to the corresponding form fields.
// Also marks tab buttons with a red dot when errors exist on that tab.
// Returns the number of errors that were shown inline.
function showFieldErrors(formEl, detail) {
  clearFieldErrors(formEl);
  if (!Array.isArray(detail)) return 0;
  const tabsWithErrors = new Set();
  let shown = 0;
  for (const d of detail) {
    // Find the field name from loc, skipping "body" and array indices.
    const parts = Array.isArray(d.loc)
      ? d.loc.filter(p => p !== 'body' && p !== 'query' && p !== 'path' && typeof p !== 'number')
      : [];
    const fieldName = parts[0] || '';
    if (!fieldName) continue;
    // Find the wrapper element via data-field attribute.  Every field's
    // section div (from form_field macro, string_list/pair_list field= param,
    // or manual data-field on inline inputs) carries this attribute.
    const wrapper = formEl.querySelector(`[data-field="${CSS.escape(fieldName)}"]`);
    if (!wrapper) continue;
    const errEl = document.createElement('p');
    errEl.className = 'qm-field-error';
    errEl.textContent = d.msg;
    if (!wrapper.querySelector('.qm-field-error')) {
      wrapper.appendChild(errEl);
    }
    shown++;
    const panel = wrapper.closest('[data-tab-panel]');
    if (panel) tabsWithErrors.add(panel.dataset.tabPanel);
  }
  // Show red dot on tab buttons that have errors.
  for (const tabNum of tabsWithErrors) {
    const btn = formEl.closest('[x-data]')?.querySelector(`[data-tab="${tabNum}"] .qm-tab-error-dot`);
    if (btn) btn.classList.remove('hidden');
  }
  return shown;
}

// Generic JSON POST/PUT helper used by all form submissions
async function jsonFetch(method, url, data) {
  return fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
    body: JSON.stringify(data),
  });
}

// Inject CSRF token into every HTMX mutating request automatically.
document.addEventListener('htmx:configRequest', function(evt) {
  evt.detail.headers['X-CSRF-Token'] = getCsrfToken();
});

// Read HX-Trigger for toast notifications and surface API errors.
// Registered as a DOM event listener (not hx-on attribute) to avoid CSP eval requirement.
document.addEventListener('htmx:afterRequest', function handleHtmxResponse(evt) {
  const xhr = evt.detail.xhr;
  if (!xhr) return;
  if (xhr.status === 401) {
    window.location.href = '/login';
    return;
  }
  if (xhr.status >= 400) {
    try {
      const body = JSON.parse(xhr.responseText);
      if (body.detail) { showApiError(body, t('Request failed')); return; }
    } catch {}
    showToast(t('Request failed') + ' (' + xhr.status + ')', 'error');
    return;
  }
  const trigger = xhr.getResponseHeader('HX-Trigger');
  if (!trigger) return;
  try {
    const data = JSON.parse(trigger);
    if (data.showToast) showToast(data.showToast, data.toastType || 'success');
    if (data.clearDetail) {
      const tpl = document.getElementById('main-content-empty-tpl');
      const mc = document.getElementById('main-content');
      mc.replaceChildren(tpl.content.cloneNode(true));
    }
  } catch {}
});
