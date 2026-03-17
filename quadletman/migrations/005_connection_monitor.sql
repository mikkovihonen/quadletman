-- Migration 005: connection monitor
--
-- Every unique outbound connection (proto, dst_ip, dst_port) observed from a container
-- in a compartment is recorded here. container_name identifies which container made the
-- connection. known=0 means never reviewed; known=1 means the user has marked it expected.
-- times_seen counts how many poll cycles the connection has been observed.

CREATE TABLE connections (
    id TEXT PRIMARY KEY,
    compartment_id TEXT NOT NULL REFERENCES compartments(id) ON DELETE CASCADE,
    container_name TEXT NOT NULL DEFAULT '',
    proto TEXT NOT NULL,
    dst_ip TEXT NOT NULL,
    dst_port INTEGER NOT NULL,
    known INTEGER NOT NULL DEFAULT 0,
    times_seen INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(compartment_id, container_name, proto, dst_ip, dst_port)
);
