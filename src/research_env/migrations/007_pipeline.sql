-- Durable graph effects are separate from checkpoints: reentry reuses these refs.
CREATE TABLE pipeline_binding (
 run_id TEXT PRIMARY KEY REFERENCES run_binding(run_id),
 job_id TEXT NOT NULL UNIQUE REFERENCES job_state(job_id),
 manifest_id TEXT NOT NULL REFERENCES register_record(id),
 manifest_hash TEXT NOT NULL,
 order_id TEXT NOT NULL REFERENCES adapter_start_order(id),
 status TEXT NOT NULL CHECK(status IN ('ready','running','pause_requested','paused','abort_requested','recovery_required','resume_requested','completed')),
 process_id TEXT,
 clock_json TEXT,
 continuation TEXT,
 reason TEXT NOT NULL
);
CREATE TRIGGER pipeline_identity_immutable BEFORE UPDATE ON pipeline_binding
 WHEN OLD.run_id != NEW.run_id OR OLD.job_id != NEW.job_id OR OLD.manifest_id != NEW.manifest_id
 OR OLD.manifest_hash != NEW.manifest_hash OR OLD.order_id != NEW.order_id
 BEGIN SELECT RAISE(ABORT,'Pipelinebindung unveränderlich'); END;
CREATE TABLE pipeline_effect (
 run_id TEXT NOT NULL REFERENCES pipeline_binding(run_id),
 node TEXT NOT NULL,
 artifact_id TEXT NOT NULL REFERENCES register_record(id),
 sha256 TEXT NOT NULL,
 PRIMARY KEY(run_id,node)
);
CREATE TRIGGER pipeline_effect_no_update BEFORE UPDATE ON pipeline_effect
 BEGIN SELECT RAISE(ABORT,'Nodeergebnis unveränderlich'); END;
CREATE TRIGGER pipeline_effect_no_delete BEFORE DELETE ON pipeline_effect
 BEGIN SELECT RAISE(ABORT,'Nodeergebnis bleibt erhalten'); END;
CREATE TABLE pipeline_series (
 id TEXT PRIMARY KEY REFERENCES register_record(id),
 status TEXT NOT NULL CHECK(status IN ('waiting','ready','completed','stopped')),
 reason TEXT NOT NULL
);
CREATE TABLE pipeline_runner (
 run_id TEXT NOT NULL REFERENCES pipeline_binding(run_id),
 node TEXT NOT NULL,
 candidate_hash TEXT NOT NULL,
 tests_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','recovery_required','completed')),
 result_id TEXT REFERENCES register_record(id),
 PRIMARY KEY(run_id,node)
);
