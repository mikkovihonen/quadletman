// Terminal via xterm.js + WebSocket

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
    compartmentId + ' / ' + containerName + ' \u2014 ' + t('terminal');
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
      showToast(t('Session expired \u2014 please log in again'), 'error');
      closeTerminal();
    } else if (evt.code === 4400) {
      showToast(t('Invalid exec user'), 'error');
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
