-- Explicit technical instrument correction; frozen scientific settings do not
-- change. Each authorization remains tied to the original run and sealed hash.
CREATE TABLE evaluation_tool_correction (
 run_id TEXT NOT NULL REFERENCES run_binding(run_id),
 candidate_id TEXT NOT NULL REFERENCES register_record(id),
 candidate_hash TEXT NOT NULL,
 old_tool_id TEXT NOT NULL REFERENCES register_record(id),
 new_tool_id TEXT NOT NULL REFERENCES register_record(id),
 contract_id TEXT NOT NULL,
 suite_id TEXT NOT NULL,
 rubric_id TEXT NOT NULL,
 evidence_id TEXT NOT NULL REFERENCES register_record(id),
 PRIMARY KEY(run_id,new_tool_id)
);
CREATE TRIGGER immutable_evaluation_correction_update BEFORE UPDATE ON evaluation_tool_correction BEGIN SELECT RAISE(ABORT,'Immutable evaluator correction'); END;
CREATE TRIGGER immutable_evaluation_correction_delete BEFORE DELETE ON evaluation_tool_correction BEGIN SELECT RAISE(ABORT,'Immutable evaluator correction'); END;
