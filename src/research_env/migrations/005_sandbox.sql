-- Own allocations are journalled before external Docker creation. No resume on boot.
CREATE TABLE sandbox_execution (
 id TEXT PRIMARY KEY,
 job_id TEXT NOT NULL REFERENCES job_state(job_id),
 run_id TEXT NOT NULL REFERENCES run_binding(run_id),
 instance_id TEXT NOT NULL,
 suite_kind TEXT NOT NULL CHECK(suite_kind IN ('development','study_holdout')),
 profile TEXT NOT NULL,
 status TEXT NOT NULL,
 body TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE sandbox_observation (
 id INTEGER PRIMARY KEY,
 execution_id TEXT NOT NULL REFERENCES sandbox_execution(id),
 happened_at TEXT NOT NULL,
 kind TEXT NOT NULL,
 artifact_id TEXT REFERENCES register_record(id)
);
