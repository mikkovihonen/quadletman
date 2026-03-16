-- Migration 002: secrets, timers, templates, notification hooks, container secrets field

CREATE TABLE secrets (
    id TEXT PRIMARY KEY,
    compartment_id TEXT NOT NULL REFERENCES compartments(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(compartment_id, name)
);

CREATE TABLE timers (
    id TEXT PRIMARY KEY,
    compartment_id TEXT NOT NULL REFERENCES compartments(id) ON DELETE CASCADE,
    container_id TEXT NOT NULL REFERENCES containers(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    on_calendar TEXT NOT NULL DEFAULT '',
    on_boot_sec TEXT NOT NULL DEFAULT '',
    random_delay_sec TEXT NOT NULL DEFAULT '',
    persistent INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(compartment_id, name)
);

CREATE TABLE templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    config_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE TABLE notification_hooks (
    id TEXT PRIMARY KEY,
    compartment_id TEXT NOT NULL REFERENCES compartments(id) ON DELETE CASCADE,
    container_name TEXT NOT NULL DEFAULT '',
    event_type TEXT NOT NULL DEFAULT 'on_failure',
    webhook_url TEXT NOT NULL,
    webhook_secret TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

ALTER TABLE containers ADD COLUMN secrets TEXT NOT NULL DEFAULT '[]';
