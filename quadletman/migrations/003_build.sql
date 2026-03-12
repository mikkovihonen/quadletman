-- Add Containerfile build support to containers
ALTER TABLE containers ADD COLUMN build_context TEXT NOT NULL DEFAULT '';
ALTER TABLE containers ADD COLUMN build_file TEXT NOT NULL DEFAULT '';
