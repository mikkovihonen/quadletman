// Consolidated view polling — replaces per-endpoint setInterval and HTMX polling.
// Depends on: metrics.js (drawSparkline, fmtBytes, setText), requests.js (getCsrfToken)

/**
 * ViewPoller — single polling loop for a view.
 *
 * Reads poll_interval and disk_poll_interval from the first JSON response
 * so the backend controls cadence.  The first tick always includes disk data.
 *
 * @param {Object} opts
 * @param {string} opts.url          Base URL to poll (e.g. /api/dashboard/poll)
 * @param {function} opts.onData     Called every tick with the parsed JSON
 * @param {function} [opts.onDisk]   Called when disk data is present
 */
function ViewPoller({ url, onData, onDisk }) {
  let _timer = null;
  let _tick = 0;
  let _pollMs = 5000;
  let _diskEvery = 12;
  let _stopped = false;

  function _cleanupListener(e) {
    if (e.target && e.target.id === 'main-content') {
      stop();
      document.removeEventListener('htmx:beforeSwap', _cleanupListener);
    }
  }

  async function _poll() {
    if (_stopped) return;
    _tick++;
    const includeDisk = _tick === 1 || _tick % _diskEvery === 0;
    const sep = url.includes('?') ? '&' : '?';
    const fetchUrl = includeDisk ? url + sep + 'include_disk=true' : url;
    try {
      const r = await fetch(fetchUrl, { redirect: 'manual' });
      if (r.type === 'opaqueredirect' || r.status === 401) {
        stop();
        window.location.href = '/login';
        return;
      }
      if (!r.ok) return;
      const data = await r.json();
      // Adapt intervals from server response (first response reconfigures the timer)
      if (_tick === 1 && data.poll_interval && data.disk_poll_interval) {
        const newMs = data.poll_interval * 1000;
        const diskMs = data.disk_poll_interval * 1000;
        _diskEvery = Math.max(1, Math.round(diskMs / newMs));
        if (newMs !== _pollMs) {
          _pollMs = newMs;
          // Restart the interval with the server-provided cadence
          if (_timer) {
            clearInterval(_timer);
            _timer = setInterval(_poll, _pollMs);
          }
        }
      }
      onData(data);
      if (includeDisk && data.disk != null && onDisk) {
        onDisk(data.disk);
      }
    } catch (_) {
      // Network error — skip this tick silently
    }
  }

  function start() {
    _stopped = false;
    _tick = 0;
    _poll();
    _timer = setInterval(_poll, _pollMs);
    document.addEventListener('htmx:beforeSwap', _cleanupListener);
  }

  function stop() {
    _stopped = true;
    if (_timer) {
      clearInterval(_timer);
      _timer = null;
    }
  }

  return { start, stop };
}

// ---------------------------------------------------------------------------
// Status badge rendering (client-side, mirrors status_badges.html)
// ---------------------------------------------------------------------------

/**
 * Render container status badges into the #status-{compartmentId} element.
 * @param {string} compartmentId
 * @param {Array} statuses  Array of {container, active_state, sub_state, load_state, unit_file_state}
 * @param {Array} [pendingOps]  Array of {op_type, status} — pending/running operations
 */
function renderStatusBadges(compartmentId, statuses, pendingOps) {
  const el = document.getElementById('status-' + compartmentId);
  if (!el) return;

  // Build lookup: container_name → pending op for per-container overrides.
  // Compartment-level ops (start/stop/restart/resync) apply to all containers.
  const pendingByContainer = {};
  let compartmentWideOp = null;
  if (pendingOps) {
    pendingOps.forEach(op => {
      if (op.container_name) {
        pendingByContainer[op.container_name] = op;
      } else {
        compartmentWideOp = op;
      }
    });
  }

  if (!statuses || statuses.length === 0) {
    el.innerHTML = '<span class="qm-status-inline qm-mono-sm qm-text-dimmer">'
      + '<span class="qm-dot-sm qm-dot-loading animate-pulse"></span>'
      + t('fetching\u2026') + '</span>';
    return;
  }
  const parts = statuses.map(s => {
    // Check if this container has a pending operation that overrides its badge
    const pending = pendingByContainer[s.container] || compartmentWideOp;

    let btnClass, dotClasses, label;
    if (pending) {
      const opLabels = {
        start: t('starting\u2026'), stop: t('stopping\u2026'),
        restart: t('restarting\u2026'), resync: t('resyncing\u2026'),
        start_container: t('starting\u2026'), stop_container: t('stopping\u2026'),
      };
      btnClass = 'qm-status-badge-transition';
      dotClasses = 'qm-dot-warn animate-pulse';
      label = opLabels[pending.op_type] || pending.op_type;
    } else if (s.active_state === 'active') {
      btnClass = 'qm-status-badge-active';
      dotClasses = 'qm-dot-green';
      label = t(s.sub_state) || s.sub_state;
    } else if (s.active_state === 'failed') {
      btnClass = 'qm-status-badge-failed';
      dotClasses = 'qm-dot-danger';
      label = t('failed');
    } else if (s.active_state === 'activating' || s.active_state === 'deactivating') {
      btnClass = 'qm-status-badge-transition';
      dotClasses = 'qm-dot-warn animate-pulse';
      label = t(s.active_state) || s.active_state;
    } else if (s.load_state === 'not-found') {
      btnClass = 'qm-status-badge-not-found';
      dotClasses = 'qm-dot-loading';
      label = t('not loaded');
    } else if (s.active_state === 'unknown') {
      btnClass = 'qm-status-badge-unknown';
      dotClasses = 'qm-dot-loading qm-opacity-0';
      label = t('unknown');
    } else {
      btnClass = 'qm-status-badge-inactive';
      dotClasses = 'qm-dot-loading';
      label = t(s.active_state) || s.active_state;
    }
    let autostart = '';
    if (s.unit_file_state === 'enabled') {
      autostart = '<span class="qm-autostart-on" title="' + t('autostart enabled') + '">\u23FB</span>';
    } else if (s.unit_file_state === 'disabled' || s.unit_file_state === 'masked') {
      autostart = '<span class="qm-autostart-off" title="' + s.unit_file_state + '">\u23FB</span>';
    }
    const cid = _escAttr(compartmentId);
    const cname = _escAttr(s.container);
    return '<span class="qm-status-inline qm-mono-sm">'
      + '<span class="qm-text-muted">' + _esc(s.container) + '</span>'
      + '<button onclick="showStatusModal(\'' + cid + '\', \'' + cname + '\')" class="qm-status-badge ' + btnClass + '">'
      + '<span class="qm-dot-sm ' + dotClasses + '"></span>'
      + _esc(label) + autostart
      + '</button></span>';
  });
  el.innerHTML = parts.join('\n');
}

/**
 * Update lifecycle button visibility based on current container statuses.
 * Single source of truth — called after every poll and after HTMX swaps.
 * @param {string} compartmentId
 * @param {Array} statuses  Array of {container, active_state, unit_file_state, ...}
 * @param {Array} [pendingOps]  Pending/running operations
 */
function updateLifecycleButtons(compartmentId, statuses, pendingOps) {
  const wrap = document.getElementById('lifecycle-btns-' + compartmentId);
  if (!wrap) return;

  const hasContainers = statuses && statuses.length > 0;
  const running = hasContainers
    ? statuses.filter(s => s.active_state === 'active' || s.active_state === 'activating').length
    : 0;
  const noneRunning = running === 0;
  const anyRunning = running > 0;
  const hasPending = pendingOps && pendingOps.length > 0;
  const allEnabled = hasContainers && statuses.every(s => s.unit_file_state === 'enabled');

  wrap.querySelectorAll('[data-action]').forEach(btn => {
    const action = btn.dataset.action;
    let visible = false;
    switch (action) {
      case 'start':    visible = hasContainers && noneRunning && !hasPending; break;
      case 'stop':     visible = hasContainers && anyRunning && !hasPending; break;
      case 'restart':  visible = hasContainers && !hasPending; break;
      case 'disable':  visible = allEnabled; break;
      case 'enable':   visible = !allEnabled; break;
    }
    btn.hidden = !visible;
  });

  // Per-container start/stop buttons
  if (statuses) {
    statuses.forEach(s => {
      const isRunning = s.active_state === 'active' || s.active_state === 'activating';
      document.querySelectorAll('[data-container="' + _escAttr(s.container) + '"]').forEach(btn => {
        if (btn.dataset.action === 'container-stop') btn.hidden = !isRunning;
        if (btn.dataset.action === 'container-start') btn.hidden = isRunning;
      });
    });
  }
}

/** Minimal HTML escaper for untrusted text inserted into innerHTML. */
function _esc(s) {
  if (!s) return '';
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

/** Escape a string for use inside a single-quoted HTML attribute (onclick='...'). */
function _escAttr(s) {
  if (!s) return '';
  return s.replace(/\\/g, '\\\\').replace(/'/g, "\\'").replace(/</g, '\\x3c').replace(/>/g, '\\x3e');
}

// ---------------------------------------------------------------------------
// Status dot rendering (sidebar)
// ---------------------------------------------------------------------------

/**
 * Update sidebar status dots from poll data.
 * @param {Array} dots  Array of {compartment_id, color, title}
 * @param {Object} [pendingOps]  Map of compartment_id → [{op_type, status}]
 */
function renderStatusDots(dots, pendingOps) {
  if (!dots) return;
  dots.forEach(d => {
    const el = document.getElementById('cmp-dot-' + d.compartment_id);
    if (el) {
      const hasPending = pendingOps && pendingOps[d.compartment_id];
      if (hasPending) {
        el.className = 'qm-dot qm-dot-warn animate-pulse inline-block';
        el.title = d.title + ' \u2014 ' + t('operation in progress');
      } else {
        el.className = 'qm-dot ' + d.color + ' inline-block';
        el.title = d.title;
      }
    }
  });
}
