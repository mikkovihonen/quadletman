// Modal helpers — show/hide modals and HTMX-loaded modal openers

// ---------------------------------------------------------------------------
// Toast system
// Deduplicates identical messages within 500 ms to prevent double-firing from
// HTMX events that may dispatch on both element and document.
// ---------------------------------------------------------------------------

let _lastToast = null;
function showToast(msg, type = 'success', { html = false } = {}) {
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
  if (html) {
    toast.innerHTML = DOMPurify.sanitize(msg, { ALLOWED_TAGS: ['b', 'br', 'em', 'strong', 'code'], ALLOWED_ATTR: [] });
  }
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
  if (opts.titleId) document.getElementById(opts.titleId).textContent = opts.title || '';
  const p = htmx.ajax('GET', url, {
    target: '#' + targetId,
    swap: 'innerHTML',
    headers: { 'HX-Request': 'true', ...(opts.headers || {}) },
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
  _htmxModal(`/api/compartments/${compartmentId}/processes`, 'proc-modal-body', 'proc-modal',
    { titleId: 'proc-modal-title', title: displayName + ' \u2014 ' + t('processes') });
}

function showDiskModal(compartmentId, displayName) {
  _htmxModal(`/api/compartments/${compartmentId}/disk-usage`, 'disk-modal-body', 'disk-modal',
    { titleId: 'disk-modal-title', title: displayName + ' \u2014 ' + t('disk usage') });
}

function showStatusModal(compartmentId, containerName) {
  _htmxModal(`/api/compartments/${compartmentId}/containers/${containerName}/status-detail`,
    'status-modal-body', 'status-modal',
    { titleId: 'status-modal-title', title: containerName });
}

function showQuadletsModal(compartmentId, compartmentName) {
  document.getElementById('quadlets-export-link').href = `/api/compartments/${compartmentId}/export`;
  document.getElementById('quadlets-export-link').download = `${compartmentId}.quadlets`;
  _htmxModal(`/api/compartments/${compartmentId}/quadlets`, 'quadlets-modal-content', 'quadlets-modal',
    { titleId: 'quadlets-modal-title', title: compartmentName + ' \u2014 ' + t('quadlet files') });
}

function showAddContainerModal(compartmentId, containerId) {
  const url = containerId
    ? `/api/compartments/${compartmentId}/containers/${containerId}/form`
    : `/api/compartments/${compartmentId}/containers/form`;
  _htmxModal(url, 'add-container-form-wrapper', 'add-container-modal',
    { clear: true, indicator: '#global-spinner', defer: true });
}
function showBuildUnitModal(compartmentId, buildUnitId) {
  const url = buildUnitId
    ? `/api/compartments/${compartmentId}/build-units/${buildUnitId}/form`
    : `/api/compartments/${compartmentId}/build-units/form`;
  _htmxModal(url, 'build-unit-form-wrapper', 'build-unit-modal',
    { clear: true, indicator: '#global-spinner', defer: true });
}
function showImageUnitModal(compartmentId, imageUnitId) {
  const url = imageUnitId
    ? `/api/compartments/${compartmentId}/image-units/${imageUnitId}/form`
    : `/api/compartments/${compartmentId}/image-units/form`;
  _htmxModal(url, 'image-unit-form-wrapper', 'image-unit-modal',
    { clear: true, indicator: '#global-spinner', defer: true });
}
function showPodModal(compartmentId, podId) {
  const url = podId
    ? `/api/compartments/${compartmentId}/pods/${podId}/form`
    : `/api/compartments/${compartmentId}/pods/form`;
  _htmxModal(url, 'pod-form-wrapper', 'pod-modal',
    { clear: true, indicator: '#global-spinner', defer: true });
}
function showArtifactModal(compartmentId, artifactId) {
  const url = artifactId
    ? `/api/compartments/${compartmentId}/artifacts/${artifactId}/form`
    : `/api/compartments/${compartmentId}/artifacts/form`;
  _htmxModal(url, 'artifact-form-wrapper', 'artifact-modal',
    { clear: true, indicator: '#global-spinner', defer: true });
}
function showEditNetworkModal(compartmentId, networkId) {
  _htmxModal(`/api/compartments/${compartmentId}/networks/${networkId}/form`,
    'edit-network-form-wrapper', 'edit-network-modal', { clear: true });
}
function showAddVolumeModal(compartmentId) {
  _htmxModal(`/api/compartments/${compartmentId}/volumes/form`, 'add-volume-form-wrapper', 'add-volume-modal');
}
function showHostSettings() {
  _htmxModal('/api/host-settings-partial', 'host-settings-content', 'host-settings-modal');
}
function showSessionInfo() {
  _htmxModal('/api/session-info-partial', 'session-info-content', 'session-info-modal');
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
      showApiError(data, t('Failed to apply boolean'));
    }
  } catch (_) {
    showToast(t('Request failed'), 'error');
  }
}


function showTemplatesModal() {
  _htmxModal('/api/templates', 'templates-modal-body', 'templates-modal', { clear: true });
}

function saveAsTemplate(compartmentId) {
  document.getElementById('save-template-compartment-id').value = compartmentId;
  document.getElementById('save-template-name').value = '';
  document.getElementById('save-template-description').value = '';
  showModal('save-template-modal');
  document.getElementById('save-template-form').onsubmit = async (e) => {
    e.preventDefault();
    const name = document.getElementById('save-template-name').value;
    const description = document.getElementById('save-template-description').value;
    try {
      const r = await jsonFetch('POST', '/api/templates', {
        source_compartment_id: compartmentId,
        name,
        description,
      });
      if (r.ok) {
        hideModal('save-template-modal');
        showToast(t('Template saved'));
      } else {
        const data = await r.json().catch(() => ({}));
        showApiError(data, t('Failed to save template'));
      }
    } catch (_) {
      showToast(t('Request failed'), 'error');
    }
  };
}

async function showPodmanInfo() {
  const match = window.location.pathname.match(/^\/compartments\/([^\/]+)/);
  const compartmentId = match ? match[1] : null;
  const appUser = window.QM_APP_USER || 'root';
  const hint = compartmentId
    ? t('Showing compartment user: qm-%(id)s').replace('%(id)s', compartmentId)
    : appUser === 'root'
      ? t('Showing root (system-wide)')
      : t('Showing user: %(user)s (system-wide)').replace('%(user)s', appUser);
  document.getElementById('podman-info-title-hint').textContent = hint;
  document.getElementById('podman-info-output').textContent = t('Loading…');
  showModal('podman-info-modal');
  // Load features tab via HTMX
  htmx.ajax('GET', '/api/podman-features', {
    target: '#podman-features-content', swap: 'innerHTML',
    headers: { 'HX-Request': 'true' },
  });
  // Load raw info tab
  const url = compartmentId
    ? `/api/compartments/${compartmentId}/podman-info`
    : '/api/podman-info';
  try {
    const r = await fetch(url);
    const data = await r.json();
    document.getElementById('podman-info-output').textContent =
      JSON.stringify(data, null, 2);
  } catch (e) {
    document.getElementById('podman-info-output').textContent = t('Failed to load podman info.');
  }
}
