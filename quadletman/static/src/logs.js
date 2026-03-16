// Log streaming via EventSource (SSE)

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
