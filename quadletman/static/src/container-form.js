// Alpine component: container create/edit form
// Defined in <head> so it is available before HTMX-loaded fragments are
// processed by Alpine's MutationObserver.

function containerForm(compartmentId, containerId) {
  return {
    compartmentId,
    containerId,
    activeTab: 1,
    ports: [],
    envFilePath: '',
    envFileUploaded: false,
    envFilePreview: null,
    envFileLoading: false,
    envFileError: '',
    envPairs: [],
    volumeMounts: [],
    bindMounts: [],
    uidMaps: [],
    gidMaps: [],
    imageSource: 'registry',  // 'registry' | 'image_unit' | 'build_unit'
    registryImage: '',
    imageUnitRef: '',
    buildUnitRef: '',
    hasImageUnits: false,
    hasBuildUnits: false,
    healthEnabled: false,
    dropCaps: [],
    addCaps: [],
    sysctlPairs: [],
    dnsServers: [],
    dnsSearch: [],
    dnsOption: [],
    maskPaths: [],
    unmaskPaths: [],
    selectedSecrets: [],
    devices: [],
    networkAliases: [],
    init() {
      const el = this.$el;
      const d = JSON.parse(el.dataset.init || '{}');
      this.ports = d.ports ?? [];
      this.envPairs = d.envPairs ?? [];
      this.volumeMounts = d.volumeMounts ?? [];
      this.bindMounts = d.bindMounts ?? [];
      this.uidMaps = d.uidMap ?? [];
      this.gidMaps = d.gidMap ?? [];
      const imageUnits = d.imageUnits ?? [];
      const buildUnits = d.buildUnits ?? [];
      this.hasImageUnits = imageUnits.length > 0;
      this.hasBuildUnits = buildUnits.length > 0;
      const currentImage = d.image ?? '';
      const currentBuildUnit = d.buildUnitName ?? '';
      if (currentBuildUnit && buildUnits.includes(currentBuildUnit)) {
        this.imageSource = 'build_unit';
        this.buildUnitRef = currentBuildUnit;
      } else if (currentImage.endsWith('.image') && imageUnits.some(n => currentImage === n + '.image')) {
        this.imageSource = 'image_unit';
        this.imageUnitRef = currentImage;
      } else {
        this.imageSource = 'registry';
        this.registryImage = currentImage;
      }
      // Always pre-fill refs with the first available unit so that
      // switching tabs never leaves them blank.
      if (!this.imageUnitRef && imageUnits.length > 0) {
        this.imageUnitRef = imageUnits[0] + '.image';
      }
      if (!this.buildUnitRef && buildUnits.length > 0) {
        this.buildUnitRef = buildUnits[0];
      }
      this.healthEnabled = d.healthEnabled === true;
      this.dropCaps = d.dropCaps ?? [];
      this.addCaps = d.addCaps ?? [];
      this.sysctlPairs = d.sysctlPairs ?? [];
      this.dnsServers = d.dns ?? [];
      this.dnsSearch = d.dnsSearch ?? [];
      this.dnsOption = d.dnsOption ?? [];
      this.maskPaths = d.maskPaths ?? [];
      this.unmaskPaths = d.unmaskPaths ?? [];
      this.selectedSecrets = d.selectedSecrets ?? [];
      this.devices = d.devices ?? [];
      this.networkAliases = d.networkAliases ?? [];
      this.envFilePath = d.environmentFile ?? '';
      if (this.envFilePath) {
        this.envFileUploaded = true;
        this.loadEnvPreview();
      }
    },
    async _fetchErrMsg(resp, defaultMsg) {
      const data = await resp.json().catch(() => ({}));
      return data.detail || defaultMsg;
    },
    async uploadEnvFile(inputEl) {
      const file = inputEl.files[0];
      if (!file) return;
      const form = new FormData();
      form.append('file', file);
      this.envFileError = '';
      this.envFilePreview = null;
      const resp = await fetch(
        `/api/compartments/${this.compartmentId}/containers/${this.containerId}/envfile`,
        { method: 'POST', body: form, headers: { 'X-CSRF-Token': getCsrfToken() } }
      );
      if (resp.ok) {
        const data = await resp.json();
        this.envFilePath = data.path;
        this.envFileUploaded = true;
        await this.loadEnvPreview();
      } else {
        this.envFileError = await this._fetchErrMsg(resp, t('Upload failed'));
      }
      inputEl.value = '';
    },
    async deleteEnvFile() {
      this.envFileError = '';
      const resp = await fetch(
        `/api/compartments/${this.compartmentId}/containers/${this.containerId}/envfile`,
        { method: 'DELETE', headers: { 'X-CSRF-Token': getCsrfToken() } }
      );
      if (resp.ok) {
        this.envFilePath = '';
        this.envFileUploaded = false;
        this.envFilePreview = null;
      } else {
        this.envFileError = await this._fetchErrMsg(resp, t('Delete failed'));
      }
    },
    async loadEnvPreview() {
      if (!this.envFilePath) { this.envFilePreview = null; return; }
      this.envFileLoading = true;
      this.envFileError = '';
      try {
        const resp = await fetch(
          `/api/compartments/${this.compartmentId}/envfile?path=${encodeURIComponent(this.envFilePath)}`,
          { headers: { 'X-CSRF-Token': getCsrfToken() } }
        );
        if (resp.ok) {
          const data = await resp.json();
          this.envFilePreview = data.lines;
        } else {
          this.envFileError = await this._fetchErrMsg(resp, t('Could not load preview'));
          this.envFilePreview = null;
        }
      } finally {
        this.envFileLoading = false;
      }
    },
    filteredVolumes() {
      return JSON.stringify(this.volumeMounts.filter(function(vm) {
        return vm.volume_id && vm.container_path;
      }));
    },
    resolvedImage() {
      if (this.imageSource === 'image_unit') return this.imageUnitRef;
      // For build units, the image tag is set by the build unit itself;
      // the container just needs any non-empty image (the build unit name
      // serves as the reference).  We return the build unit ref as a
      // placeholder — the backend resolves the actual image tag.
      if (this.imageSource === 'build_unit') return this.buildUnitRef + '.image';
      return this.registryImage;
    },
    async submitForm(form) {
      // Validate before collecting FormData so required fields on hidden tabs
      // are reachable. Find the first invalid field, switch to its tab, then
      // let the browser show its native validation tooltip.
      const firstInvalid = form.querySelector(':invalid');
      if (firstInvalid) {
        const tabPanel = firstInvalid.closest('[x-show^="activeTab"]');
        if (tabPanel) {
          const match = tabPanel.getAttribute('x-show').match(/activeTab === (\d+)/);
          if (match) this.activeTab = parseInt(match[1], 10);
        }
        // Defer reportValidity so Alpine has time to unhide the panel.
        await new Promise(r => setTimeout(r, 50));
        form.reportValidity();
        return;
      }
      const fd = new FormData(form);
      const data = {
        name: fd.get('name'),
        image: this.resolvedImage(),
        network: fd.get('network'),
        restart_policy: fd.get('restart_policy'),
        memory_limit: fd.get('memory_limit') || '',
        cpu_quota: fd.get('cpu_quota') || '',
        apparmor_profile: fd.get('apparmor_profile') || '',
        run_user: fd.get('run_user') || '',
        exec_start_pre: '',
        sort_order: 0,
        labels: {},
        depends_on: fd.getAll('depends_on'),
        ports: this.ports.filter(p => p.trim()),
        environment: Object.fromEntries(this.envPairs.filter(([k]) => k.trim())),
        volumes: this.volumeMounts.filter(vm => vm.volume_id && vm.container_path),
        bind_mounts: this.bindMounts.filter(bm => bm.host_path.trim() && bm.container_path.trim()),
        build_unit_name: this.imageSource === 'build_unit' ? this.buildUnitRef : '',
        uid_map: this.uidMaps.filter(m => m.trim && m.trim() && String(m) !== '0'),
        gid_map: this.gidMaps.filter(m => m.trim && m.trim() && String(m) !== '0'),
        // New fields
        entrypoint: fd.get('entrypoint') || '',
        exec_cmd: fd.get('exec_cmd') || '',
        environment_file: fd.get('environment_file') || '',
        auto_update: fd.get('auto_update') || '',
        no_new_privileges: fd.get('no_new_privileges') === 'true',
        read_only: fd.get('read_only') === 'true',
        health_cmd: this.healthEnabled ? (fd.get('health_cmd') || '') : '',
        health_interval: this.healthEnabled ? (fd.get('health_interval') || '') : '',
        health_timeout: this.healthEnabled ? (fd.get('health_timeout') || '') : '',
        health_retries: this.healthEnabled ? (fd.get('health_retries') || '') : '',
        health_start_period: this.healthEnabled ? (fd.get('health_start_period') || '') : '',
        health_on_failure: this.healthEnabled ? (fd.get('health_on_failure') || '') : '',
        notify_healthy: this.healthEnabled && fd.get('notify_healthy') === 'true',
        working_dir: fd.get('working_dir') || '',
        hostname: fd.get('hostname') || '',
        privileged: fd.get('privileged') === 'true',
        drop_caps: this.dropCaps.filter(c => c.trim()),
        add_caps: this.addCaps.filter(c => c.trim()),
        sysctl: Object.fromEntries(this.sysctlPairs.filter(([k]) => k.trim())),
        seccomp_profile: fd.get('seccomp_profile') || '',
        mask_paths: this.maskPaths.filter(p => p.trim()),
        unmask_paths: this.unmaskPaths.filter(p => p.trim()),
        dns: this.dnsServers.filter(d => d.trim()),
        dns_search: this.dnsSearch.filter(d => d.trim()),
        dns_option: this.dnsOption.filter(d => d.trim()),
        // P2/P3 fields
        pod_name: fd.get('pod_name') || '',
        log_driver: fd.get('log_driver') || '',
        log_opt: (() => {
          const o = {};
          const sz = (fd.get('log_opt_max_size') || '').trim();
          const mf = (fd.get('log_opt_max_file') || '').trim();
          if (sz) o['max-size'] = sz;
          if (mf) o['max-file'] = mf;
          return o;
        })(),
        exec_start_post: fd.get('exec_start_post') || '',
        exec_stop: fd.get('exec_stop') || '',
        secrets: this.selectedSecrets.filter(s => s.trim()),
        // Features 1-6, 13, 15
        devices: this.devices.filter(d => d.trim()),
        network_aliases: this.networkAliases.filter(a => a.trim()),
        runtime: fd.get('runtime') || '',
        init: fd.get('init') === 'true',
        service_extra: fd.get('service_extra') || '',
        memory_reservation: fd.get('memory_reservation') || '',
        cpu_weight: fd.get('cpu_weight') || '',
        io_weight: fd.get('io_weight') || '',
      };
      const url = this.containerId
        ? `/api/compartments/${this.compartmentId}/containers/${this.containerId}`
        : `/api/compartments/${this.compartmentId}/containers`;
      const resp = await jsonFetch(this.containerId ? 'PUT' : 'POST', url, data);
      if (resp.ok) {
        hideModal('add-container-modal');
        htmx.ajax('GET', `/api/compartments/${this.compartmentId}`, {
          target: '#main-content', swap: 'innerHTML',
          headers: { 'HX-Request': 'true' },
        });
      } else {
        const err = await resp.json().catch(() => ({}));
        showToast(err.detail || t('Failed to save container'), 'error');
      }
    },
  };
}
