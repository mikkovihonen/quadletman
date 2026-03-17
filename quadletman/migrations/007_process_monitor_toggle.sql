-- Migration 007: per-compartment process monitor toggle
--
-- Adds an enabled flag to the compartments table so each compartment can opt out
-- of process monitoring independently. Defaults to 1 (enabled) for all existing
-- and new compartments.

ALTER TABLE compartments ADD COLUMN process_monitor_enabled INTEGER NOT NULL DEFAULT 1;
