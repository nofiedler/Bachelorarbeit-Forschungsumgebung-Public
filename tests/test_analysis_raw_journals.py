"""Real SQLite/CAS fixtures, isolated from research data; no external requests."""
import csv
from io import StringIO
import json
from uuid import uuid4

import pytest

from test_adapter import prepared, response
from research_env.analysis_raw_journals import journal_files
from research_env.artifacts import IntegrityError
from research_env.domain import (Artifact, ModelCall, Run, RunState, TransportAttempt, canonical, digest)
from research_env.providers import KnownTransient, MockAdapter, WireResponse


def owned_records(register, run_id):
    """Same ownership root as package selection, restricted to one test run."""
    run = register.get(run_id, Run)
    records = {str(run.id): run}
    for row in register.connection.execute('SELECT id FROM register_record').fetchall():
        entity = register.get(row[0])
        if getattr(entity, 'run_id', None) == run_id:
            records[str(entity.id)] = entity
    for transport in register.all(TransportAttempt):
        if str(transport.call_id) in records:
            records[str(transport.id)] = transport
    return records


def read_files(register, records):
    register.connection.execute('BEGIN')
    try:
        return journal_files(register, records)
    finally:
        register.connection.rollback()


def test_raw_retry_metadata_bytes_and_scope_are_preserved(tmp_path):
    counter = iter(range(100, 1000))
    metadata = WireResponse(200, b'{"data":{"id":"synthetic-generation-1","total_cost":123456.789}}', {})
    r, _, journal, adapter, call, _ = prepared(tmp_path,
        MockAdapter([KnownTransient('synthetic'), response()], metadata=metadata),
        clock=lambda: next(counter), sleep=lambda _: None)
    try:
        adapter.execute_attempt(journal, call.id)
        transport = journal._latest(call.id)
        journal.fetch_metadata(adapter, transport.id)
        # Failed metadata requests are observed raw data too, even without CAS.
        mid = str(uuid4())
        body = {'generation_id': 'synthetic-generation-1', 'at': '2026-10-08T00:00:00+00:00',
                'process_id': str(journal.process.id)}
        with r.transaction():
            r.connection.execute('INSERT INTO adapter_metadata VALUES (?,?,?,?,?,?,?)',
                (mid, str(transport.id), 'synthetic-generation-1', 'outcome_unknown', None, canonical(body), digest(body)))
        original = [dict(row) for row in r.connection.execute('SELECT transport_id,body,sha256 FROM adapter_attempt ORDER BY transport_id')]
        records = owned_records(r, call.run_id)
        before = r.connection.total_changes
        files = read_files(r, records)
        assert r.connection.total_changes == before
        assert json.loads(files['raw-data/adapter_attempt.json'])['rows'] == original
        details = list(csv.DictReader(StringIO(files['raw-data/attempt-details.csv'].decode())))
        retry = next(row for row in details if row['attempt_number'] == '2')
        original_retry = json.loads(next(row['body'] for row in original if row['transport_id'] == str(transport.id)))
        assert retry['retry_wait.monotonic_start'] == original_retry['retry_wait']['monotonic_start']
        assert retry['retry_wait.monotonic_end'] == original_retry['retry_wait']['monotonic_end']
        metadata_rows = json.loads(files['raw-data/adapter_metadata.json'])['rows']
        assert {row['status'] for row in metadata_rows} == {'saved', 'outcome_unknown'}
        assert next(row for row in metadata_rows if row['id'] == mid)['artifact_id'] is None
        manifest = json.loads(files['raw-data/manifest.json'])
        assert manifest['row_counts']['adapter_attempt'] == 2
        assert not manifest['missing_adapter_call_ids'] and not manifest['missing_adapter_attempt_ids']
        assert manifest['process_count'] >= 1
        index = list(csv.DictReader(StringIO(files['raw-data/artifact-index.csv'].decode())))
        assert len(index) == sum(isinstance(obj, Artifact) for obj in records.values())
        assert all(row['object_path'] == 'objects/sha256/' + row['sha256'] for row in index)
    finally:
        r.close()


def test_foreign_run_journals_are_excluded(tmp_path):
    r, _, journal, adapter, first, _ = prepared(tmp_path)
    try:
        original = r.get(first.run_id, Run)
        r.set_state(original.id, RunState(execution='terminal', terminal_cause='technical_failure'),
                    reason='SYNTHETIC fixture: close first run before creating another')
        second, _ = r.start_other(original.phase_id, original.configuration_version_id,
            decision='TECHNICAL-FIXTURE: second independent run',
            technical_evidence_ids=original.technical_evidence_ids, idempotency_key=str(uuid4()))
        preview = journal.preview(adapter, second.id, {'currency': 'USD', 'categories': {}})
        order = journal.record_start_order(second.id, preview.id, person='TECHNICAL-FIXTURE: tester',
            decision='synthetic', roles=('analyzer',), synthetic_fixture=True)
        foreign = journal.prepare(adapter, second.id, 'analyzer', order_id=order,
            messages=[{'role': 'user', 'content': 'FOREIGN RUN MUST BE EXCLUDED'}])
        files = read_files(r, owned_records(r, first.run_id))
        data = b'\n'.join(files.values())
        assert str(second.id).encode() not in data
        assert str(foreign.id).encode() not in data
        assert str(first.id).encode() in data
    finally:
        r.close()


def test_missing_schema_and_changed_body_checksum_fail_closed(tmp_path):
    r, _, _, _, call, _ = prepared(tmp_path)
    try:
        records = owned_records(r, call.run_id)
        r.connection.execute('BEGIN')
        try:
            r.connection.execute('DROP TABLE adapter_metadata')
            with pytest.raises(IntegrityError, match='nicht lesbar'):
                journal_files(r, records)
        finally:
            r.connection.rollback()
        r.connection.execute('BEGIN')
        try:
            r.connection.execute("UPDATE adapter_attempt SET sha256=?", ('0' * 64,))
            with pytest.raises(IntegrityError, match='Prüfsumme'):
                journal_files(r, records)
        finally:
            r.connection.rollback()
    finally:
        r.close()


def test_register_only_calls_are_explicitly_missing_and_no_settings_are_read(tmp_path):
    r, _, _, _, call, _ = prepared(tmp_path)
    try:
        original = r.get(call.run_id, Run)
        with r.transaction():
            plain_call = r._put(ModelCall(code='SYNTHETIC-register-only', run_id=original.id,
                node='migrate', sequence=2, model_package_id=call.model_package_id,
                request_hash=call.request_hash, messages_artifact_id=call.messages_artifact_id,
                parameters=call.parameters, input_artifact_ids=(), allowed_paths=(), tools=()))
        statements = []
        r.connection.set_trace_callback(statements.append)
        files = read_files(r, owned_records(r, call.run_id))
        manifest = json.loads(files['raw-data/manifest.json'])
        assert manifest['missing_adapter_call_ids'] == [str(plain_call.id)]
        assert not any('application_settings' in statement for statement in statements)
        assert not any('model_catalog' in statement for statement in statements)
    finally:
        r.close()


def test_open_transaction_required(tmp_path):
    r, _, _, _, call, _ = prepared(tmp_path)
    try:
        with pytest.raises(RuntimeError, match='Lesetransaktion'):
            journal_files(r, owned_records(r, call.run_id))
    finally:
        r.close()


def test_secret_in_journal_blocks_export_without_rewriting_original(tmp_path):
    r, _, _, _, call, _ = prepared(tmp_path)
    try:
        records = owned_records(r, call.run_id)
        original = dict(r.connection.execute('SELECT * FROM adapter_attempt').fetchone())
        body = json.loads(original['body'])
        body['stop_reason'] = 'Bearer synthetic-sensitive-token'
        changed = canonical(body)
        r.connection.execute('BEGIN')
        try:
            r.connection.execute('UPDATE adapter_attempt SET body=?,sha256=? WHERE transport_id=?',
                (changed, digest(body), original['transport_id']))
            with pytest.raises(IntegrityError, match='Zugangsschlüssel'):
                journal_files(r, records)
            assert r.connection.execute('SELECT body FROM adapter_attempt').fetchone()[0] == changed
        finally:
            r.connection.rollback()
    finally:
        r.close()
