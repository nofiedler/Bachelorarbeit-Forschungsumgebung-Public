-- Persistent operational backup/transfer state; substantive records remain immutable.
CREATE TABLE backup_request (
    job_id TEXT PRIMARY KEY REFERENCES job_state(job_id),
    freeze_id TEXT NOT NULL REFERENCES freeze_binding(freeze_id),
    status TEXT NOT NULL CHECK(status IN ('requested','creating','internal_ready','transferred','failed','recovery_required')),
    substantive_revision INTEGER NOT NULL,
    backup_id TEXT UNIQUE REFERENCES register_record(id),
    package_path TEXT,
    transfer_path TEXT,
    transfer_verified_at TEXT,
    error TEXT
);
