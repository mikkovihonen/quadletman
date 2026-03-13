-- P2: .pod quadlet support
CREATE TABLE IF NOT EXISTS pods (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    network TEXT NOT NULL DEFAULT '',
    publish_ports TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

ALTER TABLE containers ADD COLUMN pod_name TEXT NOT NULL DEFAULT '';

-- P2: .volume quadlet support (opt-in per volume)
ALTER TABLE volumes ADD COLUMN use_quadlet INTEGER NOT NULL DEFAULT 0;
ALTER TABLE volumes ADD COLUMN vol_driver TEXT NOT NULL DEFAULT '';
ALTER TABLE volumes ADD COLUMN vol_device TEXT NOT NULL DEFAULT '';
ALTER TABLE volumes ADD COLUMN vol_options TEXT NOT NULL DEFAULT '';
ALTER TABLE volumes ADD COLUMN vol_copy INTEGER NOT NULL DEFAULT 1;
ALTER TABLE volumes ADD COLUMN vol_group TEXT NOT NULL DEFAULT '';

-- P2: .image quadlet support
CREATE TABLE IF NOT EXISTS image_units (
    id TEXT PRIMARY KEY,
    service_id TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    name TEXT NOT NULL,
    image TEXT NOT NULL,
    auth_file TEXT NOT NULL DEFAULT '',
    pull_policy TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- P3: additional container fields
ALTER TABLE containers ADD COLUMN log_driver TEXT NOT NULL DEFAULT '';
ALTER TABLE containers ADD COLUMN log_opt TEXT NOT NULL DEFAULT '{}';
ALTER TABLE containers ADD COLUMN exec_start_post TEXT NOT NULL DEFAULT '';
ALTER TABLE containers ADD COLUMN exec_stop TEXT NOT NULL DEFAULT '';
