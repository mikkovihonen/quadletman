-- P0: WorkingDir
ALTER TABLE containers ADD COLUMN working_dir       TEXT NOT NULL DEFAULT '';

-- P1: Security hardening
ALTER TABLE containers ADD COLUMN drop_caps         TEXT NOT NULL DEFAULT '[]';
ALTER TABLE containers ADD COLUMN add_caps          TEXT NOT NULL DEFAULT '[]';
ALTER TABLE containers ADD COLUMN sysctl            TEXT NOT NULL DEFAULT '{}';
ALTER TABLE containers ADD COLUMN seccomp_profile   TEXT NOT NULL DEFAULT '';
ALTER TABLE containers ADD COLUMN mask_paths        TEXT NOT NULL DEFAULT '[]';
ALTER TABLE containers ADD COLUMN unmask_paths      TEXT NOT NULL DEFAULT '[]';
ALTER TABLE containers ADD COLUMN privileged        INTEGER NOT NULL DEFAULT 0;

-- P2: Container-level networking
ALTER TABLE containers ADD COLUMN hostname          TEXT NOT NULL DEFAULT '';
ALTER TABLE containers ADD COLUMN dns               TEXT NOT NULL DEFAULT '[]';
ALTER TABLE containers ADD COLUMN dns_search        TEXT NOT NULL DEFAULT '[]';
ALTER TABLE containers ADD COLUMN dns_option        TEXT NOT NULL DEFAULT '[]';

-- P2: Service-level shared network configuration
ALTER TABLE services ADD COLUMN net_driver          TEXT NOT NULL DEFAULT '';
ALTER TABLE services ADD COLUMN net_subnet          TEXT NOT NULL DEFAULT '';
ALTER TABLE services ADD COLUMN net_gateway         TEXT NOT NULL DEFAULT '';
ALTER TABLE services ADD COLUMN net_ipv6            INTEGER NOT NULL DEFAULT 0;
ALTER TABLE services ADD COLUMN net_internal        INTEGER NOT NULL DEFAULT 0;
ALTER TABLE services ADD COLUMN net_dns_enabled     INTEGER NOT NULL DEFAULT 0;
