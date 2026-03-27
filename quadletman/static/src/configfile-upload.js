// Reusable Alpine mixin for config file upload/preview/delete.
//
// Usage in an Alpine component:
//
//   function myForm(compartmentId, resourceId) {
//     return {
//       ...configFileMixin(compartmentId, 'container', resourceId),
//       init() {
//         this.initConfigFile('environment_file', initialPath, 'keyvalue');
//         this.initConfigFile('seccomp_profile', initialPath2, 'raw');
//       },
//       ...
//     };
//   }

function configFileMixin(compartmentId, resourceType, resourceId) {
  return {
    _cf: {},

    initConfigFile(fieldName, path, previewMode) {
      this._cf[fieldName] = {
        path: path || '',
        error: '',
        preview: null,
        loading: false,
        previewMode: previewMode || 'raw',
      };
      if (path) {
        this.loadConfigPreview(fieldName);
      }
    },

    cfPath(fieldName) {
      return this._cf[fieldName]?.path ?? '';
    },

    setCfPath(fieldName, value) {
      if (!this._cf[fieldName]) this.initConfigFile(fieldName, '', 'raw');
      this._cf[fieldName].path = value;
      this._cf[fieldName].preview = null;
      if (value) this.loadConfigPreview(fieldName);
    },

    cfError(fieldName) {
      return this._cf[fieldName]?.error ?? '';
    },

    cfPreview(fieldName) {
      return this._cf[fieldName]?.preview ?? null;
    },

    cfPreviewMode(fieldName) {
      return this._cf[fieldName]?.previewMode ?? 'raw';
    },

    async uploadConfigFile(fieldName, inputEl) {
      const file = inputEl.files[0];
      if (!file) return;
      const state = this._cf[fieldName];
      if (!state) return;
      const form = new FormData();
      form.append('file', file);
      state.error = '';
      state.preview = null;
      const rid = typeof resourceId === 'function' ? resourceId() : resourceId;
      const resp = await fetch(
        `/api/compartments/${compartmentId}/${resourceType}/${rid}/configfile/${fieldName}`,
        { method: 'POST', body: form, headers: { 'X-CSRF-Token': getCsrfToken() } }
      );
      if (resp.ok) {
        const data = await resp.json();
        state.path = data.path;
        await this.loadConfigPreview(fieldName);
      } else {
        const data = await resp.json().catch(() => ({}));
        state.error = formatApiError(data, t('Upload failed')).msg;
      }
      inputEl.value = '';
    },

    async deleteConfigFile(fieldName) {
      const state = this._cf[fieldName];
      if (!state) return;
      state.error = '';
      const rid = typeof resourceId === 'function' ? resourceId() : resourceId;
      const resp = await fetch(
        `/api/compartments/${compartmentId}/${resourceType}/${rid}/configfile/${fieldName}`,
        { method: 'DELETE', headers: { 'X-CSRF-Token': getCsrfToken() } }
      );
      if (resp.ok) {
        state.path = '';
        state.preview = null;
      } else {
        const data = await resp.json().catch(() => ({}));
        state.error = formatApiError(data, t('Delete failed')).msg;
      }
    },

    async loadConfigPreview(fieldName) {
      const state = this._cf[fieldName];
      if (!state || !state.path) { if (state) state.preview = null; return; }
      state.loading = true;
      state.error = '';
      try {
        const params = new URLSearchParams({
          path: state.path,
          preview: state.previewMode,
        });
        const resp = await fetch(
          `/api/compartments/${compartmentId}/configfile?${params}`,
          { headers: { 'X-CSRF-Token': getCsrfToken() } }
        );
        if (resp.ok) {
          const data = await resp.json();
          // For keyvalue mode, preview is data.lines array
          // For raw mode, preview is data.raw string
          state.preview = state.previewMode === 'keyvalue' ? data.lines : data.raw;
        } else {
          const data = await resp.json().catch(() => ({}));
          state.error = formatApiError(data, t('Could not load preview')).msg;
          state.preview = null;
        }
      } finally {
        state.loading = false;
      }
    },
  };
}
