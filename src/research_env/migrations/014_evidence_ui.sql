-- Idempotence receipts for explicit human reviews and analysis actions.
CREATE TABLE evidence_ui_action (
 key TEXT PRIMARY KEY,
 request_hash TEXT NOT NULL CHECK(length(request_hash)=64),
 action TEXT NOT NULL,
 result_id TEXT NOT NULL
);
CREATE TRIGGER evidence_ui_action_no_update BEFORE UPDATE ON evidence_ui_action
BEGIN SELECT RAISE(ABORT,'immutable evidence UI receipt'); END;
CREATE TRIGGER evidence_ui_action_no_delete BEFORE DELETE ON evidence_ui_action
BEGIN SELECT RAISE(ABORT,'immutable evidence UI receipt'); END;
