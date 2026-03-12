CREATE TABLE IF NOT EXISTS services (
    id           TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    description  TEXT NOT NULL DEFAULT '',
    linux_user   TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS containers (
    id             TEXT PRIMARY KEY,
    service_id     TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    name           TEXT NOT NULL,
    image          TEXT NOT NULL,
    environment    TEXT NOT NULL DEFAULT '{}',
    ports          TEXT NOT NULL DEFAULT '[]',
    volumes        TEXT NOT NULL DEFAULT '[]',
    labels         TEXT NOT NULL DEFAULT '{}',
    network        TEXT NOT NULL DEFAULT 'host',
    restart_policy TEXT NOT NULL DEFAULT 'always',
    exec_start_pre TEXT NOT NULL DEFAULT '',
    memory_limit   TEXT NOT NULL DEFAULT '',
    cpu_quota      TEXT NOT NULL DEFAULT '',
    depends_on     TEXT NOT NULL DEFAULT '[]',
    sort_order     INTEGER NOT NULL DEFAULT 0,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(service_id, name)
);

CREATE TABLE IF NOT EXISTS volumes (
    id              TEXT PRIMARY KEY,
    service_id      TEXT NOT NULL REFERENCES services(id) ON DELETE CASCADE,
    name            TEXT NOT NULL,
    selinux_context TEXT NOT NULL DEFAULT 'container_file_t',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(service_id, name)
);

CREATE TABLE IF NOT EXISTS system_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    service_id   TEXT,
    container_id TEXT,
    event_type   TEXT NOT NULL,
    message      TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TRIGGER IF NOT EXISTS services_updated_at
AFTER UPDATE ON services
BEGIN
    UPDATE services SET updated_at = datetime('now') WHERE id = NEW.id;
END;

CREATE TRIGGER IF NOT EXISTS containers_updated_at
AFTER UPDATE ON containers
BEGIN
    UPDATE containers SET updated_at = datetime('now') WHERE id = NEW.id;
END;
