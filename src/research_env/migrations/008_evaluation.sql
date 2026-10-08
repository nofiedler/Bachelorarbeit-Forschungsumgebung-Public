-- M6 control projection. Immutable observations/revisions remain in M3 records.
CREATE TABLE evaluation_execution (
 id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL UNIQUE REFERENCES job_state(job_id),
 run_id TEXT NOT NULL REFERENCES run_binding(run_id),
 candidate_id TEXT REFERENCES register_record(id),
 suite_id TEXT NOT NULL REFERENCES register_record(id),
 tool_id TEXT NOT NULL REFERENCES register_record(id),
 status TEXT NOT NULL CHECK(status IN ('ready','running','completed','interrupted','recovery_required')),
 measurement_id TEXT REFERENCES register_record(id),
 body TEXT NOT NULL CHECK(json_valid(body)),
 created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX one_active_evaluator ON evaluation_execution((1)) WHERE status IN ('ready','running','recovery_required');
CREATE TABLE evaluation_observation (
 id INTEGER PRIMARY KEY,
 execution_id TEXT NOT NULL REFERENCES evaluation_execution(id),
 happened_at TEXT NOT NULL,
 artifact_id TEXT NOT NULL REFERENCES register_record(id)
);
