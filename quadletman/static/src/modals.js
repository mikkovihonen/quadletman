// Modal helpers — show/hide modals and HTMX-loaded modal openers

// ---------------------------------------------------------------------------
// Toast system
// Deduplicates identical messages within 500 ms to prevent double-firing from
// HTMX events that may dispatch on both element and document.
// ---------------------------------------------------------------------------

let _lastToast = null;
function showToast(msg, type = 'success') {
  const key = msg + '|' + type;
  const now = Date.now();
  if (_lastToast && _lastToast.key === key && now - _lastToast.t < 500) return;
  _lastToast = { key, t: now };
  const container = document.getElementById('toast-container');
  const toast = document.createElement('div');
  toast.className = `pointer-events-auto bg-gray-800 border ${
    type === 'error' ? 'border-red-500 text-red-300' : 'border-green-500 text-green-300'
  } rounded-lg px-4 py-2 text-sm shadow-lg qm-toast-shown`;
  toast.textContent = msg;
  container.appendChild(toast);
  toast.addEventListener('animationend', () => toast.remove(), { once: true });
}

// ---------------------------------------------------------------------------
// Core show/hide
// ---------------------------------------------------------------------------

function showModal(id) {
  document.getElementById(id).classList.remove('hidden');
}
function hideModal(id) {
  document.getElementById(id).classList.add('hidden');
}
function showCreateCompartmentModal() {
  showModal('create-compartment-modal');
}

// ---------------------------------------------------------------------------
// HTMX-loaded modal opener
// opts.clear: clear target before loading; opts.indicator: htmx indicator selector;
// opts.defer: show modal only after load completes (use when content drives modal sizing).
// ---------------------------------------------------------------------------

function _htmxModal(url, targetId, modalId, opts = {}) {
  if (opts.clear) document.getElementById(targetId).innerHTML = '';
  const p = htmx.ajax('GET', url, {
    target: '#' + targetId,
    swap: 'innerHTML',
    ...(opts.indicator ? { indicator: opts.indicator } : {}),
  });
  if (opts.defer) return p.then(() => showModal(modalId));
  showModal(modalId);
}

// ---------------------------------------------------------------------------
// Named modal openers
// ---------------------------------------------------------------------------

function doLogout() {
  fetch('/api/logout', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'X-CSRF-Token': getCsrfToken() },
  }).finally(() => { window.location.href = '/login'; });
}

function showVolumeFiles(compartmentId, volumeId) {
  _htmxModal(`/api/compartments/${compartmentId}/volumes/${volumeId}/browse`, 'volume-browser-content', 'volume-browser-modal');
}

function showProcModal(compartmentId, displayName) {
  document.getElementById('proc-modal-title').textContent = displayName + ' \u2014 ' + t('processes');
  showModal('proc-modal');
  htmx.ajax('GET', `/api/compartments/${compartmentId}/processes`, {
    target: '#proc-modal-body', swap: 'innerHTML', headers: { 'HX-Request': 'true' },
  });
}

function showDiskModal(compartmentId, displayName) {
  document.getElementById('disk-modal-title').textContent = displayName + ' \u2014 ' + t('disk usage');
  showModal('disk-modal');
  htmx.ajax('GET', `/api/compartments/${compartmentId}/disk-usage`, {
    target: '#disk-modal-body', swap: 'innerHTML', headers: { 'HX-Request': 'true' },
  });
}

function showStatusModal(compartmentId, containerName) {
  document.getElementById('status-modal-title').textContent = containerName;
  showModal('status-modal');
  htmx.ajax('GET', `/api/compartments/${compartmentId}/containers/${containerName}/status-detail`, {
    target: '#status-modal-body', swap: 'innerHTML', headers: { 'HX-Request': 'true' },
  });
}

function showQuadletsModal(compartmentId, compartmentName) {
  document.getElementById('quadlets-modal-title').textContent = compartmentName + ' \u2014 ' + t('quadlet files');
  document.getElementById('quadlets-export-link').href = `/api/compartments/${compartmentId}/export`;
  document.getElementById('quadlets-export-link').download = `${compartmentId}.quadlets`;
  htmx.ajax('GET', `/api/compartments/${compartmentId}/quadlets`, {
    target: '#quadlets-modal-content',
    swap: 'innerHTML',
    headers: { 'HX-Request': 'true' },
  });
  showModal('quadlets-modal');
}

function showAddContainerModal(compartmentId, containerId) {
  const url = containerId
    ? `/api/compartments/${compartmentId}/containers/${containerId}/form`
    : `/api/compartments/${compartmentId}/containers/form`;
  _htmxModal(url, 'add-container-form-wrapper', 'add-container-modal',
    { clear: true, indicator: '#global-spinner', defer: true });
}
function showAddVolumeModal(compartmentId) {
  _htmxModal(`/api/compartments/${compartmentId}/volumes/form`, 'add-volume-form-wrapper', 'add-volume-modal');
}
function showHostSettings() {
  _htmxModal('/api/host-settings-partial', 'host-settings-content', 'host-settings-modal');
}
function showSelinuxModal() {
  _htmxModal('/api/selinux-booleans-partial', 'selinux-modal-content', 'selinux-modal');
}

async function applySelinuxBoolean(event, form) {
  event.preventDefault();
  const name = form.elements['name'].value;
  const enabled = form.elements['enabled'].value === 'true';
  try {
    const resp = await jsonFetch('POST', '/api/selinux-booleans', { name, enabled });
    if (resp.ok) {
      showToast(t('Boolean applied (persistent)'));
      const sel = form.querySelector('select');
      sel.classList.remove('qm-flash-green');
      void sel.offsetWidth; // reflow to restart animation if already running
      sel.classList.add('qm-flash-green');
    } else {
      const data = await resp.json().catch(() => ({}));
      showToast(data.detail || t('Failed to apply boolean'), 'error');
    }
  } catch (_) {
    showToast(t('Request failed'), 'error');
  }
}

async function showPodmanInfo() {
  const match = window.location.pathname.match(/^\/compartments\/([^\/]+)/);
  const compartmentId = match ? match[1] : null;
  const url = compartmentId
    ? `/api/compartments/${compartmentId}/podman-info`
    : '/api/podman-info';
  const hint = compartmentId
    ? t('Showing compartment user: qm-%(id)s').replace('%(id)s', compartmentId)
    : t('Showing root (system-wide)');
  document.getElementById('podman-info-title-hint').textContent = hint;
  document.getElementById('podman-info-output').textContent = t('Loading…');
  showModal('podman-info-modal');
  try {
    const r = await fetch(url);
    const data = await r.json();
    document.getElementById('podman-info-output').textContent =
      JSON.stringify(data, null, 2);
  } catch (e) {
    document.getElementById('podman-info-output').textContent = t('Failed to load podman info.');
  }
}
