"""Operational network-free role fixture; no model capability/study claim."""
import json
from .providers import MockAdapter, WireResponse
from .domain import canonical


class PipelineMockAdapter(MockAdapter):
    def _send(self, request):
        data = json.loads(request)
        role = json.loads(data['messages'][1]['content'])['role']
        output = {'schema': 'roles-v1', 'role': role}
        if role in ('analyzer', 'planner'):
            output['analysis' if role == 'analyzer' else 'plan'] = 'TECHNICAL-FIXTURE: deterministic offline ' + role
        elif role == 'review':
            output.update(changes_required=False, reason='TECHNICAL-FIXTURE: no model judgment', findings=[])
        elif role == 'test':
            output['files'] = [{'path': 'internal/check.php', 'content': '<?php echo "TECHNICAL_FIXTURE_INTERNAL_ONLY";'}]
        else:
            output['files'] = [{'path': 'routes/study.php', 'content': '<?php // TECHNICAL-FIXTURE: offline ' + role}]
        self.send_count += 1
        return WireResponse(200, canonical({'id': 'synthetic-pipeline-' + str(self.send_count), 'model': data['model'],
            'provider': 'mock', 'choices': [{'message': {'content': canonical(output)}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 0, 'completion_tokens': 0, 'cost': 0}}).encode(), {})
