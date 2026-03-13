-- Drop display_name column by recreating the services table without it
CREATE TABLE services_new (
    id          TEXT PRIMARY KEY,
    description TEXT NOT NULL DEFAULT '',
    linux_user  TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    updated_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

INSERT INTO services_new (id, description, linux_user, created_at, updated_at)
    SELECT id, description, linux_user, created_at, updated_at FROM services;

DROP TABLE services;
ALTER TABLE services_new RENAME TO services;
