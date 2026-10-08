-- Technical copy-attempt projection. Never increments a research revision.
-- Device/inode claims apply only to the source copy-origin token.
CREATE TABLE seal_copy_attempt (
    id TEXT PRIMARY KEY,
    origin TEXT NOT NULL,
    candidate_id TEXT NOT NULL REFERENCES register_record(id),
    manifest_id TEXT NOT NULL REFERENCES register_record(id),
    tree_hash TEXT NOT NULL,
    path TEXT NOT NULL,
    temporary TEXT NOT NULL UNIQUE,
    size INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    mode INTEGER NOT NULL,
    synced_size INTEGER NOT NULL DEFAULT 0,
    device INTEGER,
    inode INTEGER,
    status TEXT NOT NULL CHECK(status IN ('planned','allocated','completed','retained')),
    evidence_id TEXT REFERENCES register_record(id),
    created_at TEXT NOT NULL
);
