-- Squashed initial schema. Represents the complete database schema as of the squash.
-- Existing databases that already have this migration recorded will skip this file;
-- their schema was built incrementally by the previous 001-015 migrations.

CREATE TABLE compartments (
    id TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    linux_user TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    net_driver TEXT NOT NULL DEFAULT '',
    net_subnet TEXT NOT NULL DEFAULT '',
    net_gateway TEXT NOT NULL DEFAULT '',
    net_ipv6 INTEGER NOT NULL DEFAULT 0,
    net_internal INTEGER NOT NULL DEFAULT 0,
    net_dns_enabled INTEGER NOT NULL DEFAULT 0
);

CREATE TRIGGER compartments_updated_at
AFTER UPDATE ON compartments
BEGIN
    UPDATE compartments SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = NEW.id;
END;

CREATE TABLE containers (
    id TEXT PRIMARY KEY,
    compartment_id TEXT NOT NULL REFERENCES compartments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    image TEXT NOT NULL DEFAULT '',
    environment TEXT NOT NULL DEFAULT '{}',
    ports TEXT NOT NULL DEFAULT '[]',
    volumes TEXT NOT NULL DEFAULT '[]',
    labels TEXT NOT NULL DEFAULT '{}',
    network TEXT NOT NULL DEFAULT 'host',
    restart_policy TEXT NOT NULL DEFAULT 'always',
    exec_start_pre TEXT NOT NULL DEFAULT '',
    memory_limit TEXT NOT NULL DEFAULT '',
    cpu_quota TEXT NOT NULL DEFAULT '',
    depends_on TEXT NOT NULL DEFAULT '[]',
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    apparmor_profile TEXT NOT NULL DEFAULT '',
    build_context TEXT NOT NULL DEFAULT '',
    build_file TEXT NOT NULL DEFAULT '',
    run_user TEXT NOT NULL DEFAULT '',
    containerfile_content TEXT NOT NULL DEFAULT '',
    bind_mounts TEXT NOT NULL DEFAULT '[]',
    user_ns TEXT NOT NULL DEFAULT '',
    uid_map TEXT NOT NULL DEFAULT '[]',
    gid_map TEXT NOT NULL DEFAULT '[]',
    health_cmd TEXT NOT NULL DEFAULT '',
    health_interval TEXT NOT NULL DEFAULT '',
    health_timeout TEXT NOT NULL DEFAULT '',
    health_retries TEXT NOT NULL DEFAULT '',
    health_start_period TEXT NOT NULL DEFAULT '',
    health_on_failure TEXT NOT NULL DEFAULT '',
    notify_healthy INTEGER NOT NULL DEFAULT 0,
    auto_update TEXT NOT NULL DEFAULT '',
    environment_file TEXT NOT NULL DEFAULT '',
    exec_cmd TEXT NOT NULL DEFAULT '',
    entrypoint TEXT NOT NULL DEFAULT '',
    no_new_privileges INTEGER NOT NULL DEFAULT 0,
    read_only INTEGER NOT NULL DEFAULT 0,
    working_dir TEXT NOT NULL DEFAULT '',
    drop_caps TEXT NOT NULL DEFAULT '[]',
    add_caps TEXT NOT NULL DEFAULT '[]',
    sysctl TEXT NOT NULL DEFAULT '{}',
    seccomp_profile TEXT NOT NULL DEFAULT '',
    mask_paths TEXT NOT NULL DEFAULT '[]',
    unmask_paths TEXT NOT NULL DEFAULT '[]',
    privileged INTEGER NOT NULL DEFAULT 0,
    hostname TEXT NOT NULL DEFAULT '',
    dns TEXT NOT NULL DEFAULT '[]',
    dns_search TEXT NOT NULL DEFAULT '[]',
    dns_option TEXT NOT NULL DEFAULT '[]',
    pod_name TEXT NOT NULL DEFAULT '',
    log_driver TEXT NOT NULL DEFAULT '',
    log_opt TEXT NOT NULL DEFAULT '{}',
    exec_start_post TEXT NOT NULL DEFAULT '',
    exec_stop TEXT NOT NULL DEFAULT '',
    UNIQUE(compartment_id, name)
);

CREATE TRIGGER containers_updated_at
AFTER UPDATE ON containers
BEGIN
    UPDATE containers SET updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE id = NEW.id;
END;

CREATE TABLE volumes (
    id TEXT PRIMARY KEY,
    compartment_id TEXT NOT NULL REFERENCES compartments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    selinux_context TEXT NOT NULL DEFAULT 'container_file_t',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    owner_uid INTEGER NOT NULL DEFAULT 0,
    use_quadlet INTEGER NOT NULL DEFAULT 0,
    vol_driver TEXT NOT NULL DEFAULT '',
    vol_device TEXT NOT NULL DEFAULT '',
    vol_options TEXT NOT NULL DEFAULT '',
    vol_copy INTEGER NOT NULL DEFAULT 1,
    vol_group TEXT NOT NULL DEFAULT '',
    UNIQUE(compartment_id, name)
);

CREATE TABLE pods (
    id TEXT PRIMARY KEY,
    compartment_id TEXT NOT NULL REFERENCES compartments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    network TEXT NOT NULL DEFAULT '',
    publish_ports TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE image_units (
    id TEXT PRIMARY KEY,
    compartment_id TEXT NOT NULL REFERENCES compartments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    image TEXT NOT NULL,
    auth_file TEXT NOT NULL DEFAULT '',
    pull_policy TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    compartment_id TEXT,
    container_id TEXT,
    event_type TEXT NOT NULL,
    message TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);
