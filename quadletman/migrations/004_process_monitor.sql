-- Migration 004: process monitor
--
-- Every (process_name, cmdline) pair ever observed running under a compartment user
-- is recorded here. known=0 means the process has never been reviewed; known=1 means
-- the user has marked it as expected. times_seen counts how many poll cycles it has
-- been observed across its lifetime.

CREATE TABLE processes (
    id TEXT PRIMARY KEY,
    compartment_id TEXT NOT NULL REFERENCES compartments(id) ON DELETE CASCADE,
    process_name TEXT NOT NULL,
    cmdline TEXT NOT NULL DEFAULT '',
    known INTEGER NOT NULL DEFAULT 0,
    times_seen INTEGER NOT NULL DEFAULT 1,
    first_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    last_seen_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(compartment_id, process_name, cmdline)
);
