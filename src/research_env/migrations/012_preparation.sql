-- HTTP records intentions only; the persistent worker owns external preparation.
CREATE TABLE ui_command (
 id TEXT PRIMARY KEY,
 idempotency_key TEXT NOT NULL UNIQUE,
 kind TEXT NOT NULL,
 payload TEXT NOT NULL,
 payload_hash TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('queued','processing','completed','failed','recovery_required')),
 created_at TEXT NOT NULL,
 result TEXT,
 reason TEXT NOT NULL
);
CREATE TRIGGER ui_command_identity BEFORE UPDATE ON ui_command
 WHEN OLD.id != NEW.id OR OLD.idempotency_key != NEW.idempotency_key
 OR OLD.kind != NEW.kind OR OLD.payload != NEW.payload OR OLD.payload_hash != NEW.payload_hash
 BEGIN SELECT RAISE(ABORT,'Bedienauftrag unveränderlich'); END;
