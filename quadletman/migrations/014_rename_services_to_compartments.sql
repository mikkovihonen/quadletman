-- Rename the services table to compartments and rename service_id columns
-- in all child tables to compartment_id.
--
-- SQLite >= 3.26 resolves FK references by table name after ALTER TABLE
-- RENAME so the child-table FKs remain valid. The service_id column rename
-- uses ALTER TABLE … RENAME COLUMN which requires SQLite >= 3.25 (Python 3.8
-- ships with 3.31.1 so this is safe).

PRAGMA foreign_keys = OFF;

ALTER TABLE services RENAME TO compartments;

DROP TRIGGER IF EXISTS services_updated_at;
CREATE TRIGGER compartments_updated_at
    AFTER UPDATE ON compartments
    FOR EACH ROW BEGIN
        UPDATE compartments SET updated_at = datetime('now') WHERE id = NEW.id;
    END;

ALTER TABLE containers    RENAME COLUMN service_id TO compartment_id;
ALTER TABLE volumes       RENAME COLUMN service_id TO compartment_id;
ALTER TABLE pods          RENAME COLUMN service_id TO compartment_id;
ALTER TABLE image_units   RENAME COLUMN service_id TO compartment_id;
ALTER TABLE system_events RENAME COLUMN service_id TO compartment_id;

PRAGMA foreign_keys = ON;
