-- M3 immutable contracts plus small mutable operational projections.
-- All entity payloads are hashed/validated through the repository. References use FK.
CREATE TABLE register_record (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    code TEXT NOT NULL,
    payload TEXT NOT NULL CHECK(json_valid(payload)),
    sha256 TEXT NOT NULL CHECK(length(sha256)=64),
    UNIQUE(kind,code),
    CHECK(json_extract(payload,'$.id')=id),
    CHECK(json_extract(payload,'$.code')=code)
);
CREATE TRIGGER register_immutable_update BEFORE UPDATE ON register_record
BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TRIGGER register_immutable_delete BEFORE DELETE ON register_record
BEGIN SELECT RAISE(ABORT,'immutable record'); END;
CREATE TABLE register_reference (
    owner_id TEXT NOT NULL REFERENCES register_record(id),
    path TEXT NOT NULL,
    target_id TEXT NOT NULL REFERENCES register_record(id),
    PRIMARY KEY(owner_id,path,target_id)
);
CREATE TRIGGER reference_immutable_update BEFORE UPDATE ON register_reference
BEGIN SELECT RAISE(ABORT,'immutable reference'); END;
CREATE TRIGGER reference_immutable_delete BEFORE DELETE ON register_reference
BEGIN SELECT RAISE(ABORT,'immutable reference'); END;
CREATE TABLE phase_state (
    phase_id TEXT PRIMARY KEY REFERENCES register_record(id),
    status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','ready','running','pause_requested','paused','completed','stopped')),
    substantive_revision INTEGER NOT NULL DEFAULT 0 CHECK(substantive_revision>=0),
    reason TEXT NOT NULL DEFAULT 'New draft phase' CHECK(length(reason)>0)
);
CREATE TABLE freeze_binding (
    freeze_id TEXT PRIMARY KEY REFERENCES register_record(id),
    phase_id TEXT NOT NULL UNIQUE REFERENCES phase_state(phase_id),
    scope_hash TEXT NOT NULL,
    freeze_hash TEXT NOT NULL
);
CREATE TABLE main_plan (
    planned_id TEXT PRIMARY KEY REFERENCES register_record(id),
    freeze_id TEXT NOT NULL REFERENCES freeze_binding(freeze_id),
    configuration_id TEXT NOT NULL REFERENCES register_record(id),
    block_id TEXT NOT NULL REFERENCES register_record(id),
    cell_key TEXT NOT NULL CHECK(cell_key IN ('C-BF-0','C-BF-1','C-SQL-0','C-SQL-1','C-UP-0','C-UP-1','E-AB','E-BA','E-BB','E-P0R1','E-P1R0','E-P0R0')),
    position INTEGER NOT NULL CHECK(position>0),
    UNIQUE(freeze_id,position),
    UNIQUE(block_id,cell_key)
);
CREATE TABLE run_binding (
    run_id TEXT PRIMARY KEY REFERENCES register_record(id),
    phase_id TEXT NOT NULL REFERENCES phase_state(phase_id),
    purpose TEXT NOT NULL CHECK(purpose IN ('main','pilot','preparation','demo','free_test')),
    planned_id TEXT UNIQUE REFERENCES main_plan(planned_id),
    execution TEXT NOT NULL CHECK(execution IN ('queued','preflight','running','terminal')),
    state_json TEXT NOT NULL CHECK(json_valid(state_json)) CHECK(json_extract(state_json,'$.execution')=execution),
    CHECK((purpose='main' AND planned_id IS NOT NULL) OR (purpose!='main' AND planned_id IS NULL))
);
CREATE UNIQUE INDEX single_active_generation ON run_binding((1)) WHERE execution!='terminal';
CREATE TABLE job_state (
    job_id TEXT PRIMARY KEY REFERENCES register_record(id),
    idempotency_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK(status IN ('ready','running','completed','stopped')),
    worker TEXT,
    claimed_at TEXT,
    heartbeat_at TEXT
);
CREATE TABLE call_binding (
    call_id TEXT PRIMARY KEY REFERENCES register_record(id),
    run_id TEXT NOT NULL REFERENCES run_binding(run_id),
    node TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK(sequence>0),
    UNIQUE(run_id,node),
    UNIQUE(run_id,sequence)
);
CREATE TABLE transport_binding (
    transport_id TEXT PRIMARY KEY REFERENCES register_record(id),
    call_id TEXT NOT NULL REFERENCES call_binding(call_id),
    number INTEGER NOT NULL CHECK(number IN (1,2)),
    UNIQUE(call_id,number)
);
CREATE TABLE event_binding (
    event_id TEXT PRIMARY KEY REFERENCES register_record(id),
    run_id TEXT NOT NULL REFERENCES run_binding(run_id),
    sequence INTEGER NOT NULL CHECK(sequence>0),
    UNIQUE(run_id,sequence)
);
CREATE TABLE revision_binding (
    revision_id TEXT PRIMARY KEY REFERENCES register_record(id),
    run_id TEXT NOT NULL REFERENCES run_binding(run_id),
    kind TEXT NOT NULL CHECK(kind IN ('measurement','review')),
    field_key TEXT NOT NULL,
    number INTEGER NOT NULL CHECK(number>0),
    predecessor_id TEXT UNIQUE REFERENCES revision_binding(revision_id),
    UNIQUE(run_id,kind,field_key,number)
);
CREATE TABLE interval_binding (
    interval_id TEXT PRIMARY KEY REFERENCES register_record(id),
    run_id TEXT NOT NULL REFERENCES run_binding(run_id),
    sequence INTEGER NOT NULL CHECK(sequence>0),
    UNIQUE(run_id,sequence)
);
CREATE TABLE transport_state (
    transport_id TEXT PRIMARY KEY REFERENCES transport_binding(transport_id),
    status TEXT NOT NULL,
    sequence INTEGER NOT NULL DEFAULT 0 CHECK(sequence>=0)
);
CREATE TABLE transport_event_binding (
    event_id TEXT PRIMARY KEY REFERENCES register_record(id),
    transport_id TEXT NOT NULL REFERENCES transport_state(transport_id),
    sequence INTEGER NOT NULL CHECK(sequence>0),
    UNIQUE(transport_id,sequence)
);

CREATE TRIGGER freeze_binding_immutable_update BEFORE UPDATE ON freeze_binding
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;
CREATE TRIGGER freeze_binding_immutable_delete BEFORE DELETE ON freeze_binding
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;

CREATE TRIGGER main_plan_immutable_update BEFORE UPDATE ON main_plan
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;
CREATE TRIGGER main_plan_immutable_delete BEFORE DELETE ON main_plan
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;

CREATE TRIGGER call_binding_immutable_update BEFORE UPDATE ON call_binding
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;
CREATE TRIGGER call_binding_immutable_delete BEFORE DELETE ON call_binding
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;

CREATE TRIGGER transport_binding_immutable_update BEFORE UPDATE ON transport_binding
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;
CREATE TRIGGER transport_binding_immutable_delete BEFORE DELETE ON transport_binding
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;

CREATE TRIGGER event_binding_immutable_update BEFORE UPDATE ON event_binding
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;
CREATE TRIGGER event_binding_immutable_delete BEFORE DELETE ON event_binding
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;

CREATE TRIGGER revision_binding_immutable_update BEFORE UPDATE ON revision_binding
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;
CREATE TRIGGER revision_binding_immutable_delete BEFORE DELETE ON revision_binding
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;

CREATE TRIGGER interval_binding_immutable_update BEFORE UPDATE ON interval_binding
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;
CREATE TRIGGER interval_binding_immutable_delete BEFORE DELETE ON interval_binding
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;

CREATE TRIGGER transport_event_binding_immutable_update BEFORE UPDATE ON transport_event_binding
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;
CREATE TRIGGER transport_event_binding_immutable_delete BEFORE DELETE ON transport_event_binding
BEGIN SELECT RAISE(ABORT,'immutable identity/projection'); END;

CREATE TRIGGER run_provenance_immutable BEFORE UPDATE OF run_id,phase_id,purpose,planned_id ON run_binding
BEGIN SELECT RAISE(ABORT,'immutable run provenance'); END;
CREATE TRIGGER run_no_delete BEFORE DELETE ON run_binding
BEGIN SELECT RAISE(ABORT,'retain started run'); END;
CREATE TRIGGER job_identity_immutable BEFORE UPDATE OF job_id,idempotency_key ON job_state
BEGIN SELECT RAISE(ABORT,'immutable job identity'); END;
CREATE TRIGGER phase_identity_immutable BEFORE UPDATE OF phase_id ON phase_state
BEGIN SELECT RAISE(ABORT,'immutable phase identity'); END;
