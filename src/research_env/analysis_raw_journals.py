"""Read-only export of recorded adapter detail outside the immutable register.

The caller supplies the package's selected records and an open read transaction.
These are export-time originals, never a replacement for confirmed measurements.
"""
import csv
import hashlib
from io import StringIO
import json
import sqlite3
from uuid import UUID

from .artifacts import IntegrityError
from .domain import Artifact, ModelCall, ProcessInstance, Run, TransportAttempt, canonical, digest
from .providers import safe_bytes


FORMAT = 'research-raw-adapter-journals-v1'
TABLES = {
    'adapter_start_order': ('run_id', ('id', 'run_id', 'artifact_id', 'body', 'sha256')),
    'adapter_call': ('call_id', ('call_id', 'order_id', 'request_artifact_id', 'request_hash', 'created_process', 'body', 'sha256')),
    'adapter_attempt': ('transport_id', ('transport_id', 'body', 'sha256')),
    'adapter_metadata': ('transport_id', ('id', 'transport_id', 'generation_id', 'status', 'artifact_id', 'body', 'sha256')),
}
DESCRIPTIONS = {
    'adapter_start_order': 'Gespeicherter Startauftrag mit Person, Entscheidung, Freigabe und Vorschauverweis.',
    'adapter_call': 'Unveränderliche Bindung des logischen Aufrufs an Auftrag, Request und Prozess.',
    'adapter_attempt': 'Aktueller gespeicherter Versuchsjournalstand: Dispatch, Antwortzeit, Retry-Wartezeiten, Unterbrechungen, Wiederaufnahme und Stoppgrund.',
    'adapter_metadata': 'Jeder dokumentierte Metadatenabruf einschließlich fehlgeschlagener oder ungeklärter Abrufe; saved bezeichnet den Abrufstatus, keine abschließende Kostenbewertung.',
}


def _read_rows(connection, table, identities):
    key, columns = TABLES[table]
    # Even an empty scope validates the required migrated schema. Missing tables
    # must not silently become an apparently complete empty journal.
    projection = ','.join(columns)
    try:
        connection.execute(f'SELECT {projection} FROM {table} LIMIT 0')
        rows = []
        identities = sorted(identities)
        for start in range(0, len(identities), 400):
            batch = identities[start:start + 400]
            rows.extend(dict(row) for row in connection.execute(
                f'SELECT {projection} FROM {table} WHERE {key} IN ({",".join("?" for _ in batch)})', batch))
    except sqlite3.DatabaseError as error:
        raise IntegrityError(f'Erforderliches Rohdatenjournal {table} ist nicht lesbar') from error
    for row in rows:
        try:
            body = json.loads(row['body'])
        except (TypeError, ValueError) as error:
            raise IntegrityError(f'Beschädigtes Rohdatenjournal {table}') from error
        if not isinstance(body, dict) or digest(body) != row['sha256']:
            raise IntegrityError(f'Prüfsumme des Rohdatenjournals {table} stimmt nicht')
        # Preserve originals; never silently redact or normalize their content.
        if safe_bytes(canonical(row).encode())[1]:
            raise IntegrityError('Rohdatenjournal enthält einen nicht exportierbaren Zugangsschlüssel')
    return sorted(rows, key=lambda row: tuple(str(row[column]) for column in columns))


def _csv(rows, columns):
    output = StringIO(newline='')
    writer = csv.DictWriter(output, columns, lineterminator='\n')
    writer.writeheader()
    writer.writerows({key: canonical(value) if isinstance(value, (list, dict)) else value
                     for key, value in row.items()} for row in rows)
    return output.getvalue().encode('utf-8')


def journal_files(register, records):
    """Return checksummed package files; require the export's existing snapshot."""
    if not register.connection.in_transaction:
        raise RuntimeError('Rohdatenjournale benötigen die konsistente Lesetransaktion des Exports')
    runs = {str(obj.id): obj for obj in records.values() if isinstance(obj, Run)}
    calls = {str(obj.id): obj for obj in records.values() if isinstance(obj, ModelCall)}
    transports = {str(obj.id): obj for obj in records.values() if isinstance(obj, TransportAttempt)}
    if any(str(call.run_id) not in runs for call in calls.values()) or any(
            str(transport.call_id) not in calls for transport in transports.values()):
        raise IntegrityError('Rohdatenjournal-Scope enthält ungebundene Aufrufe oder Versuche')
    scopes = {'adapter_start_order': runs, 'adapter_call': calls,
              'adapter_attempt': transports, 'adapter_metadata': transports}
    tables = {table: _read_rows(register.connection, table, scope) for table, scope in scopes.items()}
    orders = {row['id']: row for row in tables['adapter_start_order']}
    call_rows = {row['call_id']: row for row in tables['adapter_call']}
    attempt_rows = {row['transport_id']: row for row in tables['adapter_attempt']}
    for identity, row in call_rows.items():
        call, body = calls[identity], json.loads(row['body'])
        if (row['order_id'] not in orders or orders[row['order_id']]['run_id'] != str(call.run_id)
                or row['request_artifact_id'] != str(call.messages_artifact_id)
                or row['request_hash'] != call.request_hash
                or any(body.get(key) != row[key] for key in
                       ('call_id', 'order_id', 'request_artifact_id', 'request_hash', 'created_process'))):
            raise IntegrityError('Aufrufjournal passt nicht zu seinen Originalbindungen')
    for row in orders.values():
        body = json.loads(row['body'])
        if body.get('id') != row['id'] or body.get('run_id') != row['run_id']:
            raise IntegrityError('Startjournal passt nicht zum gespeicherten Lauf')
    for identity, row in attempt_rows.items():
        body, transport = json.loads(row['body']), transports[identity]
        if (body.get('transport_id') != identity
                or body.get('request_hash') != calls[str(transport.call_id)].request_hash):
            raise IntegrityError('Versuchsjournal passt nicht zum gespeicherten Request')
    for row in tables['adapter_metadata']:
        if json.loads(row['body']).get('generation_id') != row['generation_id']:
            raise IntegrityError('Metadatenjournal passt nicht zur gespeicherten Generation')
    # A registered transport from a recorded adapter call always has an attempt
    # row atomically created with it. Register-only legacy/test calls may not.
    if any(str(transport.call_id) in call_rows and identity not in attempt_rows
           for identity, transport in transports.items()):
        raise IntegrityError('Gespeicherter Adapterversuch hat kein Rohdatenjournal')

    clock_ids = set()
    def clock_refs(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ('created_process', 'recovery_process', 'process_id') and item:
                    clock_ids.add(str(item))
                clock_refs(item)
        elif isinstance(value, list):
            for item in value:
                clock_refs(item)
    for rows in tables.values():
        for row in rows:
            clock_refs(json.loads(row['body']))
    processes = []
    for identity in sorted(clock_ids):
        process = register.get(UUID(identity), ProcessInstance)
        processes.append({'kind': 'ProcessInstance', 'sha256': digest(process),
                          'payload': process.model_dump(mode='json')})

    attempts = []
    for identity, row in attempt_rows.items():
        body, transport = json.loads(row['body']), transports[identity]
        call = calls[str(transport.call_id)]
        output = {'run_id': str(call.run_id), 'call_id': str(call.id), 'node': call.node,
                  'transport_id': identity, 'attempt_number': transport.number,
                  'body_sha256': row['sha256'], 'stop_reason': body.get('stop_reason'),
                  'metadata_issue': body.get('metadata_issue'),
                  'interrupted_retry_waits': body.get('interrupted_retry_waits', []),
                  'raw_id': body.get('raw_id'), 'envelope_id': body.get('envelope_id'),
                  'parsed_id': body.get('parsed_id')}
        for kind in ('dispatch', 'completed', 'retry_wait'):
            timing = body.get(kind) or {}
            for field in ('process_id', 'utc_start', 'utc_end', 'monotonic_start', 'monotonic_end'):
                output[f'{kind}.{field}'] = timing.get(field)
        attempts.append(output)
    attempt_columns = ('run_id', 'call_id', 'node', 'transport_id', 'attempt_number',
                       'body_sha256', 'stop_reason', 'metadata_issue', 'interrupted_retry_waits',
                       'raw_id', 'envelope_id', 'parsed_id') + tuple(
        f'{kind}.{field}' for kind in ('dispatch', 'completed', 'retry_wait')
        for field in ('process_id', 'utc_start', 'utc_end', 'monotonic_start', 'monotonic_end'))
    metadata = []
    for row in tables['adapter_metadata']:
        transport = transports[row['transport_id']]
        call = calls[str(transport.call_id)]
        metadata.append({'run_id': str(call.run_id), 'call_id': str(call.id), **row})

    files = {f'raw-data/{name}.json': canonical({'schema': FORMAT, 'table': name,
              'description': DESCRIPTIONS[name], 'columns': list(TABLES[name][1]),
              'rows': rows}).encode() for name, rows in tables.items()}
    files['raw-data/processes.json'] = canonical({'schema': FORMAT, 'records': processes}).encode()
    files['raw-data/attempt-details.csv'] = _csv(attempts, attempt_columns)
    files['raw-data/metadata-requests.csv'] = _csv(metadata,
        ('run_id', 'call_id', *TABLES['adapter_metadata'][1]))
    artifact_rows = [{'artifact_id': str(obj.id), 'sha256': obj.sha256,
        'object_path': 'objects/sha256/' + obj.sha256, 'original_name': obj.original_name,
        'run_id': str(obj.run_id) if obj.run_id else None,
        'call_id': str(obj.call_id) if obj.call_id else None,
        'measurement_id': str(obj.measurement_id) if obj.measurement_id else None,
        'artifact_type': obj.artifact_type, 'mime_type': obj.mime_type,
        'byte_count': obj.byte_count} for obj in records.values() if isinstance(obj, Artifact)]
    files['raw-data/artifact-index.csv'] = _csv(sorted(artifact_rows, key=lambda row: row['artifact_id']),
        ('artifact_id', 'sha256', 'object_path', 'original_name', 'run_id', 'call_id',
         'measurement_id', 'artifact_type', 'mime_type', 'byte_count'))
    files['raw-data/README.md'] = (
        '# Ergänzende Rohdatenjournale\n\n'
        'Originale der zum Exportzeitpunkt gespeicherten Adapterjournale für die im Paket '
        'enthaltenen Läufe, Aufrufe und Transportversuche. Die JSON-Zeilen bewahren alle '
        'Spalten der vier Tabellen, einschließlich des unveränderten JSON-Textes in `body`. '
        '`sha256` ist die gespeicherte Prüfsumme des kanonisch serialisierten Body-Inhalts. '
        'CSV-Dateien sind lesbare Ableitungen; JSON bewahrt weitere Originalfelder.\n\n'
        '`../presentation/runs.csv` ordnet Laufnummern, geplante IDs und tatsächliche Lauf-IDs zu. '
        '`artifact-index.csv` ordnet Originaldateinamen und Artefakt-IDs dem Objektpfad '
        '`../objects/sha256/…` zu. Die vollständigen unveränderlichen Registerdatensätze '
        'liegen in `../registers/records.jsonl`. Identische Dateibytes werden über ihren Hash '
        'einmal gespeichert; der Index bewahrt die unterschiedlichen Herkunftszuordnungen.\n\n'
        'Diese Journale können später erfasste Informationen enthalten als der bestätigte '
        'Analysestand unter `analysis/`; sie wählen keine neueren Bewertungen für diese Analyse aus. '
        'Das Versuchsjournal enthält den zuletzt gespeicherten Zustand, keine vollständige '
        'Historie jedes Zwischenstands. Unveränderliche Transportereignisse, Requests, Antworten, '
        'Anbieternutzung und Originalbelege stehen zusätzlich im Register und CAS des Pakets.\n\n'
        'UTC-Zeitpunkte und monotone Zähler bleiben unverändert. Monotone Differenzen sind nur '
        'innerhalb derselben Prozess-ID sinnvoll; `processes.json` dokumentiert diese Prozesse. '
        'Retry-Wartezeit ist weder reine Inferenzzeit noch ein Ersatz für aktive Pipelinezeit. '
        'Leerfelder bedeuten nicht erfasst, nicht null. `saved` beim Metadatenabruf bedeutet '
        'nicht automatisch eine gültige oder abschließend bekannte Kostenangabe.\n\n'
        'Scope und fehlende Journalbindungen stehen in `manifest.json`. Nicht erfasste Werte '
        'werden nicht ergänzt. Dieser Export enthält keine Zugangsschlüsseldateien, '
        'Anwendungseinstellungen, fremden Laufjournale, Docker-Images oder vollständige '
        'Instanzdatenbank; nicht registrierte Dateien und reine Betriebsprojektionen sind '
        'nicht Teil dieses Rohdatenpakets.\n').encode()
    manifest = {'schema': FORMAT, 'scope': 'export_time_selected_package_runs',
        'run_ids': sorted(runs), 'call_ids': sorted(calls), 'transport_ids': sorted(transports),
        'row_counts': {name: len(rows) for name, rows in tables.items()},
        'artifact_count': len(artifact_rows), 'process_count': len(processes),
        'missing_adapter_call_ids': sorted(set(calls) - set(call_rows)),
        'missing_adapter_attempt_ids': sorted(set(transports) - set(attempt_rows)),
        'missing_journal_meaning': 'Kein gespeicherter Journalstand vorhanden; keine Nullmessung. Ein registrierter Lauf ohne Modellaufrufe benötigt kein Adapterjournal.',
        'columns': {'body': 'Unveränderter gespeicherter JSON-Text.',
                    'sha256': 'Gespeicherter SHA-256 des kanonischen Body-Inhalts, vor Export überprüft.',
                    'monotonic_start/monotonic_end': 'Unveränderte prozessgebundene Zähler in Sekunden; nur bei gleicher Prozess-ID vergleichen.',
                    'status': 'Originalstatus des Metadatenabrufs: dispatching, saved, failed oder outcome_unknown.',
                    'artifact_id': 'ID des Originalbelegs im Paketregister, soweit ein Beleg vorliegt.'},
        'files': {name: {'sha256': hashlib.sha256(data).hexdigest(), 'byte_count': len(data)}
                  for name, data in sorted(files.items())}}
    files['raw-data/manifest.json'] = canonical(manifest).encode()
    return files
