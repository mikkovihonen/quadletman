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

function showEventsModal() {
  ['#events-app-content', '#events-systemd-content', '#events-audit-content'].forEach(sel => {
    const el = document.querySelector(sel);
    if (el) delete el.dataset.loaded;
  });
  showModal('events-modal');
  _loadEventsTab(1);
}

function _loadEventsTab(tab) {
  const urls = { 1: '/api/events', 2: '/api/events/systemd', 3: '/api/events/audit' };
  const targets = { 1: '#events-app-content', 2: '#events-systemd-content', 3: '#events-audit-content' };
  const el = document.querySelector(targets[tab]);
  if (!el || el.dataset.loaded) return;
  el.dataset.loaded = '1';
  htmx.ajax('GET', urls[tab], {
    target: targets[tab],
    swap: 'innerHTML',
    headers: { 'HX-Request': 'true' },
  });
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
