// CSRF / fetch helpers and HTMX integration

// Read the CSRF token from the qm_csrf cookie (set by the server on login)
function getCsrfToken() {
  const entry = document.cookie.split('; ').find(r => r.startsWith('qm_csrf='));
  if (!entry) return '';
  try { return decodeURIComponent(entry.split('=')[1]); } catch { return ''; }
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
      if (body.detail) { showToast(body.detail, 'error'); return; }
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
