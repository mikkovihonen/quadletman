-- Migration 006: per-compartment connection monitor toggle
--
-- Adds an enabled flag to the compartments table so each compartment can opt out
-- of connection monitoring independently. Defaults to 1 (enabled) for all existing
-- and new compartments.

ALTER TABLE compartments ADD COLUMN connection_monitor_enabled INTEGER NOT NULL DEFAULT 1;
