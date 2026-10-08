CREATE TABLE backup_operation_log (
    id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL REFERENCES backup_request(job_id),
    payload TEXT NOT NULL
);
CREATE TRIGGER backup_operation_no_update BEFORE UPDATE ON backup_operation_log BEGIN SELECT RAISE(ABORT, 'immutable backup operation'); END;
CREATE TRIGGER backup_operation_no_delete BEFORE DELETE ON backup_operation_log BEGIN SELECT RAISE(ABORT, 'immutable backup operation'); END;
CREATE TABLE study_restore (
    id TEXT PRIMARY KEY,
    archive_sha256 TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL,
    title TEXT,
    study_id TEXT,
    freeze_id TEXT,
    source_manifest_hash TEXT,
    created_at TEXT NOT NULL,
    error TEXT
);
