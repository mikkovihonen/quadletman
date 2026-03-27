// Alpine component: build unit create/edit form
// Defined in <head> so it is available before HTMX-loaded fragments are
// processed by Alpine's MutationObserver.

function buildUnitForm(compartmentId, buildUnitId) {
  return {
    ...configFileMixin(compartmentId, 'build', () => buildUnitId),
    compartmentId,
    buildUnitId,
    activeTab: 1,
    containerfileContent: '',
    annotations: [],
    dnsServers: [],
    dnsSearch: [],
    dnsOption: [],
    envPairs: [],
    globalArgs: [],
    groupAdd: [],
    labelPairs: [],
    podmanArgs: [],
    secrets: [],
    volumes: [],
    buildArgPairs: [],
    init() {
      const el = this.$el;
      const d = JSON.parse(el.dataset.init || '{}');
      this.containerfileContent = d.containerfileContent ?? '';
      this.annotations = d.annotation ?? [];
      this.dnsServers = d.dns ?? [];
      this.dnsSearch = d.dnsSearch ?? [];
      this.dnsOption = d.dnsOption ?? [];
      this.envPairs = d.envPairs ?? [];
      this.globalArgs = d.globalArgs ?? [];
      this.groupAdd = d.groupAdd ?? [];
      this.labelPairs = d.labelPairs ?? [];
      this.podmanArgs = d.podmanArgs ?? [];
      this.secrets = d.secret ?? [];
      this.volumes = d.volume ?? [];
      this.buildArgPairs = d.buildArgPairs ?? [];
      this.initConfigFile('ignore_file', d.ignoreFile ?? '', 'raw');
    },
    async submitForm(form) {
      clearFieldErrors(form);
      var firstInvalid = null;
      for (var el of form.elements) {
        if (el.checkValidity && !el.checkValidity()) {
          firstInvalid = el;
          break;
        }
      }
      if (firstInvalid) {
        var tabPanel = firstInvalid.closest('[x-show^="activeTab"]');
        if (tabPanel) {
          var match = tabPanel.getAttribute('x-show').match(/activeTab === (\d+)/);
          if (match) this.activeTab = parseInt(match[1], 10);
        }
        await new Promise(r => setTimeout(r, 50));
        form.reportValidity();
        return;
      }
      const fd = new FormData(form);
      const data = {
        qm_name: fd.get('name'),
        image_tag: fd.get('image_tag'),
        qm_containerfile_content: this.containerfileContent,
        target: fd.get('target') || '',
        network: fd.get('network') || '',
        arch: fd.get('arch') || '',
        variant: fd.get('variant') || '',
        pull: fd.get('pull') || '',
        tls_verify: fd.get('tls_verify') === 'true',
        force_rm: fd.get('force_rm') === 'true',
        retry: fd.get('retry') || '',
        retry_delay: fd.get('retry_delay') || '',
        service_name: fd.get('service_name') || '',
        ignore_file: this.cfPath('ignore_file'),
        annotation: this.annotations.filter(a => a.trim()),
        dns: this.dnsServers.filter(d => d.trim()),
        dns_option: this.dnsOption.filter(d => d.trim()),
        dns_search: this.dnsSearch.filter(d => d.trim()),
        env: Object.fromEntries(this.envPairs.filter(([k]) => k.trim())),
        global_args: this.globalArgs.filter(a => a.trim()),
        group_add: this.groupAdd.filter(g => g.trim()),
        label: Object.fromEntries(this.labelPairs.filter(([k]) => k.trim())),
        podman_args: this.podmanArgs.filter(a => a.trim()),
        secret: this.secrets.filter(s => s.trim()),
        volume: this.volumes.filter(v => v.trim()),
        build_args: Object.fromEntries(this.buildArgPairs.filter(([k]) => k.trim())),
      };
      const url = this.buildUnitId
        ? `/api/compartments/${this.compartmentId}/build-units/${this.buildUnitId}`
        : `/api/compartments/${this.compartmentId}/build-units`;
      const resp = await jsonFetch(this.buildUnitId ? 'PUT' : 'POST', url, data);
      if (resp.ok) {
        hideModal('build-unit-modal');
        htmx.ajax('GET', `/api/compartments/${this.compartmentId}`, {
          target: '#main-content', swap: 'innerHTML',
          headers: { 'HX-Request': 'true' },
        });
      } else {
        const err = await resp.json().catch(() => ({}));
        showFieldErrors(form, err.detail);
        showApiError(err, t('Failed to save build unit'));
      }
    },
  };
}
