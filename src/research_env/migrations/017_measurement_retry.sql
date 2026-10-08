-- Explicit retries retain prior measurement executions and their original IDs.
ALTER TABLE run_completion ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0;
ALTER TABLE run_completion ADD COLUMN control_run_id TEXT;
