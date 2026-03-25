// App initialisation — i18n helper, Alpine stores, Alpine components, and DOMContentLoaded setup

// ---------------------------------------------------------------------------
// Alpine stores — state that must survive HTMX outerHTML swaps.
// Registered on alpine:init so they are available before Alpine processes the DOM.
// ---------------------------------------------------------------------------

document.addEventListener('alpine:init', function () {
  // Collapse state for the process monitor unknown/known sections.
  // Keyed by compartment_id so each compartment has independent state.
  Alpine.store('processMonitor', {
    unknownOpen: {},
    knownOpen: {},
    isUnknownOpen(id)  { return this.unknownOpen[id] !== false; },
    isKnownOpen(id)    { return this.knownOpen[id]   === true;  },
    toggleUnknown(id)  { this.unknownOpen[id] = !this.isUnknownOpen(id); },
    toggleKnown(id)    { this.knownOpen[id]   = !this.isKnownOpen(id);   },
  });

  // Collapse state for the connection monitor sections (history, settings).
  // Keyed by compartment_id. History defaults open; settings defaults closed.
  Alpine.store('connectionMonitor', {
    historyOpen: {},
    settingsOpen: {},
    isHistoryOpen(id)  { return this.historyOpen[id]  === true;  },
    isSettingsOpen(id) { return this.settingsOpen[id] === true;  },
    toggleHistory(id)  { this.historyOpen[id]  = !this.isHistoryOpen(id);  },
    toggleSettings(id) { this.settingsOpen[id] = !this.isSettingsOpen(id); },
  });
});

// ---------------------------------------------------------------------------
// i18n helper
// ---------------------------------------------------------------------------

function t(key) { return (window.QM_I18N && window.QM_I18N[key]) || key; }

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
// Alpine component: process pattern editor (shared base)
// ---------------------------------------------------------------------------

function _patternEditorBase() {
  return {
    segments: [],
    originalSegments: [],
    editing: false,
    selStart: null,
    selEnd: null,
    hoverPos: null,
    regexInput: '.*',
    selectedWildcard: null,
    wildcardRegexInput: '',

    clickChar(segIdx, charIdx) {
      this.selectedWildcard = null;
      if (this.selStart === null || this.selEnd !== null) {
        this.selStart = { seg: segIdx, ch: charIdx };
        this.selEnd = null;
        this.hoverPos = null;
      } else {
        this.selEnd = { seg: segIdx, ch: charIdx };
        this.hoverPos = null;
      }
    },
    onHover(event) {
      if (this.selStart === null || this.selEnd !== null) {
        this.hoverPos = null;
        return;
      }
      var el = event.target;
      if (!el.hasAttribute || !el.hasAttribute('data-si')) { this.hoverPos = null; return; }
      var si = parseInt(el.getAttribute('data-si'));
      var ci = parseInt(el.getAttribute('data-ci'));
      this.hoverPos = { seg: si, ch: ci };
    },
    charState(segIdx, charIdx) {
      // Returns: 'selected', 'preview', or ''
      if (this.segments[segIdx].t !== 'l') return '';
      // Confirmed selection
      if (this.selStart && this.selEnd) {
        if (this.charInRange(segIdx, charIdx, this.selStart, this.selEnd)) return 'selected';
      }
      // Preview: range from selStart to hoverPos
      else if (this.selStart && this.hoverPos) {
        if (this.charInRange(segIdx, charIdx, this.selStart, this.hoverPos)) return 'preview';
      }
      // Just start clicked, no hover yet
      else if (this.selStart) {
        if (segIdx === this.selStart.seg && charIdx === this.selStart.ch) return 'preview';
      }
      return '';
    },
    charInRange(segIdx, charIdx, s, e) {
      if (s.seg > e.seg || (s.seg === e.seg && s.ch > e.ch)) { var tmp = s; s = e; e = tmp; }
      if (segIdx < s.seg || segIdx > e.seg) return false;
      if (segIdx === s.seg && segIdx === e.seg) return charIdx >= s.ch && charIdx <= e.ch;
      if (segIdx === s.seg) return charIdx >= s.ch;
      if (segIdx === e.seg) return charIdx <= e.ch;
      return true;
    },
    isCharSelected(segIdx, charIdx) {
      // Only used for confirmed selection (fallback for Alpine re-renders)
      if (!this.selStart || !this.selEnd) return false;
      if (this.segments[segIdx].t !== 'l') return false;
      var s = this.selStart, e = this.selEnd;
      if (s.seg > e.seg || (s.seg === e.seg && s.ch > e.ch)) { var tmp = s; s = e; e = tmp; }
      if (segIdx < s.seg || segIdx > e.seg) return false;
      if (segIdx === s.seg && segIdx === e.seg) return charIdx >= s.ch && charIdx <= e.ch;
      if (segIdx === s.seg) return charIdx >= s.ch;
      if (segIdx === e.seg) return charIdx <= e.ch;
      return true;
    },
    hasSelection() {
      return this.selStart !== null && this.selEnd !== null;
    },
    selectWildcard(segIdx) {
      this.selStart = null;
      this.selEnd = null;
      this.selectedWildcard = segIdx;
      this.wildcardRegexInput = this.segments[segIdx].r;
    },
    applyWildcard() {
      if (!this.selStart || !this.selEnd) return;
      var s = this.selStart, e = this.selEnd;
      if (s.seg > e.seg || (s.seg === e.seg && s.ch > e.ch)) { var tmp = s; s = e; e = tmp; }
      if (s.seg !== e.seg) { this.selStart = null; this.selEnd = null; return; }
      var seg = this.segments[s.seg];
      if (seg.t !== 'l') return;
      var text = seg.v;
      var before = text.substring(0, s.ch);
      var selected = text.substring(s.ch, e.ch + 1);
      var after = text.substring(e.ch + 1);
      var newSegs = [];
      for (var i = 0; i < s.seg; i++) newSegs.push(this.segments[i]);
      if (before) newSegs.push({ t: 'l', v: before });
      newSegs.push({ t: 'w', r: this.regexInput, o: selected });
      if (after) newSegs.push({ t: 'l', v: after });
      for (var j = s.seg + 1; j < this.segments.length; j++) newSegs.push(this.segments[j]);
      this.segments = newSegs;
      this.selStart = null;
      this.selEnd = null;
    },
    updateWildcard() {
      if (this.selectedWildcard === null) return;
      this.segments[this.selectedWildcard].r = this.wildcardRegexInput;
      this.selectedWildcard = null;
    },
    revertWildcard() {
      if (this.selectedWildcard === null) return;
      var seg = this.segments[this.selectedWildcard];
      var newSegs = [];
      for (var i = 0; i < this.segments.length; i++) {
        if (i === this.selectedWildcard) {
          var literal = seg.o;
          var prev = newSegs.length > 0 ? newSegs[newSegs.length - 1] : null;
          var next = i + 1 < this.segments.length ? this.segments[i + 1] : null;
          if (prev && prev.t === 'l') {
            prev.v += literal;
            if (next && next.t === 'l') { prev.v += next.v; i++; }
          } else {
            var merged = { t: 'l', v: literal };
            if (next && next.t === 'l') { merged.v += next.v; i++; }
            newSegs.push(merged);
          }
        } else {
          newSegs.push(this.segments[i]);
        }
      }
      this.segments = newSegs;
      this.selectedWildcard = null;
    },
    composedRegex() {
      return this.segments.map(function(seg) {
        if (seg.t === 'w') return seg.r;
        return seg.v.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
      }).join('');
    },
    cancel() {
      this.segments = JSON.parse(JSON.stringify(this.originalSegments));
      this.editing = false;
      this.selStart = null;
      this.selEnd = null;
      this.hoverPos = null;
      this.selectedWildcard = null;
    }
  };
}

// Return a complete Alpine data object for a new pattern editor.
// Called from x-data="initNewPatternData($el)" — the returned object IS the component state.
function initNewPatternData(el) {
  var data = JSON.parse(el.querySelector('.pm-init-data').textContent);
  var base = _patternEditorBase();
  base.compartmentId = data.compartmentId;
  base.processName = data.processName;
  base.peerCmdlines = data.peerCmdlines;
  base.segments = [{ t: 'l', v: data.cmdline }];
  base.originalSegments = [{ t: 'l', v: data.cmdline }];
  base.matchingCmdlines = function() {
    var regex = this.composedRegex();
    try { var re = new RegExp('^' + regex + '$'); } catch(e) { return []; }
    return this.peerCmdlines.filter(function(cmd) { return re.test(cmd); });
  };
  base.matchCount = function() { return this.matchingCmdlines().length; };
  base.saveNew = function() {
    if (this.matchCount() === 0) return;
    var regex = this.composedRegex();
    var segs = JSON.stringify(this.segments);
    var card = document.getElementById('process-monitor-card');
    htmx.ajax('POST',
      '/api/compartments/' + this.compartmentId + '/process-patterns',
      { target: card, swap: 'outerHTML', values: {
        process_name: this.processName,
        cmdline_pattern: regex,
        segments_json: segs
      }}
    );
  };
  return base;
}

// Return a complete Alpine data object for an existing pattern editor.
function initExistingPatternData(el) {
  var data = JSON.parse(el.querySelector('.pm-init-data').textContent);
  var segs = typeof data.segments === 'string' ? JSON.parse(data.segments) : data.segments;
  var base = _patternEditorBase();
  base.patternId = data.patternId;
  base.compartmentId = data.compartmentId;
  base.processName = data.processName;
  base.peerCmdlines = data.peerCmdlines || [];
  base.segments = JSON.parse(JSON.stringify(segs));
  base.originalSegments = JSON.parse(JSON.stringify(segs));
  base.matchingCmdlines = function() {
    var regex = this.composedRegex();
    try { var re = new RegExp('^' + regex + '$'); } catch(e) { return []; }
    return this.peerCmdlines.filter(function(cmd) { return re.test(cmd); });
  };
  base.matchCount = function() { return this.matchingCmdlines().length; };
  base.save = function() {
    if (this.matchCount() === 0) return;
    var regex = this.composedRegex();
    var segsJson = JSON.stringify(this.segments);
    var card = document.getElementById('process-monitor-card');
    htmx.ajax('POST',
      '/api/compartments/' + this.compartmentId + '/process-patterns/' + this.patternId,
      { target: card, swap: 'outerHTML', values: { cmdline_pattern: regex, segments_json: segsJson } }
    );
  };
  return base;
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
        showToast(t('Compartment created successfully'));
      } else {
        showApiError(result, t('Failed to create compartment'));
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

    if (!fileInput.files.length) { showToast(t('Select a .quadlets file'), 'error'); return; }

    const fd = new FormData();
    fd.append('compartment_id', compartmentId);
    fd.append('file', fileInput.files[0]);

    submitBtn.disabled = true;
    submitBtn.textContent = t('Importing…');
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
        showApiError(data, t('Import failed'));
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
      showToast(t("Compartment '%(id)s' imported").replace('%(id)s', compartmentId));
    } catch (err) {
      showToast(t('Network error during import'), 'error');
    } finally {
      submitBtn.disabled = false;
      submitBtn.textContent = t('Import');
    }
  });

  // Close modals on backdrop click
  ['create-compartment-modal', 'add-container-modal', 'add-volume-modal', 'edit-network-modal', 'import-modal',
   'volume-browser-modal', 'status-modal', 'quadlets-modal', 'selinux-modal'].forEach(id => {
    document.getElementById(id)?.addEventListener('click', function(e) {
      if (e.target === this) hideModal(id);
    });
  });
});
