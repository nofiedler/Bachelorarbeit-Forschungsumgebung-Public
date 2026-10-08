-- Immutable M7 data proposal and exact human acknowledgement. No automatic run.
CREATE TABLE analysis_selection (
 proposal_id TEXT PRIMARY KEY,
 phase_id TEXT NOT NULL REFERENCES phase_state(phase_id),
 freeze_id TEXT NOT NULL REFERENCES freeze_binding(freeze_id),
 snapshot_id TEXT NOT NULL REFERENCES register_record(id),
 input_hash TEXT NOT NULL CHECK(length(input_hash)=64),
 analysis_version TEXT NOT NULL,
 phase_revision INTEGER NOT NULL
);
CREATE TABLE analysis_confirmation (
 analysis_id TEXT PRIMARY KEY REFERENCES register_record(id),
 proposal_id TEXT NOT NULL UNIQUE REFERENCES analysis_selection(proposal_id),
 acknowledgement_id TEXT NOT NULL REFERENCES register_record(id)
);
CREATE TRIGGER analysis_selection_no_update BEFORE UPDATE ON analysis_selection
BEGIN SELECT RAISE(ABORT,'immutable analysis selection'); END;
CREATE TRIGGER analysis_selection_no_delete BEFORE DELETE ON analysis_selection
BEGIN SELECT RAISE(ABORT,'immutable analysis selection'); END;
CREATE TRIGGER analysis_confirmation_no_update BEFORE UPDATE ON analysis_confirmation
BEGIN SELECT RAISE(ABORT,'immutable analysis confirmation'); END;
CREATE TRIGGER analysis_confirmation_no_delete BEFORE DELETE ON analysis_confirmation
BEGIN SELECT RAISE(ABORT,'immutable analysis confirmation'); END;
