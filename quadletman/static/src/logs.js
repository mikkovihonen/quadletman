// Log streaming via EventSource (SSE)

let _logEvtSource = null;
let _logCompartmentId = null;
let _logContainerId = null;
let _inspectLoaded = false;
let _tcpLoaded = false;
let _logIsAgent = false;

const _LOG_TAB_ACTIVE = 'text-xs font-mono px-3 py-1.5 transition text-white border-b-2 border-blue-500';
const _LOG_TAB_INACTIVE = 'text-xs font-mono px-3 py-1.5 transition text-gray-400 hover:text-white';
const _LOG_OUTPUT_CLASSES = 'log-output flex-1 overflow-y-auto p-4 text-green-400 text-xs whitespace-pre-wrap';

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
    const wasHidden = btnEl.classList.contains('hidden');
    if (name === tab) {
      btnEl.className = _LOG_TAB_ACTIVE;
      panelEl.classList.remove('hidden');
    } else {
      btnEl.className = _LOG_TAB_INACTIVE;
      panelEl.classList.add('hidden');
    }
    if (wasHidden) btnEl.classList.add('hidden');
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
  output.className = _LOG_OUTPUT_CLASSES;
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

function _setAgentRestartBtn(visible) {
  const btn = document.getElementById('log-agent-restart-btn');
  if (btn) btn.classList.toggle('hidden', !visible);
  _logIsAgent = visible;
}

function showLogs(compartmentId, containerName, containerId) {
  _logCompartmentId = compartmentId;
  _logContainerId = containerId || null;
  _inspectLoaded = false;
  _tcpLoaded = false;
  _setAgentRestartBtn(false);

  // Reset to logs tab first (overwrites className), then toggle extra tabs
  _logTab('logs');

  const inspectBtn = document.getElementById('log-tab-inspect-btn');
  if (inspectBtn) inspectBtn.classList.toggle('hidden', !containerId);
  const tcpBtn = document.getElementById('log-tab-tcp-btn');
  if (tcpBtn) tcpBtn.classList.toggle('hidden', !containerId);

  _openLogStream(
    `${compartmentId} / ${containerName}`,
    `/api/compartments/${compartmentId}/containers/${containerName}/logs`
  );
}

function stopLogs() {
  if (_logEvtSource) { _logEvtSource.close(); _logEvtSource = null; }
}

function showAgentLogs(compartmentId) {
  _logCompartmentId = compartmentId;
  _logContainerId = null;
  _inspectLoaded = false;
  _tcpLoaded = false;
  _setAgentRestartBtn(true);

  // Reset to logs tab first (overwrites className), then hide extra tabs
  _logTab('logs');

  const inspectBtn = document.getElementById('log-tab-inspect-btn');
  if (inspectBtn) inspectBtn.classList.add('hidden');
  const tcpBtn = document.getElementById('log-tab-tcp-btn');
  if (tcpBtn) tcpBtn.classList.add('hidden');

  _openLogStream(
    `${compartmentId} / agent`,
    `/api/compartments/${compartmentId}/agent/logs`
  );
}

function restartAgent() {
  if (!_logCompartmentId || !_logIsAgent) return;
  jsonFetch('POST', `/api/compartments/${_logCompartmentId}/agent/restart`)
    .then(() => {
      // Re-open the log stream to pick up new output after restart
      _openLogStream(
        `${_logCompartmentId} / agent`,
        `/api/compartments/${_logCompartmentId}/agent/logs`
      );
    });
}

function showJournalXE(compartmentId) {
  _logCompartmentId = compartmentId;
  _logContainerId = null;
  _inspectLoaded = false;
  _tcpLoaded = false;
  _setAgentRestartBtn(false);

  // Reset to logs tab first (overwrites className), then hide extra tabs
  _logTab('logs');

  const inspectBtn = document.getElementById('log-tab-inspect-btn');
  if (inspectBtn) inspectBtn.classList.add('hidden');
  const tcpBtn = document.getElementById('log-tab-tcp-btn');
  if (tcpBtn) tcpBtn.classList.add('hidden');

  _openLogStream(
    `${compartmentId} / journal -xe`,
    `/api/compartments/${compartmentId}/journal`
  );
}
