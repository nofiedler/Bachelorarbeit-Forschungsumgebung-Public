CREATE TABLE static_execution (
 id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL UNIQUE REFERENCES job_state(job_id),
 run_id TEXT NOT NULL REFERENCES run_binding(run_id),
 candidate_id TEXT NOT NULL REFERENCES register_record(id),
 tool_id TEXT NOT NULL REFERENCES register_record(id),
 status TEXT NOT NULL CHECK(status IN ('ready','running','completed','interrupted','recovery_required')),
 measurement_id TEXT REFERENCES register_record(id),
 body TEXT NOT NULL CHECK(json_valid(body)),
 created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX one_active_static ON static_execution((1)) WHERE status IN ('ready','running','recovery_required');
CREATE TABLE static_observation (
 id INTEGER PRIMARY KEY,
 execution_id TEXT NOT NULL REFERENCES static_execution(id),
 happened_at TEXT NOT NULL,
 artifact_id TEXT NOT NULL REFERENCES register_record(id)
);
CREATE TABLE static_tool_correction (
 run_id TEXT NOT NULL REFERENCES run_binding(run_id),
 candidate_id TEXT NOT NULL REFERENCES register_record(id),
 candidate_hash TEXT NOT NULL,
 old_tool_id TEXT NOT NULL REFERENCES register_record(id),
 new_tool_id TEXT NOT NULL REFERENCES register_record(id),
 measure_hash TEXT NOT NULL,
 evidence_id TEXT NOT NULL REFERENCES register_record(id),
 PRIMARY KEY(run_id,new_tool_id)
);
