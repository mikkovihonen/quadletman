// Alpine component: pod create/edit form
// Defined in <head> so it is available before HTMX-loaded fragments are
// processed by Alpine's MutationObserver.

function podForm(compartmentId, podId) {
  return {
    compartmentId,
    podId,
    activeTab: 1,
    publishPorts: [],
    volumes: [],
    globalArgs: [],
    podmanArgs: [],
    dns: [],
    dnsSearch: [],
    dnsOption: [],
    addHost: [],
    uidMap: [],
    gidMap: [],
    networkAliases: [],
    labelPairs: [],
    init() {
      const el = this.$el;
      const d = JSON.parse(el.dataset.init || '{}');
      this.publishPorts = d.publishPorts ?? [];
      this.volumes = d.volumes ?? [];
      this.globalArgs = d.globalArgs ?? [];
      this.podmanArgs = d.podmanArgs ?? [];
      this.dns = d.dns ?? [];
      this.dnsSearch = d.dnsSearch ?? [];
      this.dnsOption = d.dnsOption ?? [];
      this.addHost = d.addHost ?? [];
      this.uidMap = d.uidMap ?? [];
      this.gidMap = d.gidMap ?? [];
      this.networkAliases = d.networkAliases ?? [];
      this.labelPairs = d.labelPairs ?? [];
    },
    async submitForm(form) {
      clearFieldErrors(form);
      // Validate all inputs including those on hidden tabs.
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
        qm_name: fd.get('qm_name'),
        network: fd.get('network') || '',
        hostname: fd.get('hostname') || '',
        exit_policy: fd.get('exit_policy') || '',
        stop_timeout: fd.get('stop_timeout') || '',
        shm_size: fd.get('shm_size') || '',
        ip: fd.get('ip') || '',
        ip6: fd.get('ip6') || '',
        user_ns: fd.get('user_ns') || '',
        sub_uid_map: fd.get('sub_uid_map') || '',
        sub_gid_map: fd.get('sub_gid_map') || '',
        containers_conf_module: fd.get('containers_conf_module') || '',
        service_name: fd.get('service_name') || '',
        publish_ports: this.publishPorts.filter(p => p.trim()),
        volumes: this.volumes.filter(v => v.trim()),
        global_args: this.globalArgs.filter(a => a.trim()),
        podman_args: this.podmanArgs.filter(a => a.trim()),
        dns: this.dns.filter(d => d.trim()),
        dns_search: this.dnsSearch.filter(d => d.trim()),
        dns_option: this.dnsOption.filter(d => d.trim()),
        add_host: this.addHost.filter(h => h.trim()),
        uid_map: this.uidMap.filter(m => m.trim()),
        gid_map: this.gidMap.filter(m => m.trim()),
        network_aliases: this.networkAliases.filter(a => a.trim()),
        labels: Object.fromEntries(this.labelPairs.filter(([k]) => k.trim())),
      };
      const url = this.podId
        ? `/api/compartments/${this.compartmentId}/pods/${this.podId}`
        : `/api/compartments/${this.compartmentId}/pods`;
      const resp = await jsonFetch(this.podId ? 'PUT' : 'POST', url, data);
      if (resp.ok) {
        hideModal('pod-modal');
        htmx.ajax('GET', `/api/compartments/${this.compartmentId}`, {
          target: '#main-content', swap: 'innerHTML',
          headers: { 'HX-Request': 'true' },
        });
      } else {
        const err = await resp.json().catch(() => ({}));
        showFieldErrors(form, err.detail);
        showApiError(err, t('Failed to save pod'));
      }
    },
  };
}
