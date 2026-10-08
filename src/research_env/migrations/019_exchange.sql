CREATE TABLE exchange_job (
    id TEXT PRIMARY KEY,
    analysis_id TEXT NOT NULL REFERENCES register_record(id),
    request_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('queued','running','ready','failed')),
    sha256 TEXT,
    byte_count INTEGER,
    error TEXT
);
