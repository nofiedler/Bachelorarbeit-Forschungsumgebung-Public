-- Local defaults affect new configurations only; immutable runs keep their bindings.
CREATE TABLE application_settings (id INTEGER PRIMARY KEY CHECK(id=1), body TEXT NOT NULL);
CREATE TABLE model_catalog (model_id TEXT PRIMARY KEY, artifact_id TEXT NOT NULL, retrieved_at TEXT NOT NULL);
CREATE TABLE run_completion (run_id TEXT PRIMARY KEY, status TEXT NOT NULL, step TEXT NOT NULL,
    reason TEXT, functional_id TEXT, static_id TEXT, updated_at TEXT NOT NULL);
