-- Technical installation metadata only. Research entities belong to M3.
CREATE TABLE runtime_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE worker_status (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    observed_at REAL NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('running', 'stopped')),
    components_json TEXT NOT NULL
);
