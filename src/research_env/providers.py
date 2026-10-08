"""Fixed OpenRouter wire contract and deterministic, network-free fixtures.

Only CallJournal may dispatch. No SDK, implicit retry, redirects, environment
proxy/key discovery or client time limit. Endpoint identity is evidence-bound;
reported provider names do not prove unchanged server weights/endpoint internals.
"""
from dataclasses import dataclass
from decimal import Decimal
import hashlib
import json
import re

import httpx

from .domain import canonical
from .numeric import deterministic, sum_exact
from .register import GateError

CHAT_URL = 'https://openrouter.ai/api/v1/chat/completions'
METADATA_URL = 'https://openrouter.ai/api/v1/generation'
SAFE_HEADERS = {'content-type', 'date', 'x-request-id', 'retry-after'}
PARAMETERS = {'temperature', 'top_p', 'top_k', 'min_p', 'top_a', 'seed',
              'frequency_penalty', 'presence_penalty', 'repetition_penalty',
              'response_format', 'reasoning', 'reasoning_effort', 'verbosity',
              'stop', 'logit_bias', 'logprobs', 'top_logprobs',
              'max_tokens', 'max_completion_tokens'}
SECRET = re.compile(rb'(?:sk-or-v1-[A-Za-z0-9_-]+|Bearer\s+[^\s"\'<>]+)', re.I)
JSON_STRING = re.compile(rb'"(?:[^"\\]|\\.)*"', re.S)
UNICODE_ESCAPE = re.compile(rb'\\+u([0-9a-fA-F]{4})')


def json_bytes(data):
    return canonical(data).encode()


def decode(data):
    return json.loads(data, parse_float=Decimal,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))


def export_json(data):
    """Numbers stay exact in persisted observations (decimal strings, raw native bytes separate)."""
    if isinstance(data, Decimal):
        return str(data)
    if isinstance(data, dict):
        return {k: export_json(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [export_json(v) for v in data]
    return data


def _literal_safe(data, secret=None):
    changed = False
    if secret and secret.encode() in data:
        data = data.replace(secret.encode(), b'[REDACTED]')
        changed = True
    clean = SECRET.sub(b'[REDACTED]', data)
    return clean, changed or clean != data


def _escaped_inspection(data):
    # Inspection only. Each substitution shortens the sequence, including
    # nested encodings of a backslash. Never normalize a nonsecret raw body.
    while True:
        inspected = UNICODE_ESCAPE.sub(lambda m:chr(int(m[1],16)).encode('utf-8','surrogatepass'),data)
        if inspected == data:
            return data
        data = inspected


def safe_bytes(data, secret=None):
    clean, changed = _literal_safe(data,secret)
    def token(match):
        nonlocal changed
        try:
            value = json.loads(match[0])
        except (ValueError,UnicodeError):
            return match[0]
        inspected = _escaped_inspection(value.encode('utf-8','surrogatepass'))
        sanitized, found = _literal_safe(inspected,secret)
        if not found:
            return match[0]
        changed = True
        # Only a secret string token is rewritten. Native number tokens and
        # every nonsecret byte outside it remain exactly as received.
        return json.dumps(sanitized.decode('utf-8','surrogatepass'),ensure_ascii=True).encode()
    clean = JSON_STRING.sub(token,clean)
    # Headers and generic plaintext may contain bare JSON unicode escapes.
    inspected, found = _literal_safe(_escaped_inspection(clean),secret)
    return (inspected,True) if found else (clean,changed)


def safe_headers(headers, secret=None):
    clean, redactions = {}, {}
    for name,value in headers.items():
        name = name.lower()
        if name not in SAFE_HEADERS:
            continue
        original = str(value).encode()
        sanitized, found = safe_bytes(original,secret)
        clean[name] = sanitized.decode(errors='replace')
        if found:
            redactions[name] = {'original_sha256':hashlib.sha256(original).hexdigest(),
                                'reason':'secret_exposure_redacted'}
    return clean, redactions


@dataclass(frozen=True)
class WireResponse:
    status: int
    body: bytes
    headers: dict


class KnownTransient(Exception):
    """Fixture-only positive evidence: no HTTP request reached an inference server."""


class OpenRouterAdapter:
    synthetic = False

    def __init__(self, *, api_key=None, transport=None):
        if transport is not None and not isinstance(transport,httpx.MockTransport):
            raise GateError('Nur geprüfter Standardtransport oder netzfreier MockTransport')
        self._key = api_key
        # A MockTransport cannot accidentally dial a real endpoint.
        self.offline = isinstance(transport, httpx.MockTransport)
        self._client = httpx.Client(transport=transport or httpx.HTTPTransport(retries=0),
                                   timeout=None, follow_redirects=False, trust_env=False)

    def close(self):
        self._client.close()

    def validate_capabilities(self, package, parameters, evidence):
        model = package.exact_model_id
        if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)?', model):
            raise GateError('Exakte Modell-ID erforderlich')
        if any(part.lower() in {'auto', 'latest', 'router', 'nitro', 'floor', 'free', 'online'}
               for part in re.split(r'[/:-]', model)) or 'latest' in model.lower():
            raise GateError('Dynamischer Modellalias/Routingvariante verboten')
        expected = {'only': [package.endpoint], 'order': [package.endpoint],
                    'allow_fallbacks': False, 'require_parameters': True}
        if package.routing != expected or package.fallback not in ({}, {'models': []}):
            raise GateError('Genau ein Endpoint; keine Fallbacks/Preisfilter')
        if not package.endpoint or '://' in package.endpoint or '*' in package.endpoint:
            raise GateError('Vollständiger Provider-Slug erforderlich')
        if set(parameters) - PARAMETERS or set(parameters) - set(package.supported_parameters):
            raise GateError('Nicht unterstützte/unerlaubte Parameter')
        if not evidence or evidence.get('schema') != 'endpoint-v1':
            raise GateError('Geprüfter Endpointbeleg fehlt')
        for key, value in {'model_id': model, 'endpoint_slug': package.endpoint,
                           'provider_name': package.upstream}.items():
            if evidence.get(key) != value:
                raise GateError('Endpointbeleg passt nicht zum Modellpaket')
        if evidence.get('endpoint_is_complete') is not True or not evidence.get('source_url') or not evidence.get('retrieved_at'):
            raise GateError('Vollständiger Endpoint mit Quellenstand erforderlich')
        if not evidence.get('limits_defaults'):
            raise GateError('Anbietergrenzen/Defaults oder konkrete Unkenntnis fehlen')
        if set(parameters) - set(evidence.get('supported_parameters', [])):
            raise GateError('Parameter nicht im Endpointbeleg unterstützt')
        response_format = parameters.get('response_format', {})
        if isinstance(response_format, dict) and response_format.get('type') == 'json_schema':
            if 'structured_outputs' not in set(evidence.get('supported_parameters', [])):
                raise GateError('Der feste Modellendpunkt unterstützt laut Metadaten keine strukturierten Antwortschemata')
        required = evidence.get('required_parameters', {})
        reasoning = parameters.get('reasoning')
        if isinstance(reasoning,dict) and set(reasoning) & {'max_tokens','budget_tokens'} and reasoning != required.get('reasoning'):
            raise GateError('Keine frei gewählte Reasoningtokengrenze')
        for key in ('max_tokens', 'max_completion_tokens'):
            if key in parameters and (key not in required or parameters[key] != required[key]):
                raise GateError('Keine frei gewählte Ausgabebegrenzung')
        if any(parameters.get(k) != v for k, v in required.items()):
            raise GateError('Belegter Anbieterzwang fehlt/abweichend')
        if evidence.get('synthetic') and not self.offline:
            raise GateError('Synthetischer Beleg kein realer Modellzugang')
        return {'identity_limit': package.version_uncertainty,
                'limits_defaults': evidence['limits_defaults'], 'synthetic': self.offline}

    def build_request(self, package, messages, parameters, evidence):
        self.validate_capabilities(package, parameters, evidence)
        if not isinstance(messages, list) or not messages:
            raise GateError('Vollständiges Nachrichtenarray erforderlich')
        if any(not isinstance(m, dict) or set(m) != {'role', 'content'} or
               m['role'] not in ('system', 'user', 'assistant') or not isinstance(m['content'], str)
               for m in messages):
            raise GateError('Nur Textnachrichten ohne Tools/Referenzen')
        data = json_bytes({'model': package.exact_model_id, 'messages': messages,
                           'provider': package.routing, 'stream': False, **parameters})
        if safe_bytes(data, self._key)[1]:
            raise GateError('Secret im Request verhindert Versand')
        return data

    def estimate_expected_resources(self, basis):
        return resource_estimate(basis)

    def execute_attempt(self, journal, call_id, *, continuation=None, crash=None):
        return journal.execute(self, call_id, continuation=continuation, crash=crash)

    def parse_response(self, response, package):
        data = decode(response.body)
        if not isinstance(data, dict):
            raise GateError('Responseformat ungültig')
        model, provider = data.get('model'), data.get('provider')
        if not isinstance(model,str) or not model or provider is not None and not isinstance(provider,str):
            raise GateError('response_format_error')
        selected = data.get('openrouter_metadata', {}).get('endpoints', {}).get('available', [])
        selected_names = {e.get('provider') for e in selected if isinstance(e, dict) and e.get('selected') is True}
        if provider is None and len(selected_names) == 1:
            provider = next(iter(selected_names))
        if model and model != package.exact_model_id or provider and provider != package.upstream:
            raise GateError('reported_identity_drift')
        if len(selected_names) > 1 or any(n != package.upstream for n in selected_names):
            raise GateError('reported_identity_drift')
        choices = data.get('choices')
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise GateError('response_format_error')
        message = choices[0].get('message', {})
        if not isinstance(message, dict) or message.get('tool_calls'):
            raise GateError('response_format_error')
        content = message.get('content')
        if not isinstance(content, str) or not content:
            raise GateError('response_format_error')
        return {'content': content, 'model': model, 'provider': provider,
                'generation_id': data.get('id'), 'fingerprint': data.get('system_fingerprint'),
                'finish_reason': choices[0].get('finish_reason'),
                'native_usage': export_json(data.get('usage')), 'native_metadata': export_json(data.get('openrouter_metadata')),
                'identity_limit': package.version_uncertainty}

    def fetch_usage_metadata(self, journal, transport_id):
        return journal.fetch_metadata(self, transport_id)

    def _send(self, request):
        if not self._key:
            raise GateError('Worker-Modellschlüssel fehlt')
        response = self._client.post(CHAT_URL, content=request,
                                     headers={'Authorization': f'Bearer {self._key}', 'Content-Type': 'application/json'})
        return WireResponse(response.status_code, response.content, dict(response.headers))

    def _metadata(self, generation_id):
        if not self._key:
            raise GateError('Worker-Modellschlüssel fehlt')
        response = self._client.get(METADATA_URL, params={'id': generation_id},
                                    headers={'Authorization': f'Bearer {self._key}'})
        return WireResponse(response.status_code, response.content, dict(response.headers))


class MockAdapter(OpenRouterAdapter):
    synthetic = True

    def __init__(self, responses=None, *, metadata=None, on_send=None):
        self._key = None
        self.offline = True
        self.responses = list(responses or [])
        self.metadata = metadata
        self.on_send = on_send
        self.send_count = 0
        self.metadata_count = 0

    def close(self):
        pass

    def validate_capabilities(self, package, parameters, evidence):
        if not package.endpoint.startswith('mock://') or not package.exact_model_id.startswith('TECHNICAL-FIXTURE:'):
            raise GateError('Mock verlangt synthetisches Modellpaket')
        if set(parameters) - set(package.supported_parameters) or set(parameters) & {'max_tokens', 'max_completion_tokens', 'models', 'provider'}:
            raise GateError('Mockparameter ungültig')
        return {'synthetic': True, 'identity_limit': package.version_uncertainty,
                'limits_defaults': 'synthetic; no real provider capabilities'}

    def _send(self, request):
        self.send_count += 1
        if self.on_send:
            self.on_send(request)
        if self.responses:
            response = self.responses.pop(0)
            if isinstance(response, BaseException):
                raise response
            if callable(response):
                response = response(request)
            return response
        model = decode(request)['model']
        return WireResponse(200, json_bytes({'id': f'synthetic-generation-{self.send_count}', 'model': model,
                            'provider': 'mock', 'system_fingerprint': 'synthetic-v1',
                            'choices': [{'message': {'content': '{"text":"synthetic output"}'}, 'finish_reason': 'stop'}],
                            'usage': {'prompt_tokens': 10, 'completion_tokens': 20, 'cost': 0}}), {})

    def _metadata(self, generation_id):
        self.metadata_count += 1
        if isinstance(self.metadata, BaseException):
            raise self.metadata
        return self.metadata(generation_id) if callable(self.metadata) else self.metadata or WireResponse(404, b'{"error":{"code":404}}', {})


def decimal_value(value):
    if value is None or isinstance(value, bool) or isinstance(value, float):
        raise ValueError('Exact decimal required')
    result = Decimal(value)
    if not result.is_finite() or result < 0:
        raise ValueError('Nonnegative finite decimal required')
    return result


@deterministic
def resource_estimate(basis):
    """Explicit evidence inputs, no invented prices/token forecasts/conversion/buffer."""
    result = {'status': 'estimated', 'basis': basis, 'amount': None, 'eur': None,
              'uncertainty': basis.get('uncertainty') or 'No guarantee; missing inputs remain unknown'}
    categories = basis.get('categories', {})
    if categories and all(v.get('expected_units') is not None and v.get('price_per_unit') is not None
                          and v.get('source') for v in categories.values()):
        amount = sum((decimal_value(v['expected_units']) * decimal_value(v['price_per_unit'])
                      for v in categories.values()), Decimal(0))
        result['amount'] = str(amount)
        fx = basis.get('eur_basis')
        if fx and all(fx.get(k) is not None for k in ('rate', 'source', 'as_of', 'buffer_fraction', 'buffer_reason')):
            result['eur'] = str(amount * decimal_value(fx['rate']) * (1 + decimal_value(fx['buffer_fraction'])))
    if not basis.get('currency') or not basis.get('price_as_of'):
        result['status'] = 'unresolved'
        result['amount'] = result['eur'] = None
    return result
