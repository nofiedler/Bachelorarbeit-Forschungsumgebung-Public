"""Cutoff-bound companion table for the supplementary run timeline.

Step event spans supplement the fixed aggregate measurements. They are never
substituted for the monotonic UF5 pipeline total or used to reselect outcomes.
"""
import hashlib
import csv
from io import StringIO
import json
from pathlib import Path
from uuid import UUID

from .analysis_export import csv_bytes
from .analysis_step_ui import event_times, steps
from .domain import Artifact, canonical, digest

FILENAMES = ('pipeline-steps.csv', 'pipeline-steps.json', 'pipeline-steps-manifest.json')
SOURCE_FILES = ('analysis_timeline_export.py', 'analysis_step_ui.py', 'analysis_resources.py',
                'workflow_views.py', 'numeric.py', 'domain.py', 'analysis.py', 'analysis_export.py')


def timeline_binding():
    root = Path(__file__).parent
    return {'version': 'research-pipeline-timeline-v1',
            'files': {name: hashlib.sha256((root / name).read_bytes()).hexdigest() for name in SOURCE_FILES}}


def timeline_files(snapshot, register, snapshot_id):
    cutoff = register.get(UUID(str(snapshot_id)), Artifact)
    events = event_times(register, snapshot, snapshot_id)
    rows = []
    for item in snapshot['rows']:
        identity = {'planned_id': item['plan']['id'], 'run_id': item.get('run_id'),
                    'position': item['plan']['position'], 'block_id': item['plan']['block_id'],
                    'cell_key': item['plan']['cell_key'], 'configuration_id': item['configuration_id'],
                    'configuration_hash': item['configuration_hash'], 'candidate_hash': item.get('candidate_hash')}
        pipeline, checks = steps(item, events.get(item.get('run_id'), ()))
        for index, step in enumerate(pipeline, 1):
            rows.append({**identity, 'step_index': index, 'kind': 'pipeline', 'node': step['key'],
                'title': step['title'], 'execution_status': step['status'], 'models': step['models'],
                'model_step': step['model_step'], 'calls': step['calls'], 'transports': step['transports'],
                'call_ids': step['call_ids'], 'event_time': step['timing'], 'tokens': step['tokens'],
                'cost': step['cost']})
        for index, check in enumerate(checks, 10):
            rows.append({**identity, 'step_index': index, 'kind': 'independent_check',
                'title': check['title'], 'event_time': check['timing']})
    used = {identity for row in rows for identity in row['event_time']['event_ids']}
    source = {'data_hash': digest(snapshot), 'snapshot_artifact_id': str(cutoff.id),
              'snapshot_artifact_sha256': cutoff.sha256, 'cutoff': cutoff.created_at.isoformat(),
              'source_binding': timeline_binding(),
              'event_hashes': {str(event.id): digest(event) for run_events in events.values()
                               for event in run_events if str(event.id) in used}}
    metadata = {'source': source, 'analysis_version': snapshot['analysis_version'],
                'definition': 'Start/end event spans within the same process, accumulated per pipeline node; not model-only inference time and not a replacement for the active pipeline total.',
                'missing': 'value=null means incomplete/unavailable; known_subtotal preserves measured portions; disabled/unexecuted nodes remain explicitly labelled.',
                'planned_ids': [row['plan']['id'] for row in snapshot['rows']],
                'independent_checks': 'Separate from the nine pipeline steps and their active pipeline total.'}
    csv_content = csv_bytes(rows, result={'data_hash': source['data_hash'],
        'analysis_version': snapshot['analysis_version']}, unit='s / tokens / currency; explicit per observation',
        denominators={'planned_ids': metadata['planned_ids']})
    from .analysis_presentation import field_description
    metadata['columns'] = [{'name': name, 'description': field_description(name)} for name in
                           csv.DictReader(StringIO(csv_content.split(b'\n', 1)[0].decode())).fieldnames]
    files = {'pipeline-steps.csv': (csv_content, 'text/csv; charset=utf-8'),
             'pipeline-steps.json': (canonical({**metadata, 'rows': rows}).encode(), 'application/json')}
    manifest = {**metadata, 'row_count': len(rows), 'files': {
        name: {'sha256': hashlib.sha256(content).hexdigest(), 'byte_count': len(content),
               'mime_type': mime, 'unit': 's / tokens / currency; explicit per observation',
               'denominator_sets': {'planned_ids': metadata['planned_ids']}, 'row_count': len(rows)}
        for name, (content, mime) in files.items()}}
    files['pipeline-steps-manifest.json'] = (canonical(manifest).encode(), 'application/json')
    return files


def attach_timeline(presentation, companion):
    """Extend an export bundle, leaving the pure result presentation untouched."""
    output = dict(presentation)
    manifest = json.loads(output['manifest.json'][0])
    timeline_manifest = json.loads(companion['pipeline-steps-manifest.json'][0])
    manifest['supplemental_sources'] = {'pipeline_steps': timeline_manifest['source']}
    for name, (content, mime) in companion.items():
        output[name] = (content, mime)
        manifest['files'][name] = {'data_hash': manifest['data_hash'],
            'analysis_version': manifest['analysis_version'], 'sha256': hashlib.sha256(content).hexdigest(),
            'byte_count': len(content), 'mime_type': mime, 'unit': 's / tokens / currency; explicit per observation',
            'denominator_sets': {'planned_ids': timeline_manifest['planned_ids']},
            'row_count': timeline_manifest['row_count']}
    # The dictionary should enumerate companion columns too.
    output['manifest.json'] = (canonical(manifest).encode(), 'application/json')
    from .analysis_presentation import data_dictionary
    result = {'data_hash': manifest['data_hash'], 'analysis_version': manifest['analysis_version']}
    dictionary = canonical(data_dictionary(result, output)).encode()
    output['data-dictionary.json'] = (dictionary, 'application/json')
    manifest['files']['data-dictionary.json'].update(sha256=hashlib.sha256(dictionary).hexdigest(), byte_count=len(dictionary))
    output['manifest.json'] = (canonical(manifest).encode(), 'application/json')
    return output
