// Alpine component: image unit create/edit form
// Defined in <head> so it is available before HTMX-loaded fragments are
// processed by Alpine's MutationObserver.

function imageUnitForm(compartmentId, imageUnitId) {
  return {
    ...configFileMixin(compartmentId, 'image', () => imageUnitId),
    compartmentId,
    imageUnitId,
    imageTags: [],
    globalArgs: [],
    podmanArgs: [],
    init() {
      const el = this.$el;
      const d = JSON.parse(el.dataset.init || '{}');
      this.imageTags = d.imageTags ?? [];
      this.globalArgs = d.globalArgs ?? [];
      this.podmanArgs = d.podmanArgs ?? [];
      this.initConfigFile('auth_file', d.authFile ?? '', 'raw');
      this.initConfigFile('containers_conf_module', d.containersConfModule ?? '', 'raw');
    },
    async submitForm(form) {
      clearFieldErrors(form);
      if (!form.checkValidity()) {
        form.reportValidity();
        return;
      }
      const fd = new FormData(form);
      const data = {
        qm_name: fd.get('qm_name'),
        image: fd.get('image') || '',
        auth_file: fd.get('auth_file') || '',
        cert_dir: fd.get('cert_dir') || '',
        creds: fd.get('creds') || '',
        tls_verify: fd.get('tls_verify') === 'true',
        all_tags: fd.get('all_tags') === 'true',
        arch: fd.get('arch') || '',
        os: fd.get('os') || '',
        variant: fd.get('variant') || '',
        image_tags: this.imageTags.filter(t => t.trim()),
        retry: fd.get('retry') || '',
        retry_delay: fd.get('retry_delay') || '',
        containers_conf_module: fd.get('containers_conf_module') || '',
        global_args: this.globalArgs.filter(a => a.trim()),
        podman_args: this.podmanArgs.filter(a => a.trim()),
        service_name: fd.get('service_name') || '',
        decryption_key: fd.get('decryption_key') || '',
        policy: fd.get('policy') || '',
      };
      const url = this.imageUnitId
        ? `/api/compartments/${this.compartmentId}/image-units/${this.imageUnitId}`
        : `/api/compartments/${this.compartmentId}/image-units`;
      const resp = await jsonFetch(this.imageUnitId ? 'PUT' : 'POST', url, data);
      if (resp.ok) {
        hideModal('image-unit-modal');
        htmx.ajax('GET', `/api/compartments/${this.compartmentId}`, {
          target: '#main-content', swap: 'innerHTML',
          headers: { 'HX-Request': 'true' },
        });
      } else {
        const err = await resp.json().catch(() => ({}));
        showFieldErrors(form, err.detail);
        showApiError(err, t('Failed to save image unit'));
      }
    },
  };
}
