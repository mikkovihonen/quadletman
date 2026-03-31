// Navigation helpers — SPA-style view switching with history API

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
function loadHelp(push = true) {
  _loadView('/api/help', '/help', { view: 'help' }, push);
}
function loadEvents(push = true) {
  showEventsModal();
}

let _eventsLogSource = null;

function _setEventsMode(mode) {
  const modal = document.getElementById('events-modal');
  const alpineEl = modal?.querySelector('[x-data]');
  if (alpineEl && alpineEl._x_dataStack) {
    alpineEl._x_dataStack[0].mode = mode;
  }
}

function showEventsModal() {
  ['#events-app-content', '#events-audit-content', '#events-config-content'].forEach(sel => {
    const el = document.querySelector(sel);
    if (el) delete el.dataset.loaded;
  });
  _stopEventsLog();
  _setEventsMode('logs');
  const modal = document.getElementById('events-modal');
  const alpineEl = modal?.querySelector('[x-data]');
  if (alpineEl && alpineEl._x_dataStack) {
    alpineEl._x_dataStack[0].tab = 1;
  }
  showModal('events-modal');
  _loadEventsTab(1);
}

function _stopEventsLog() {
  if (_eventsLogSource) { _eventsLogSource.close(); _eventsLogSource = null; }
}

function _loadEventsTab(tab) {
  // Tab 2 uses SSE streaming — handled separately
  if (tab === 2) {
    _startEventsLog();
    return;
  }
  // Stop SSE when switching away from tab 2
  if (tab !== 2) _stopEventsLog();

  const urls = { 1: '/api/events', 3: '/api/events/audit', 4: '/api/app/config' };
  const targets = { 1: '#events-app-content', 3: '#events-audit-content', 4: '#events-config-content' };
  const el = document.querySelector(targets[tab]);
  if (!el || el.dataset.loaded) return;
  el.dataset.loaded = '1';
  htmx.ajax('GET', urls[tab], {
    target: targets[tab],
    swap: 'innerHTML',
    headers: { 'HX-Request': 'true' },
  });
}

function _startEventsLog() {
  _stopEventsLog();
  const output = document.getElementById('events-systemd-output');
  if (!output) return;
  output.textContent = '';

  _eventsLogSource = new EventSource('/api/app/logs');
  _eventsLogSource.onmessage = (e) => {
    if (e.data.startsWith('__unavailable__:')) {
      const msg = e.data.slice('__unavailable__:'.length);
      output.className = 'flex-1 flex items-center justify-center px-8';
      output.innerHTML = '';
      const p = document.createElement('p');
      p.className = 'qm-loading qm-text-center';
      p.textContent = msg;
      output.appendChild(p);
      _stopEventsLog();
      return;
    }
    output.textContent += e.data + '\n';
    output.scrollTop = output.scrollHeight;
  };
  _eventsLogSource.onerror = () => {
    if (output.textContent) output.textContent += '\n[stream ended]';
    _stopEventsLog();
  };
}

function showAppConfig() {
  _stopEventsLog();
  _setEventsMode('config');
  const modal = document.getElementById('events-modal');
  const alpineEl = modal?.querySelector('[x-data]');
  if (alpineEl && alpineEl._x_dataStack) {
    alpineEl._x_dataStack[0].tab = 4;
  }
  const el = document.querySelector('#events-config-content');
  if (el) delete el.dataset.loaded;
  showModal('events-modal');
  _loadEventsTab(4);
}

// Restore correct view after hard reload, and handle browser back/forward.
function initFromUrl() {
  const path = window.location.pathname;
  const compMatch = path.match(/^\/compartments\/([^/]+)$/);
  if (compMatch) {
    loadCompartment(compMatch[1], false);
  } else if (path === '/events') {
    loadEvents(false);
  } else if (path === '/help') {
    loadHelp(false);
  } else {
    loadDashboard(false);
  }
}

window.addEventListener('popstate', (e) => {
  const state = e.state;
  if (!state) { loadDashboard(false); return; }
  if (state.view === 'compartment') loadCompartment(state.id, false);
  else if (state.view === 'events') loadEvents(false);
  else if (state.view === 'help') loadHelp(false);
  else loadDashboard(false);
});
