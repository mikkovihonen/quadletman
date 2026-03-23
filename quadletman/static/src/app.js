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
  ['create-compartment-modal', 'add-container-modal', 'add-volume-modal', 'import-modal',
   'volume-browser-modal', 'status-modal', 'quadlets-modal', 'selinux-modal'].forEach(id => {
    document.getElementById(id)?.addEventListener('click', function(e) {
      if (e.target === this) hideModal(id);
    });
  });
});
