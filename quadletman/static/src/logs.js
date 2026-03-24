// Log streaming via EventSource (SSE)

let _logEvtSource = null;
let _logCompartmentId = null;
let _logContainerId = null;
let _inspectLoaded = false;
let _tcpLoaded = false;

const _LOG_TAB_ACTIVE = 'text-xs font-mono px-3 py-1.5 transition text-white border-b-2 border-blue-500';
const _LOG_TAB_INACTIVE = 'text-xs font-mono px-3 py-1.5 transition text-gray-400 hover:text-white';

function _logTab(tab) {
  const tabs = {
    logs: { btn: 'log-tab-logs-btn', panel: 'log-output' },
    inspect: { btn: 'log-tab-inspect-btn', panel: 'log-inspect-panel' },
    tcp: { btn: 'log-tab-tcp-btn', panel: 'log-tcp-panel' },
  };

  for (const [name, { btn, panel }] of Object.entries(tabs)) {
    const btnEl = document.getElementById(btn);
    const panelEl = document.getElementById(panel);
    if (!btnEl || !panelEl) continue;
    if (name === tab) {
      btnEl.className = _LOG_TAB_ACTIVE;
      panelEl.classList.remove('hidden');
    } else {
      btnEl.className = _LOG_TAB_INACTIVE;
      panelEl.classList.add('hidden');
    }
  }

  // Lazy-load inspect tab
  if (tab === 'inspect' && !_inspectLoaded) {
    _inspectLoaded = true;
    const body = document.getElementById('log-inspect-body');
    body.innerHTML = '<p class="text-sm text-gray-500 py-4 text-center">Loading…</p>';
    htmx.ajax('GET', `/api/compartments/${_logCompartmentId}/containers/${_logContainerId}/inspect`, {
      target: '#log-inspect-body',
      swap: 'innerHTML',
      headers: { 'HX-Request': 'true' },
    });
  }

  // Lazy-load TCP tab (always refresh — connections change)
  if (tab === 'tcp') {
    const body = document.getElementById('log-tcp-body');
    body.innerHTML = '<p class="text-sm text-gray-500 py-4 text-center">Loading…</p>';
    htmx.ajax('GET', `/api/compartments/${_logCompartmentId}/containers/${_logContainerId}/tcp`, {
      target: '#log-tcp-body',
      swap: 'innerHTML',
      headers: { 'HX-Request': 'true' },
    });
  }
}

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

function showLogs(compartmentId, containerName, containerId) {
  _logCompartmentId = compartmentId;
  _logContainerId = containerId || null;
  _inspectLoaded = false;
  _tcpLoaded = false;

  // Show inspect and TCP tabs only when a container id is available
  const inspectBtn = document.getElementById('log-tab-inspect-btn');
  if (inspectBtn) inspectBtn.classList.toggle('hidden', !containerId);
  const tcpBtn = document.getElementById('log-tab-tcp-btn');
  if (tcpBtn) tcpBtn.classList.toggle('hidden', !containerId);

  // Reset to logs tab
  _logTab('logs');

  _openLogStream(
    `${compartmentId} / ${containerName}`,
    `/api/compartments/${compartmentId}/containers/${containerName}/logs`
  );
}

function stopLogs() {
  if (_logEvtSource) { _logEvtSource.close(); _logEvtSource = null; }
}

function showJournalXE(compartmentId) {
  _logCompartmentId = compartmentId;
  _logContainerId = null;
  _inspectLoaded = false;
  _tcpLoaded = false;

  const inspectBtn = document.getElementById('log-tab-inspect-btn');
  if (inspectBtn) inspectBtn.classList.add('hidden');
  const tcpBtn = document.getElementById('log-tab-tcp-btn');
  if (tcpBtn) tcpBtn.classList.add('hidden');

  _logTab('logs');

  _openLogStream(
    `${compartmentId} / journal -xe`,
    `/api/compartments/${compartmentId}/journal`
  );
}
