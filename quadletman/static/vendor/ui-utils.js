// Shared utilities — used by base.html, dashboard.html, and compartment_metrics.html

// ---------------------------------------------------------------------------
// Metrics helpers
// ---------------------------------------------------------------------------

function drawSparkline(canvas, data, color) {
  if (!canvas) return;
  const ctx = canvas.getContext('2d');
  const W = canvas.width, H = canvas.height;
  ctx.clearRect(0, 0, W, H);
  if (data.length < 2) return;
  const max = Math.max(...data, 0.001);
  function buildPath() {
    ctx.beginPath();
    data.forEach((v, i) => {
      const x = (i / (data.length - 1)) * W;
      const y = H - (v / max) * H * 0.85 - 2;
      i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
    });
  }
  buildPath(); ctx.lineTo(W, H); ctx.lineTo(0, H); ctx.closePath();
  ctx.fillStyle = color + '22'; ctx.fill();
  buildPath(); ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.stroke();
}

function fmtBytes(b) {
  if (b >= 1e9) return (b / 1e9).toFixed(1) + ' GB';
  if (b >= 1e6) return (b / 1e6).toFixed(1) + ' MB';
  if (b >= 1e3) return (b / 1e3).toFixed(1) + ' KB';
  return b + ' B';
}

function setText(id, text) {
  const el = document.getElementById(id);
  if (el) el.textContent = text;
}

// ---------------------------------------------------------------------------
// Alpine component: per-file permission editor (volume browser)
// Receives the server-rendered mode object (ur/uw/ux/gr/gw/gx/or/ow/ox booleans)
// and exposes rwx checkboxes plus a computed octal getter for the hidden input.
// ---------------------------------------------------------------------------

function chmodEditor(mode) {
  return {
    showPerms: false,
    ur: mode.ur, uw: mode.uw, ux: mode.ux,
    gr: mode.gr, gw: mode.gw, gx: mode.gx,
    or_: mode.or, ow: mode.ow, ox: mode.ox,
    get octal() {
      const u = (this.ur ? 4 : 0) + (this.uw ? 2 : 0) + (this.ux ? 1 : 0);
      const g = (this.gr ? 4 : 0) + (this.gw ? 2 : 0) + (this.gx ? 1 : 0);
      const o = (this.or_ ? 4 : 0) + (this.ow ? 2 : 0) + (this.ox ? 1 : 0);
      return '' + u + g + o;
    },
  };
}

// ---------------------------------------------------------------------------
// CSRF / fetch helpers
// ---------------------------------------------------------------------------

// Read the CSRF token from the qm_csrf cookie (set by the server on login)
function getCsrfToken() {
  const entry = document.cookie.split('; ').find(r => r.startsWith('qm_csrf='));
  return entry ? decodeURIComponent(entry.split('=')[1]) : '';
}

// Generic JSON POST/PUT helper used by all form submissions
async function jsonFetch(method, url, data) {
  return fetch(url, {
    method,
    headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': getCsrfToken() },
    body: JSON.stringify(data),
  });
}

// ---------------------------------------------------------------------------
// Alpine component: container form
// Defined here (loaded in <head>) so it is available before HTMX-loaded
// fragments are processed by Alpine's MutationObserver.
// ---------------------------------------------------------------------------

function containerForm(compartmentId, containerId) {
  return {
    compartmentId,
    containerId,
    activeTab: 1,
    ports: [],
    envFilePath: '',
    envFileUploaded: false,
    envFilePreview: null,
    envFileLoading: false,
    envFileError: '',
    envPairs: [],
    volumeMounts: [],
    bindMounts: [],
    uidMaps: [],
    gidMaps: [],
    imageSource: 'registry',  // 'registry' | 'image_unit' | 'build'
    registryImage: '',
    imageUnitRef: '',
    buildImageTag: '',
    hasImageUnits: false,
    canBuild: false,
    containerfileContent: '',
    healthEnabled: false,
    dropCaps: [],
    addCaps: [],
    sysctlPairs: [],
    dnsServers: [],
    dnsSearch: [],
    dnsOption: [],
    maskPaths: [],
    unmaskPaths: [],
    init() {
      const el = this.$el;
      const d = JSON.parse(el.dataset.init || '{}');
      this.ports = d.ports ?? [];
      this.envPairs = d.envPairs ?? [];
      this.volumeMounts = d.volumeMounts ?? [];
      this.bindMounts = d.bindMounts ?? [];
      this.uidMaps = d.uidMap ?? [];
      this.gidMaps = d.gidMap ?? [];
      const imageUnits = d.imageUnits ?? [];
      this.hasImageUnits = imageUnits.length > 0;
      this.canBuild = d.podmanBuild !== false;
      this.containerfileContent = d.containerfileContent ?? '';
      const currentImage = d.image ?? '';
      if (this.containerfileContent) {
        this.imageSource = 'build';
        this.buildImageTag = currentImage;
      } else if (currentImage.endsWith('.image') && imageUnits.some(n => currentImage === n + '.image')) {
        this.imageSource = 'image_unit';
        this.imageUnitRef = currentImage;
      } else {
        this.imageSource = 'registry';
        this.registryImage = currentImage;
      }
      // Always pre-fill imageUnitRef with the first available unit so that
      // switching to the Pre-pulled image tab never leaves it blank.
      if (!this.imageUnitRef && imageUnits.length > 0) {
        this.imageUnitRef = imageUnits[0] + '.image';
      }
      this.healthEnabled = d.healthEnabled === true;
      this.dropCaps = d.dropCaps ?? [];
      this.addCaps = d.addCaps ?? [];
      this.sysctlPairs = d.sysctlPairs ?? [];
      this.dnsServers = d.dns ?? [];
      this.dnsSearch = d.dnsSearch ?? [];
      this.dnsOption = d.dnsOption ?? [];
      this.maskPaths = d.maskPaths ?? [];
      this.unmaskPaths = d.unmaskPaths ?? [];
      this.envFilePath = d.environmentFile ?? '';
      if (this.envFilePath) {
        this.envFileUploaded = true;
        this.loadEnvPreview();
      }
    },
    async _fetchErrMsg(resp, defaultMsg) {
      const data = await resp.json().catch(() => ({}));
      return data.detail || defaultMsg;
    },
    async uploadEnvFile(inputEl) {
      const file = inputEl.files[0];
      if (!file) return;
      const form = new FormData();
      form.append('file', file);
      this.envFileError = '';
      this.envFilePreview = null;
      const resp = await fetch(
        `/api/compartments/${this.compartmentId}/containers/${this.containerId}/envfile`,
        { method: 'POST', body: form, headers: { 'X-CSRF-Token': getCsrfToken() } }
      );
      if (resp.ok) {
        const data = await resp.json();
        this.envFilePath = data.path;
        this.envFileUploaded = true;
        await this.loadEnvPreview();
      } else {
        this.envFileError = await this._fetchErrMsg(resp, 'Upload failed');
      }
      inputEl.value = '';
    },
    async deleteEnvFile() {
      this.envFileError = '';
      const resp = await fetch(
        `/api/compartments/${this.compartmentId}/containers/${this.containerId}/envfile`,
        { method: 'DELETE', headers: { 'X-CSRF-Token': getCsrfToken() } }
      );
      if (resp.ok) {
        this.envFilePath = '';
        this.envFileUploaded = false;
        this.envFilePreview = null;
      } else {
        this.envFileError = await this._fetchErrMsg(resp, 'Delete failed');
      }
    },
    async loadEnvPreview() {
      if (!this.envFilePath) { this.envFilePreview = null; return; }
      this.envFileLoading = true;
      this.envFileError = '';
      try {
        const resp = await fetch(
          `/api/compartments/${this.compartmentId}/envfile?path=${encodeURIComponent(this.envFilePath)}`,
          { headers: { 'X-CSRF-Token': getCsrfToken() } }
        );
        if (resp.ok) {
          const data = await resp.json();
          this.envFilePreview = data.lines;
        } else {
          this.envFileError = await this._fetchErrMsg(resp, 'Could not load preview');
          this.envFilePreview = null;
        }
      } finally {
        this.envFileLoading = false;
      }
    },
    filteredVolumes() {
      return JSON.stringify(this.volumeMounts.filter(function(vm) {
        return vm.volume_id && vm.container_path;
      }));
    },
    resolvedImage() {
      if (this.imageSource === 'image_unit') return this.imageUnitRef;
      if (this.imageSource === 'build') return this.buildImageTag;
      return this.registryImage;
    },
    async submitForm(form) {
      // Validate before collecting FormData so required fields on hidden tabs
      // are reachable. Find the first invalid field, switch to its tab, then
      // let the browser show its native validation tooltip.
      const firstInvalid = form.querySelector(':invalid');
      if (firstInvalid) {
        const tabPanel = firstInvalid.closest('[x-show^="activeTab"]');
        if (tabPanel) {
          const match = tabPanel.getAttribute('x-show').match(/activeTab === (\d+)/);
          if (match) this.activeTab = parseInt(match[1], 10);
        }
        // Defer reportValidity so Alpine has time to unhide the panel.
        await new Promise(r => setTimeout(r, 50));
        form.reportValidity();
        return;
      }
      const fd = new FormData(form);
      const data = {
        name: fd.get('name'),
        image: this.resolvedImage(),
        network: fd.get('network'),
        restart_policy: fd.get('restart_policy'),
        memory_limit: fd.get('memory_limit') || '',
        cpu_quota: fd.get('cpu_quota') || '',
        apparmor_profile: fd.get('apparmor_profile') || '',
        run_user: fd.get('run_user') || '',
        exec_start_pre: '',
        sort_order: 0,
        labels: {},
        depends_on: fd.getAll('depends_on'),
        ports: this.ports.filter(p => p.trim()),
        environment: Object.fromEntries(this.envPairs.filter(([k]) => k.trim())),
        volumes: this.volumeMounts.filter(vm => vm.volume_id && vm.container_path),
        bind_mounts: this.bindMounts.filter(bm => bm.host_path.trim() && bm.container_path.trim()),
        containerfile_content: this.imageSource === 'build' ? this.containerfileContent : '',
        uid_map: this.uidMaps.filter(m => m.trim && m.trim() && String(m) !== '0'),
        gid_map: this.gidMaps.filter(m => m.trim && m.trim() && String(m) !== '0'),
        // New fields
        entrypoint: fd.get('entrypoint') || '',
        exec_cmd: fd.get('exec_cmd') || '',
        environment_file: fd.get('environment_file') || '',
        auto_update: fd.get('auto_update') || '',
        no_new_privileges: fd.get('no_new_privileges') === 'true',
        read_only: fd.get('read_only') === 'true',
        health_cmd: this.healthEnabled ? (fd.get('health_cmd') || '') : '',
        health_interval: this.healthEnabled ? (fd.get('health_interval') || '') : '',
        health_timeout: this.healthEnabled ? (fd.get('health_timeout') || '') : '',
        health_retries: this.healthEnabled ? (fd.get('health_retries') || '') : '',
        health_start_period: this.healthEnabled ? (fd.get('health_start_period') || '') : '',
        health_on_failure: this.healthEnabled ? (fd.get('health_on_failure') || '') : '',
        notify_healthy: this.healthEnabled && fd.get('notify_healthy') === 'true',
        working_dir: fd.get('working_dir') || '',
        hostname: fd.get('hostname') || '',
        privileged: fd.get('privileged') === 'true',
        drop_caps: this.dropCaps.filter(c => c.trim()),
        add_caps: this.addCaps.filter(c => c.trim()),
        sysctl: Object.fromEntries(this.sysctlPairs.filter(([k]) => k.trim())),
        seccomp_profile: fd.get('seccomp_profile') || '',
        mask_paths: this.maskPaths.filter(p => p.trim()),
        unmask_paths: this.unmaskPaths.filter(p => p.trim()),
        dns: this.dnsServers.filter(d => d.trim()),
        dns_search: this.dnsSearch.filter(d => d.trim()),
        dns_option: this.dnsOption.filter(d => d.trim()),
        // P2/P3 fields
        pod_name: fd.get('pod_name') || '',
        log_driver: fd.get('log_driver') || '',
        log_opt: {},
        exec_start_post: fd.get('exec_start_post') || '',
        exec_stop: fd.get('exec_stop') || '',
      };
      const url = this.containerId
        ? `/api/compartments/${this.compartmentId}/containers/${this.containerId}`
        : `/api/compartments/${this.compartmentId}/containers`;
      const resp = await jsonFetch(this.containerId ? 'PUT' : 'POST', url, data);
      if (resp.ok) {
        hideModal('add-container-modal');
        htmx.ajax('GET', `/api/compartments/${this.compartmentId}`, {
          target: '#main-content', swap: 'innerHTML',
          headers: { 'HX-Request': 'true' },
        });
      } else {
        const err = await resp.json().catch(() => ({}));
        showToast(err.detail || 'Failed to save container', 'error');
      }
    },
  };
}

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
  } rounded-lg px-4 py-2 text-sm shadow-lg transition-opacity duration-300`;
  toast.textContent = msg;
  container.appendChild(toast);
  setTimeout(() => { toast.style.opacity = '0'; setTimeout(() => toast.remove(), 300); }, 3000);
}

// ---------------------------------------------------------------------------
// Modal helpers
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
// HTMX integration
// ---------------------------------------------------------------------------

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
    showToast('Request failed (' + xhr.status + ')', 'error');
    return;
  }
  const trigger = xhr.getResponseHeader('HX-Trigger');
  if (!trigger) return;
  try {
    const data = JSON.parse(trigger);
    if (data.showToast) showToast(data.showToast, data.toastType || 'success');
    if (data.clearDetail) {
      document.getElementById('main-content').innerHTML =
        '<div class="flex items-center justify-center h-full text-gray-600">' +
        '<p class="text-lg">Select a compartment</p></div>';
    }
  } catch {}
});

// ---------------------------------------------------------------------------
// Navigation helpers (update URL + load content)
// ---------------------------------------------------------------------------

function _loadView(apiUrl, pushUrl, state, push) {
  if (push) history.pushState(state, '', pushUrl);
  htmx.ajax('GET', apiUrl, { target: '#main-content', swap: 'innerHTML', headers: { 'HX-Request': 'true' } });
}

function loadCompartment(compartmentId, push = true) {
  _loadView(`/api/compartments/${compartmentId}`, `/compartments/${compartmentId}`, { view: 'compartment', id: compartmentId }, push);
}
function loadDashboard(push = true) {
  _loadView('/api/dashboard', '/', { view: 'dashboard' }, push);
}
function loadEvents(push = true) {
  _loadView('/api/events', '/events', { view: 'events' }, push);
}

// Restore correct view after hard reload, and handle browser back/forward.
function initFromUrl() {
  const path = window.location.pathname;
  const compMatch = path.match(/^\/compartments\/([^/]+)$/);
  if (compMatch) {
    loadCompartment(compMatch[1], false);
  } else if (path === '/events') {
    loadEvents(false);
  } else {
    loadDashboard(false);
  }
}

window.addEventListener('popstate', (e) => {
  const state = e.state;
  if (!state) { loadDashboard(false); return; }
  if (state.view === 'compartment') loadCompartment(state.id, false);
  else if (state.view === 'events') loadEvents(false);
  else loadDashboard(false);
});

// ---------------------------------------------------------------------------
// Modal openers
// ---------------------------------------------------------------------------

// Load content via HTMX then show a modal.
// opts.clear: clear target before loading; opts.indicator: htmx indicator selector;
// opts.defer: show modal only after load completes (use when content drives modal sizing).
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

function showVolumeFiles(compartmentId, volumeId) {
  _htmxModal(`/api/compartments/${compartmentId}/volumes/${volumeId}/browse`, 'volume-browser-content', 'volume-browser-modal');
}

function doLogout() {
  fetch('/api/logout', {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'X-CSRF-Token': getCsrfToken() },
  }).finally(() => { window.location.href = '/login'; });
}

async function showProcModal(compartmentId, displayName) {
  document.getElementById('proc-modal-title').textContent = displayName + ' — processes';
  document.getElementById('proc-modal-body').innerHTML =
    '<tr><td colspan="5" class="px-5 py-4 text-gray-500">Loading…</td></tr>';
  showModal('proc-modal');
  try {
    const r = await fetch(`/api/compartments/${compartmentId}/processes`);
    const procs = await r.json();
    const tbody = document.getElementById('proc-modal-body');
    if (!procs.length) {
      tbody.innerHTML = '<tr><td colspan="5" class="px-5 py-4 text-gray-500">No processes running.</td></tr>';
      return;
    }
    tbody.innerHTML = procs.map(p => `
      <tr class="hover:bg-gray-800/50">
        <td class="px-5 py-1.5 text-gray-400">${p.pid}</td>
        <td class="px-3 py-1.5 text-green-400">${p.name}</td>
        <td class="px-3 py-1.5 text-gray-400 max-w-xs truncate hidden md:table-cell" title="${p.cmdline}">${p.cmdline}</td>
        <td class="px-3 py-1.5 text-right text-yellow-400">${p.cpu_percent.toFixed(1)}%</td>
        <td class="px-5 py-1.5 text-right text-blue-400">${fmtBytes(p.mem_bytes)}</td>
      </tr>`).join('');
  } catch (_) {
    document.getElementById('proc-modal-body').innerHTML =
      '<tr><td colspan="5" class="px-5 py-4 text-red-400">Failed to load processes.</td></tr>';
  }
}

async function showDiskModal(compartmentId, displayName) {
  document.getElementById('disk-modal-title').textContent = displayName + ' — disk usage';
  document.getElementById('disk-modal-body').innerHTML = '<div class="text-gray-500">Loading…</div>';
  showModal('disk-modal');
  try {
    const r = await fetch(`/api/compartments/${compartmentId}/disk-usage`);
    const d = await r.json();
    const total = d.images.reduce((s,x)=>s+x.bytes,0) +
                  d.overlays.reduce((s,x)=>s+x.bytes,0) +
                  d.volumes_total +
                  (d.config_bytes || 0);

    function section(title, items, emptyMsg) {
      const rows = items.length
        ? items.map(x => `<div class="flex justify-between font-mono">
            <span class="text-gray-300 truncate mr-4" title="${x.name}">${x.name}</span>
            <span class="text-white shrink-0">${fmtBytes(x.bytes)}</span>
          </div>`).join('')
        : `<div class="text-gray-600">${emptyMsg}</div>`;
      return `<div>
        <div class="text-gray-500 mb-1">${title}</div>
        <div class="space-y-1">${rows}</div>
      </div>`;
    }

    document.getElementById('disk-modal-body').innerHTML = `
      <div class="flex justify-between items-baseline border-b border-gray-700 pb-3 mb-1">
        <span class="text-gray-400">Total</span>
        <span class="text-lg font-mono font-semibold text-white">${fmtBytes(total)}</span>
      </div>
      ${section('Container Images', d.images, 'No images pulled')}
      ${section('Container Overlays (writable layers)', d.overlays, 'No writable layer data')}
      ${section('Managed Volumes', d.volumes, 'No managed volumes')}
      <div>
        <div class="text-gray-500 mb-1">Compartment Configuration</div>
        <div class="flex justify-between font-mono">
          <span class="text-gray-300">~/ (excl. container storage)</span>
          <span class="text-white shrink-0">${fmtBytes(d.config_bytes || 0)}</span>
        </div>
      </div>`;
  } catch (_) {
    document.getElementById('disk-modal-body').innerHTML = '<div class="text-red-400">Failed to load disk usage.</div>';
  }
}

function showStatusModalFromEl(el) {
  const s = JSON.parse(el.dataset.status);
  document.getElementById('status-modal-title').textContent = s.container;
  const rows = [
    ['unit', s.unit],
    ['active', s.active_state],
    ['sub', s.sub_state],
    ['load', s.load_state],
    s.unit_file_state ? ['unit file', s.unit_file_state] : null,
    (s.main_pid && s.main_pid !== '0') ? ['PID', s.main_pid] : null,
  ].filter(Boolean);
  document.getElementById('status-modal-props').innerHTML = rows.map(
    ([k, v]) => `<tr><td class="text-gray-500 pr-4 py-1 w-24 align-top">${k}</td><td class="text-gray-200 font-mono">${v}</td></tr>`
  ).join('');
  const pre = document.getElementById('status-modal-text');
  pre.textContent = s.status_text;
  pre.classList.toggle('hidden', !s.status_text);
  showModal('status-modal');
}

function showQuadletsModal(compartmentId, compartmentName) {
  document.getElementById('quadlets-modal-title').textContent = compartmentName + ' — quadlet files';
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
      showToast('Boolean applied (persistent)');
      const sel = form.querySelector('select');
      sel.classList.add('ring-1', 'ring-green-500');
      setTimeout(() => sel.classList.remove('ring-1', 'ring-green-500'), 1500);
    } else {
      const data = await resp.json().catch(() => ({}));
      showToast(data.detail || 'Failed to apply boolean', 'error');
    }
  } catch (_) {
    showToast('Request failed', 'error');
  }
}

async function showPodmanInfo() {
  const match = window.location.pathname.match(/^\/compartments\/([^\/]+)/);
  const compartmentId = match ? match[1] : null;
  const url = compartmentId
    ? `/api/compartments/${compartmentId}/podman-info`
    : '/api/podman-info';
  const hint = compartmentId
    ? `Showing compartment user: qm-${compartmentId}`
    : 'Showing root (system-wide)';
  document.getElementById('podman-info-title-hint').textContent = hint;
  document.getElementById('podman-info-output').textContent = 'Loading…';
  showModal('podman-info-modal');
  try {
    const r = await fetch(url);
    const data = await r.json();
    document.getElementById('podman-info-output').textContent =
      JSON.stringify(data, null, 2);
  } catch (e) {
    document.getElementById('podman-info-output').textContent = 'Failed to load podman info.';
  }
}

// ---------------------------------------------------------------------------
// Log streaming
// ---------------------------------------------------------------------------

let _logEvtSource = null;
function _openLogStream(title, url) {
  stopLogs();
  document.getElementById('log-modal-title').textContent = title;
  const output = document.getElementById('log-output');
  output.textContent = '';
  showModal('log-modal');
  _logEvtSource = new EventSource(url);
  _logEvtSource.onmessage = (e) => {
    output.textContent += e.data + '\n';
    output.scrollTop = output.scrollHeight;
  };
  _logEvtSource.onerror = () => {
    output.textContent += '\n[stream ended]';
    stopLogs();
  };
}
function showLogs(compartmentId, containerName) {
  _openLogStream(
    `${compartmentId} / ${containerName}`,
    `/api/compartments/${compartmentId}/containers/${containerName}/logs`
  );
}
function stopLogs() {
  if (_logEvtSource) { _logEvtSource.close(); _logEvtSource = null; }
}
function showJournalXE(compartmentId) {
  _openLogStream(
    `${compartmentId} / journal -xe`,
    `/api/compartments/${compartmentId}/journal`
  );
}

// ---------------------------------------------------------------------------
// Terminal (xterm.js + WebSocket)
// ---------------------------------------------------------------------------

let _xtermLoaded = false;
let _term = null;
let _fitAddon = null;
let _termWs = null;
let _termResizeObs = null;
let _termCompartmentId = null;
let _termContainerName = null;

function _loadXterm(callback) {
  if (_xtermLoaded) { callback(); return; }
  const link = document.createElement('link');
  link.rel = 'stylesheet';
  link.href = '/static/vendor/xterm.css';
  document.head.appendChild(link);
  const s1 = document.createElement('script');
  s1.src = '/static/vendor/xterm.js';
  s1.onload = () => {
    const s2 = document.createElement('script');
    s2.src = '/static/vendor/addon-fit.js';
    s2.onload = () => { _xtermLoaded = true; callback(); };
    document.head.appendChild(s2);
  };
  document.head.appendChild(s1);
}

function showTerminal(compartmentId, containerName, helperUsers) {
  _termCompartmentId = compartmentId;
  _termContainerName = containerName;
  const sel = document.getElementById('terminal-user-select');
  sel.innerHTML = '<option value="root">root</option>';
  (helperUsers || []).forEach(h => {
    const opt = document.createElement('option');
    opt.value = String(h.container_uid);
    opt.textContent = h.username + ' (uid ' + h.container_uid + ')';
    sel.appendChild(opt);
  });
  document.getElementById('terminal-modal-title').textContent =
    compartmentId + ' / ' + containerName + ' \u2014 terminal';
  showModal('terminal-modal');
  _loadXterm(() => _openTerminal(compartmentId, containerName, sel.value));
}

function reconnectTerminal() {
  const user = document.getElementById('terminal-user-select').value;
  _openTerminal(_termCompartmentId, _termContainerName, user);
}

function closeTerminal() {
  _closeTerminalWs();
  hideModal('terminal-modal');
}

function _openTerminal(compartmentId, containerName, execUser) {
  _closeTerminalWs();
  const container = document.getElementById('terminal-container');
  container.innerHTML = '';
  _term = new Terminal({
    theme: { background: '#000000', foreground: '#d4d4d4' },
    fontFamily: '"Courier New", monospace',
    fontSize: 13,
    cursorBlink: true,
    scrollback: 1000,
    copyOnSelect: true,
  });
  _fitAddon = new FitAddon.FitAddon();
  _term.loadAddon(_fitAddon);
  const protocol = location.protocol === 'https:' ? 'wss' : 'ws';
  const userParam = execUser !== 'root' ? '?exec_user=' + encodeURIComponent(execUser) : '';
  const url = `${protocol}://${location.host}/api/compartments/${compartmentId}/containers/${containerName}/terminal${userParam}`;
  _termWs = new WebSocket(url);
  _termWs.binaryType = 'arraybuffer';
  _termWs.onopen = () => {
    _term.open(container);
    _fitAddon.fit();
    _term.focus();
    _sendTermResize();
    _term.onData(data => {
      if (_termWs && _termWs.readyState === WebSocket.OPEN) {
        _termWs.send(new TextEncoder().encode(data));
      }
    });
    _termResizeObs = new ResizeObserver(() => _sendTermResize());
    _termResizeObs.observe(container);
  };
  _termWs.onmessage = evt => {
    _term && _term.write(new Uint8Array(evt.data));
  };
  _termWs.onclose = evt => {
    if (evt.code === 4401) {
      showToast('Session expired \u2014 please log in again', 'error');
      closeTerminal();
    } else if (evt.code === 4400) {
      showToast('Invalid exec user', 'error');
    } else if (!evt.wasClean) {
      _term && _term.write('\r\n\x1b[31m[connection lost]\x1b[0m\r\n');
    }
  };
  _termWs.onerror = () => {
    _term && _term.write('\r\n\x1b[31m[could not connect to container]\x1b[0m\r\n');
  };
}

function _sendTermResize() {
  if (!_term || !_fitAddon || !_termWs || _termWs.readyState !== WebSocket.OPEN) return;
  _fitAddon.fit();
  _termWs.send(JSON.stringify({ type: 'resize', cols: _term.cols, rows: _term.rows }));
}

function _closeTerminalWs() {
  if (_termResizeObs) { _termResizeObs.disconnect(); _termResizeObs = null; }
  if (_termWs) { _termWs.close(); _termWs = null; }
  if (_term) { _term.dispose(); _term = null; }
  _fitAddon = null;
}

// ---------------------------------------------------------------------------
// DOMContentLoaded setup — form handlers and modal backdrop clicks
// ---------------------------------------------------------------------------

document.addEventListener('DOMContentLoaded', function() {
  // Restore view from URL on hard reload
  initFromUrl();

  // Create-compartment form
  document.getElementById('create-compartment-form').addEventListener('submit', async function(e) {
    e.preventDefault();
    const btn = this.querySelector('button[type="submit"]');
    if (btn.disabled) return;
    btn.disabled = true;
    try {
      const fd = new FormData(this);
      const resp = await jsonFetch('POST', '/api/compartments', Object.fromEntries(fd));
      const result = await resp.json().catch(() => ({}));
      if (resp.ok) {
        hideModal('create-compartment-modal');
        htmx.ajax('GET', '/api/compartments', { target: '#compartment-list', swap: 'innerHTML' });
        if (result.id) loadCompartment(result.id);
        showToast('Compartment created successfully');
      } else {
        showToast(result.detail || 'Failed to create compartment', 'error');
      }
    } finally {
      btn.disabled = false;
    }
  });

  // Import bundle form — uses fetch (not HTMX) for multipart/form-data
  document.getElementById('import-form').addEventListener('submit', async function(e) {
    e.preventDefault();
    const compartmentId = document.getElementById('import-compartment-id').value.trim();
    const fileInput = document.getElementById('import-file');
    const warningsEl = document.getElementById('import-warnings');
    const submitBtn = document.getElementById('import-submit');

    if (!fileInput.files.length) { showToast('Select a .quadlets file', 'error'); return; }

    const fd = new FormData();
    fd.append('compartment_id', compartmentId);
    fd.append('file', fileInput.files[0]);

    submitBtn.disabled = true;
    submitBtn.textContent = 'Importing…';
    warningsEl.classList.add('hidden');
    warningsEl.innerHTML = '';

    try {
      const resp = await fetch('/api/compartments/import', {
        method: 'POST',
        body: fd,
        headers: { 'X-CSRF-Token': getCsrfToken() },
      });
      const data = await resp.json();
      if (!resp.ok) {
        showToast(data.detail || 'Import failed', 'error');
        return;
      }
      // Show any warnings (e.g. skipped volume mounts)
      const warnings = data.import_warnings || [];
      if (warnings.length) {
        warningsEl.innerHTML = '<strong class="text-yellow-200">Warnings:</strong>' +
          warnings.map(w => `<div>• ${w}</div>`).join('');
        warningsEl.classList.remove('hidden');
        // Keep modal open so user can read warnings; they can close manually
      } else {
        hideModal('import-modal');
        this.reset();
      }
      // Refresh sidebar and load the new compartment
      htmx.ajax('GET', '/api/compartments', {
        target: '#compartment-list', swap: 'innerHTML',
        headers: { 'HX-Request': 'true' },
      });
      loadCompartment(compartmentId);
      showToast(`Compartment '${compartmentId}' imported`);
    } catch (err) {
      showToast('Network error during import', 'error');
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = 'Import';
    }
  });

  // Close modals on backdrop click
  ['create-compartment-modal', 'add-container-modal', 'add-volume-modal', 'import-modal',
   'volume-browser-modal', 'status-modal', 'quadlets-modal', 'selinux-modal'].forEach(id => {
    document.getElementById(id)?.addEventListener('click', function(e) {
      if (e.target === this) hideModal(id);
    });
  });
});
