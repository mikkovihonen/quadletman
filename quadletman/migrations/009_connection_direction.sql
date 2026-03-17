-- Migration 009: connection direction (outbound / inbound)
--
-- Tracks whether a connection was initiated FROM a container (outbound) or TO a container
-- (inbound). Direction is added to the unique key so that the same (container, proto,
-- remote_ip, port) pair can appear as both an outbound and an inbound record.
--
-- Field semantics by direction:
--   outbound: dst_ip = external destination, dst_port = external port
--   inbound:  dst_ip = external source IP,   dst_port = container's listening port
--
-- The connections table is recreated to include direction in the UNIQUE constraint.
-- All existing rows are tagged as 'outbound' (the only direction previously tracked).
--
-- connection_whitelist_rules gets a nullable direction column; NULL means match either
-- direction.

CREATE TABLE connections_new (
    id TEXT PRIMARY KEY,
    compartment_id TEXT NOT NULL REFERENCES compartments(id) ON DELETE CASCADE,
    container_name TEXT NOT NULL DEFAULT '',
    proto TEXT NOT NULL,
    dst_ip TEXT NOT NULL,
    dst_port INTEGER NOT NULL,
    direction TEXT NOT NULL DEFAULT 'outbound',
    times_seen INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(compartment_id, container_name, proto, dst_ip, dst_port, direction)
);

INSERT INTO connections_new
    SELECT id, compartment_id, container_name, proto, dst_ip, dst_port,
           'outbound', times_seen, first_seen_at, last_seen_at
    FROM connections;

DROP TABLE connections;
ALTER TABLE connections_new RENAME TO connections;

ALTER TABLE connection_whitelist_rules ADD COLUMN direction TEXT;
