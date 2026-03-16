-- Migration 003: devices, runtime, service_extra, init, memory_reservation,
--               cpu_weight, io_weight, network_aliases, metrics_history, restart_stats

-- Feature 1: host device passthrough
ALTER TABLE containers ADD COLUMN devices TEXT NOT NULL DEFAULT '[]';

-- Feature 2: OCI runtime selection (maps to PodmanArgs=--runtime=VALUE)
ALTER TABLE containers ADD COLUMN runtime TEXT NOT NULL DEFAULT '';

-- Feature 3: raw [Service] section extra directives (freeform multi-line)
ALTER TABLE containers ADD COLUMN service_extra TEXT NOT NULL DEFAULT '';

-- Feature 5: run an init process as PID 1 inside the container
ALTER TABLE containers ADD COLUMN init INTEGER NOT NULL DEFAULT 0;

-- Feature 6: soft memory reservation and cgroup fair-share weights
ALTER TABLE containers ADD COLUMN memory_reservation TEXT NOT NULL DEFAULT '';
ALTER TABLE containers ADD COLUMN cpu_weight TEXT NOT NULL DEFAULT '';
ALTER TABLE containers ADD COLUMN io_weight TEXT NOT NULL DEFAULT '';

-- Feature 15: additional network aliases beyond the auto-generated container-name alias
ALTER TABLE containers ADD COLUMN network_aliases TEXT NOT NULL DEFAULT '[]';

-- Feature 9 & 10: per-compartment metrics history and restart counters
CREATE TABLE metrics_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    compartment_id TEXT NOT NULL REFERENCES compartments(id) ON DELETE CASCADE,
    recorded_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    cpu_percent REAL NOT NULL DEFAULT 0,
    memory_bytes INTEGER NOT NULL DEFAULT 0,
    disk_bytes INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE container_restart_stats (
    compartment_id TEXT NOT NULL,
    container_name TEXT NOT NULL,
    restart_count INTEGER NOT NULL DEFAULT 0,
    last_failure_at TEXT,
    last_restart_at TEXT,
    PRIMARY KEY (compartment_id, container_name)
);
