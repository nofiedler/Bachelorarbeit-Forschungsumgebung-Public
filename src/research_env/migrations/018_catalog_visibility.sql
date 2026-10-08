-- Selection visibility only; all immutable research records remain preserved.
CREATE TABLE catalog_visibility (
 record_id TEXT PRIMARY KEY REFERENCES register_record(id),
 hidden_at TEXT NOT NULL
);
CREATE INDEX record_run_lookup ON register_record(kind, json_extract(payload,'$.run_id'));
CREATE TABLE matrix_draft_preferences (
 phase_id TEXT PRIMARY KEY REFERENCES register_record(id),
 r_c INTEGER NOT NULL CHECK(r_c>0),
 r_e INTEGER NOT NULL CHECK(r_e>0 AND r_e<=r_c),
 seed TEXT NOT NULL CHECK(length(seed)>0)
);
