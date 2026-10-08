-- Concrete conscious recovery decisions remain append-only, even after completion.
CREATE TABLE ui_command_recovery (
 id TEXT PRIMARY KEY,
 command_id TEXT NOT NULL REFERENCES ui_command(id),
 reason TEXT NOT NULL,
 happened_at TEXT NOT NULL
);
CREATE TRIGGER ui_command_recovery_no_update BEFORE UPDATE ON ui_command_recovery
 BEGIN SELECT RAISE(ABORT,'Wiederaufnahmeentscheidung unveränderlich'); END;
CREATE TRIGGER ui_command_recovery_no_delete BEFORE DELETE ON ui_command_recovery
 BEGIN SELECT RAISE(ABORT,'Wiederaufnahmeentscheidung unveränderlich'); END;
