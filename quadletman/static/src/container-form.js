// Alpine component: container create/edit form
// Defined in <head> so it is available before HTMX-loaded fragments are
// processed by Alpine's MutationObserver.

function containerForm(compartmentId, containerId) {
  return {
    ...configFileMixin(compartmentId, 'container', () => containerId),
    compartmentId,
    containerId,
    activeTab: 1,
    ports: [],
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
    groupAdd: [],
    addHost: [],
    exposeHostPort: [],
    ulimits: [],
    tmpfs: [],
    mount: [],
    annotation: [],
    labelPairs: [],
    globalArgs: [],
    healthStartupEnabled: false,
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
      this.groupAdd = d.groupAdd ?? [];
      this.addHost = d.addHost ?? [];
      this.exposeHostPort = d.exposeHostPort ?? [];
      this.ulimits = d.ulimits ?? [];
      this.tmpfs = d.tmpfs ?? [];
      this.mount = d.mount ?? [];
      this.annotation = d.annotation ?? [];
      this.labelPairs = d.labelPairs ?? [];
      this.globalArgs = d.globalArgs ?? [];
      this.healthStartupEnabled = d.healthStartupEnabled === true;
      this.initConfigFile('environment_file', d.environmentFile ?? '', 'keyvalue');
      this.initConfigFile('seccomp_profile', d.seccompProfile ?? '', 'raw');
      this.initConfigFile('containers_conf_module', d.containersConfModule ?? '', 'raw');
    },
    async _fetchErrMsg(resp, defaultMsg) {
      const data = await resp.json().catch(() => ({}));
      return formatApiError(data, defaultMsg).msg;
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
      clearFieldErrors(form);
      // Validate all inputs including those on hidden tabs.  The :invalid
      // pseudo-class does not match display:none elements, so we call
      // checkValidity() on each input explicitly to find the first failure.
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
        // Defer reportValidity so Alpine has time to unhide the panel.
        await new Promise(r => setTimeout(r, 50));
        form.reportValidity();
        return;
      }
      const fd = new FormData(form);
      const data = {
        qm_name: fd.get('qm_name'),
        image: this.resolvedImage(),
        network: fd.get('network'),
        restart_policy: fd.get('restart_policy'),
        memory_limit: fd.get('memory_limit') || '',
        cpu_quota: fd.get('cpu_quota') || '',
        apparmor_profile: fd.get('apparmor_profile') || '',
        run_user: fd.get('run_user') || '',
        exec_start_pre: '',
        qm_sort_order: 0,
        labels: Object.fromEntries(this.labelPairs.filter(([k]) => k.trim())),
        depends_on: fd.getAll('depends_on'),
        ports: this.ports.filter(p => p.trim()),
        environment: Object.fromEntries(this.envPairs.filter(([k]) => k.trim())),
        volumes: this.volumeMounts.filter(vm => vm.volume_id && vm.container_path),
        bind_mounts: this.bindMounts.filter(bm => bm.host_path.trim() && bm.container_path.trim()),
        qm_build_unit_name: this.imageSource === 'build_unit' ? this.buildUnitRef : '',
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
        pod: fd.get('pod') || '',
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
        // Tab 2: Environment additions
        user_ns: fd.get('user_ns') || '',
        sub_uid_map: fd.get('sub_uid_map') || '',
        sub_gid_map: fd.get('sub_gid_map') || '',
        group_add: this.groupAdd.filter(g => g.trim()),
        // Tab 3: Networking additions
        ip: fd.get('ip') || '',
        ip6: fd.get('ip6') || '',
        add_host: this.addHost.filter(h => h.trim()),
        expose_host_port: this.exposeHostPort.filter(p => p.trim()),
        // Tab 4: Resources additions
        pids_limit: fd.get('pids_limit') || '',
        shm_size: fd.get('shm_size') || '',
        read_only_tmpfs: fd.get('read_only_tmpfs') === 'true',
        ulimits: this.ulimits.filter(u => u.trim()),
        tmpfs: this.tmpfs.filter(t => t.trim()),
        mount: this.mount.filter(m => m.trim()),


        // Tab 4: Health startup probe
        health_startup_cmd: this.healthStartupEnabled ? (fd.get('health_startup_cmd') || '') : '',
        health_startup_interval: this.healthStartupEnabled ? (fd.get('health_startup_interval') || '') : '',
        health_startup_retries: this.healthStartupEnabled ? (fd.get('health_startup_retries') || '') : '',
        health_startup_success: this.healthStartupEnabled ? (fd.get('health_startup_success') || '') : '',
        health_startup_timeout: this.healthStartupEnabled ? (fd.get('health_startup_timeout') || '') : '',
        health_log_destination: this.healthEnabled ? (fd.get('health_log_destination') || '') : '',
        health_max_log_count: this.healthEnabled ? (fd.get('health_max_log_count') || '') : '',
        health_max_log_size: this.healthEnabled ? (fd.get('health_max_log_size') || '') : '',
        // Tab 5: Security additions
        security_label_disable: fd.get('security_label_disable') === 'true',
        security_label_file_type: fd.get('security_label_file_type') || '',
        security_label_level: fd.get('security_label_level') || '',
        security_label_type: fd.get('security_label_type') || '',
        security_label_nested: fd.get('security_label_nested') === 'true',
        pull: fd.get('pull') || '',
        rootfs: fd.get('rootfs') || '',
        annotation: this.annotation.filter(a => a.trim()),
        // Tab 6: Advanced
        timezone: fd.get('timezone') || '',
        stop_signal: fd.get('stop_signal') || '',
        stop_timeout: fd.get('stop_timeout') || '',
        run_init: fd.get('run_init') === 'true',
        start_with_pod: fd.get('start_with_pod') === 'true',
        default_dependencies: fd.get('default_dependencies') === 'true',
        environment_host: fd.get('environment_host') === 'true',
        http_proxy: fd.get('http_proxy') === 'true',
        cgroups_mode: fd.get('cgroups_mode') || '',
        reload_cmd: fd.get('reload_cmd') || '',
        reload_signal: fd.get('reload_signal') || '',
        retry: fd.get('retry') || '',
        retry_delay: fd.get('retry_delay') || '',
        memory: fd.get('memory') || '',
        containers_conf_module: fd.get('containers_conf_module') || '',
        service_name: fd.get('service_name') || '',
        global_args: this.globalArgs.filter(a => a.trim()),
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
        showFieldErrors(form, err.detail);
        showApiError(err, t('Failed to save container'));
      }
    },
  };
}
