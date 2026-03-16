// Log streaming via EventSource (SSE)

let _logEvtSource = null;
let _logCompartmentId = null;
let _logContainerId = null;
let _inspectLoaded = false;

const _LOG_TAB_ACTIVE = 'text-xs font-mono px-3 py-1.5 transition text-white border-b-2 border-blue-500';
const _LOG_TAB_INACTIVE = 'text-xs font-mono px-3 py-1.5 transition text-gray-400 hover:text-white';

function _logTab(tab) {
  const logsBtn = document.getElementById('log-tab-logs-btn');
  const inspectBtn = document.getElementById('log-tab-inspect-btn');
  const logOutput = document.getElementById('log-output');
  const inspectPanel = document.getElementById('log-inspect-panel');

  if (tab === 'logs') {
    logsBtn.className = _LOG_TAB_ACTIVE;
    inspectBtn.className = _LOG_TAB_INACTIVE;
    logOutput.classList.remove('hidden');
    inspectPanel.classList.add('hidden');
  } else {
    logsBtn.className = _LOG_TAB_INACTIVE;
    inspectBtn.className = _LOG_TAB_ACTIVE;
    logOutput.classList.add('hidden');
    inspectPanel.classList.remove('hidden');
    if (!_inspectLoaded) {
      _inspectLoaded = true;
      const body = document.getElementById('log-inspect-body');
      body.innerHTML = '<p class="text-sm text-gray-500 py-4 text-center">Loading…</p>';
      htmx.ajax('GET', `/api/compartments/${_logCompartmentId}/containers/${_logContainerId}/inspect`, {
        target: '#log-inspect-body',
        swap: 'innerHTML',
        headers: { 'HX-Request': 'true' },
      });
    }
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

  // Show inspect tab only when a container id is available
  const inspectBtn = document.getElementById('log-tab-inspect-btn');
  if (inspectBtn) inspectBtn.classList.toggle('hidden', !containerId);

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

  const inspectBtn = document.getElementById('log-tab-inspect-btn');
  if (inspectBtn) inspectBtn.classList.add('hidden');

  _logTab('logs');

  _openLogStream(
    `${compartmentId} / journal -xe`,
    `/api/compartments/${compartmentId}/journal`
  );
}
