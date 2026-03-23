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
      const field = Array.isArray(d.loc) ? d.loc[d.loc.length - 1] : '';
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
