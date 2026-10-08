CREATE TABLE backup_download (
    job_id TEXT PRIMARY KEY REFERENCES backup_request(job_id),
    status TEXT NOT NULL,
    path TEXT,
    sha256 TEXT,
    byte_count INTEGER,
    error TEXT
);
