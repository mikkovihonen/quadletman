-- Add network columns to compartments that may be missing on databases where
-- migration 011 (which added them to the old 'services' table) was not applied
-- before migration 014 renamed the table to 'compartments'.
-- Each statement is run individually by the migration runner so duplicate-column
-- errors are silently skipped for DBs that already have these columns.
ALTER TABLE compartments ADD COLUMN net_driver      TEXT NOT NULL DEFAULT '';
ALTER TABLE compartments ADD COLUMN net_subnet      TEXT NOT NULL DEFAULT '';
ALTER TABLE compartments ADD COLUMN net_gateway     TEXT NOT NULL DEFAULT '';
ALTER TABLE compartments ADD COLUMN net_ipv6        INTEGER NOT NULL DEFAULT 0;
ALTER TABLE compartments ADD COLUMN net_internal    INTEGER NOT NULL DEFAULT 0;
ALTER TABLE compartments ADD COLUMN net_dns_enabled INTEGER NOT NULL DEFAULT 0;
