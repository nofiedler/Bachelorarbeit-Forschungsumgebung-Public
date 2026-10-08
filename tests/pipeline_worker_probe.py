"""Native offline worker/crash probe. No real provider or external network."""
import argparse
import json
import os
from pathlib import Path
import signal
import sys

sys.path.insert(0, str(Path(__file__).parent))
from research_env import worker
from research_env.config import Settings
from research_env.adapter import CallJournal
from research_env.pipeline import Pipeline
from research_env.providers import MockAdapter
from research_env.register import Register
from research_env.scheduler import Scheduler
from test_pipeline import MockRunner, envelope, wire

p = argparse.ArgumentParser()
p.add_argument('--root', type=Path, required=True)
p.add_argument('--mode', choices=('worker', 'recover'), default='worker')
p.add_argument('--crash')
p.add_argument('--hang', choices=('provider', 'candidate', 'infrastructure', 'http'))
p.add_argument('--native', action='store_true')
p.add_argument('--operational', action='store_true')
args = p.parse_args()
settings = Settings(*(args.root / x for x in ('control', 'artifacts', 'checkpoints', 'staging')))
signal.signal(signal.SIGUSR1, lambda *_: None)


def service(register):
    sent = args.root / 'worker-send.raw'
    def on_send(request):
        with sent.open('ab', buffering=0) as stream:
            stream.write(request + b'\n'); os.fsync(stream.fileno())
        if args.hang == 'provider' and not (args.root / 'hang.marker').exists():
            (args.root / 'hang.marker').write_text('provider dispatch persisted and mock request entered')
            signal.pause()
    calls = {row['node'] for row in register.connection.execute('SELECT node FROM call_binding')}
    remaining = [x for x in ('analyzer', 'migrate', 'test') if x not in calls]
    # A prepared-before-send call still needs its one response; incorporate/raw
    # recovery consumes no response. This is a trusted offline transport fixture.
    for row in register.connection.execute("SELECT c.node,s.status FROM call_binding c JOIN transport_binding t ON c.call_id=t.call_id JOIN transport_state s ON s.transport_id=t.transport_id WHERE t.number=1"):
        if row['status'] == 'prepared':
            remaining.insert(0, row['node'])
    fixture_outputs = json.loads((args.root / 'native-role-outputs.json').read_text()) if args.native else None
    a = MockAdapter([wire(fixture_outputs[x] if fixture_outputs else envelope(x)) for x in remaining], on_send=on_send)
    if args.hang == 'http':
        # Actual OpenRouterAdapter._send + standard HTTPX socket transport,
        # rewritten ONLY in this trusted probe to an owned loopback server.
        # Synthetic capability/start-order shims remain explicit; no external
        # endpoint, real key or real provider capability/approval is claimed.
        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from research_env import providers
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_): pass
            def do_POST(self):
                request = self.rfile.read(int(self.headers['Content-Length']))
                with sent.open('ab', buffering=0) as stream:
                    stream.write(request + b'\n'); os.fsync(stream.fileno())
                role = json.loads(json.loads(request)['messages'][1]['content'])['role']
                if role == 'test':
                    (args.root / 'http-accepted.json').write_text(json.dumps({'role': role, 'path': self.path,
                        'transport': 'OpenRouterAdapter._send, standard HTTPX; local owned socket', 'headers': 'not retained',
                        'response': 'intentionally never written; no timeout'}) + '\n')
                    threading.Event().wait()
                body = wire(envelope(role)).body
                self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.daemon_threads = True
        threading.Thread(target=server.serve_forever, daemon=True).start()
        providers.CHAT_URL = f'http://127.0.0.1:{server.server_port}/offline-chat'
        a = providers.OpenRouterAdapter(api_key='sk-or-v1-artificial-key-never-valid')
        a.synthetic = a.offline = True
        a.validate_capabilities = MockAdapter.validate_capabilities.__get__(a)
        a.build_request = MockAdapter.build_request.__get__(a)
    def crash(point):
        if point == args.crash:
            os._exit(79)
    def runner_wait():
        (args.root / 'hang.marker').write_text('controlled synthetic ' + args.hang + ' wait; no scientific judgment')
        signal.pause()
    runner = MockRunner(results=('technical_failure',) if args.hang == 'infrastructure' else ('passed',), on_execute=runner_wait if args.hang in ('candidate', 'infrastructure') else None)
    if args.native:
        import docker
        from research_env.artifacts import ArtifactStore
        from research_env.pipeline_runner import InternalRunner
        from research_env.sandbox_runtime import Sandbox, RuntimeImages
        images = RuntimeImages(**json.loads((Path(__file__).parents[1] / 'src/research_env/pipeline_runtime.lock.json').read_text()))
        runner = InternalRunner(Sandbox(ArtifactStore(settings, register), docker.from_env(timeout=None), images=images, assets=Path(__file__).parents[1]))
    pipeline = Pipeline(register, CallJournal(register), a, runner, crash=crash)
    class NativeScheduler(Scheduler):
        def tick(self):
            result = super().tick()
            if register.connection.execute("SELECT 1 FROM pipeline_binding WHERE status='completed'").fetchone():
                os.kill(os.getpid(), signal.SIGTERM)
            return result
    return NativeScheduler(pipeline)


if args.mode == 'recover':
    r = Register(settings)
    s = service(r); s.boot()
    row = r.connection.execute('SELECT run_id FROM pipeline_binding WHERE status="recovery_required"').fetchone()
    if row:
        s.resume(__import__('uuid').UUID(row[0]), reason='TECHNICAL-FIXTURE: explicit native user recovery')
    s.pipeline.close(); r.close()
else:
    if args.operational:
        actual_factory = worker.pipeline_service
        def product_service(register):
            scheduler = actual_factory(register)
            actual_tick = scheduler.tick
            def tick():
                result = actual_tick()
                if register.connection.execute("SELECT 1 FROM pipeline_binding WHERE status='completed'").fetchone():
                    os.kill(os.getpid(), signal.SIGTERM)
                return result
            scheduler.tick = tick
            return scheduler
        worker.pipeline_service = product_service
    else:
        worker.pipeline_service = service
    worker.docker_components = lambda: []
    worker.main = lambda: None
    from research_env.locks import worker_lock
    with worker_lock(settings):
        worker.run(settings)
