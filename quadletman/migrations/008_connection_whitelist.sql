-- Migration 008: connection monitor whitelist rules and history retention
--
-- Replaces the per-connection known flag with explicit allow rules. Each rule
-- can match on container_name, proto, dst_ip (exact or CIDR), and dst_port;
-- NULL in any field means "match any value". A connection is considered
-- whitelisted if at least one rule matches; otherwise it is flagged as
-- unexpected and triggers the on_unexpected_connection webhook.
--
-- sort_order controls display ordering of rules in the UI. Rules are purely
-- additive (allow-only); the implicit last rule always denies.
--
-- connection_history_retention_days: if set, records whose last_seen_at is
-- older than this many days are deleted on the next monitor poll. NULL means
-- keep forever.
--
-- The known column (added in migration 005) is removed as it is superseded
-- by whitelist rules.

CREATE TABLE connection_whitelist_rules (
    id TEXT PRIMARY KEY,
    compartment_id TEXT NOT NULL REFERENCES compartments(id) ON DELETE CASCADE,
    description TEXT NOT NULL DEFAULT '',
    container_name TEXT,   -- NULL = any container
    proto TEXT,            -- NULL = any protocol
    dst_ip TEXT,           -- NULL = any IP; accepts exact address or CIDR notation
    dst_port INTEGER,      -- NULL = any port
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

ALTER TABLE compartments ADD COLUMN connection_history_retention_days INTEGER;

ALTER TABLE connections DROP COLUMN known;
