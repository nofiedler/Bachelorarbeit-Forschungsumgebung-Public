-- Adapter projections supplement immutable register events/CAS artifacts.
CREATE TABLE adapter_start_order (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES run_binding(run_id),
 artifact_id TEXT NOT NULL REFERENCES register_record(id),
 body TEXT NOT NULL,
 sha256 TEXT NOT NULL
);
CREATE TRIGGER adapter_order_no_update BEFORE UPDATE ON adapter_start_order
 BEGIN SELECT RAISE(ABORT,'Startauftrag unveränderlich'); END;
CREATE TRIGGER adapter_order_no_delete BEFORE DELETE ON adapter_start_order
 BEGIN SELECT RAISE(ABORT,'Startauftrag bleibt erhalten'); END;
CREATE TABLE adapter_call (
 call_id TEXT PRIMARY KEY REFERENCES call_binding(call_id),
 order_id TEXT NOT NULL REFERENCES adapter_start_order(id),
 request_artifact_id TEXT NOT NULL REFERENCES register_record(id),
 request_hash TEXT NOT NULL,
 created_process TEXT NOT NULL,
 body TEXT NOT NULL,
 sha256 TEXT NOT NULL
);
CREATE TRIGGER adapter_call_no_update BEFORE UPDATE ON adapter_call
 BEGIN SELECT RAISE(ABORT,'Adapterrequest unveränderlich'); END;
CREATE TRIGGER adapter_call_no_delete BEFORE DELETE ON adapter_call
 BEGIN SELECT RAISE(ABORT,'Adaptercall bleibt erhalten'); END;
CREATE TABLE adapter_attempt (
 transport_id TEXT PRIMARY KEY REFERENCES transport_binding(transport_id),
 body TEXT NOT NULL,
 sha256 TEXT NOT NULL
);
CREATE TABLE adapter_metadata (
 id TEXT PRIMARY KEY,
 transport_id TEXT NOT NULL REFERENCES transport_binding(transport_id),
 generation_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('dispatching','saved','failed','outcome_unknown')),
 artifact_id TEXT REFERENCES register_record(id),
 body TEXT NOT NULL,
 sha256 TEXT NOT NULL
);
CREATE TRIGGER adapter_attempt_bound_immutable BEFORE UPDATE ON adapter_attempt
 WHEN OLD.transport_id != NEW.transport_id
 OR (json_extract(OLD.body,'$.raw_id') IS NOT NULL AND json_extract(NEW.body,'$.raw_id') IS NOT json_extract(OLD.body,'$.raw_id'))
 OR (json_extract(OLD.body,'$.envelope_id') IS NOT NULL AND json_extract(NEW.body,'$.envelope_id') IS NOT json_extract(OLD.body,'$.envelope_id'))
 OR (json_extract(OLD.body,'$.parsed_id') IS NOT NULL AND json_extract(NEW.body,'$.parsed_id') IS NOT json_extract(OLD.body,'$.parsed_id'))
 OR (json_extract(OLD.body,'$.dispatch') IS NOT NULL AND json_extract(NEW.body,'$.dispatch') IS NOT json_extract(OLD.body,'$.dispatch'))
 BEGIN SELECT RAISE(ABORT,'Dispatch-/Rohantwortbezug unveränderlich'); END;
CREATE TRIGGER adapter_attempt_no_delete BEFORE DELETE ON adapter_attempt
 BEGIN SELECT RAISE(ABORT,'Versuche bleiben erhalten'); END;
CREATE TRIGGER adapter_metadata_identity_immutable BEFORE UPDATE ON adapter_metadata
 WHEN OLD.id != NEW.id OR OLD.transport_id != NEW.transport_id OR OLD.generation_id != NEW.generation_id
 OR OLD.body != NEW.body OR OLD.sha256 != NEW.sha256
 OR (OLD.artifact_id IS NOT NULL AND OLD.artifact_id IS NOT NEW.artifact_id)
 BEGIN SELECT RAISE(ABORT,'Metadatenidentität/-rohbeleg unveränderlich'); END;
CREATE TRIGGER adapter_metadata_no_delete BEFORE DELETE ON adapter_metadata
 BEGIN SELECT RAISE(ABORT,'Metadatenversuche bleiben erhalten'); END;
