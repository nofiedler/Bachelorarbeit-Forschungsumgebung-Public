"""Fixed internal syntax/boot/test route through the real M4 isolated runtime."""
import hashlib
import json
from uuid import UUID

from .artifacts import IntegrityError
from .domain import Artifact, CandidateSnapshot, Job, Run


class InternalRunner:
    def __init__(self, sandbox):
        self.sandbox = sandbox
        self.register = sandbox.register
        self.store = sandbox.store
        self.versions = dict(vars(sandbox.images))

    def preflight(self, run_id):
        self.sandbox.package(run_id)
        self.sandbox._asset_tree('evaluation/development/m2-v0.1')
        for reference in self.versions.values():
            image = self.sandbox.docker.images.get(reference)
            if not reference.startswith('sha256:') and '@sha256:' not in reference:
                raise IntegrityError('Runtimeimage ist nicht durch Digest gebunden')
            if not image.id:
                raise IntegrityError('Runtimeimage nicht vorhanden')

    def writers(self, run_id):
        return self.sandbox.writers(run_id)

    def close(self):
        self.sandbox.docker.close()

    def _dispatch_allowed(self,run_id):
        row=self.register.connection.execute('SELECT status FROM pipeline_binding WHERE run_id=?',(str(run_id),)).fetchone()
        if row and row['status'] in ('abort_requested','recovery_required','completed'):
            raise IntegrityError('Persistenter Laufstopp: kein weiterer interner Dispatch')

    def execute(self, job_id, candidate_id, tests_id, *, node, continuation=None):
        job = self.register.get(job_id, Job)
        candidate = self.register.get(candidate_id, CandidateSnapshot)
        tests = self.register.get(tests_id, Artifact)
        if job.run_id != candidate.run_id or tests.run_id != job.run_id or tests.access_scope != 'role':
            raise IntegrityError('Interner Job/Tests/Kandidat nicht laufgleich')
        row = self.register.connection.execute('SELECT * FROM pipeline_runner WHERE run_id=? AND node=?', (str(job.run_id), node)).fetchone()
        if row:
            if row['candidate_hash'] != candidate.tree_hash or row['tests_hash'] != tests.sha256:
                raise IntegrityError('Interner Runnerinput verändert')
            if row['status'] == 'completed':
                return json.loads(self.store.read(row['result_id']))
            if not continuation:
                raise IntegrityError('Abgebrochener interner Job braucht bewusste Recovery')
            # Recovery retains all raw spools and acts on this installation's
            # recorded resources only. A new execution is a test measurement,
            # never a new model call or changed test content.
            self.sandbox.recover(decision='stop_owned', run_id=job.run_id)
        else:
            with self.register.transaction():
                self.register.connection.execute('INSERT INTO pipeline_runner VALUES (?,?,?,?,?,?)',
                    (str(job.run_id), node, candidate.tree_hash, tests.sha256, 'running', None))
        observations, classification = [], 'passed'
        try:
            for stage in ('syntax', 'boot', 'tests'):
                self._dispatch_allowed(job.run_id)
                handle = self.sandbox.allocate(job.id, candidate.id, profile='internal' if stage == 'tests' else stage,
                                                internal_tests_id=tests.id if stage == 'tests' else None)
                self._dispatch_allowed(job.run_id)
                handle.start()
                self._dispatch_allowed(job.run_id)
                container = next(c for c in handle.containers if c.name.endswith('-syntax' if stage == 'syntax' else '-candidate'))
                # Docker wait has no application wallclock cutoff. Manual stop
                # remains available through the recorded handle/owned resources.
                self._dispatch_allowed(job.run_id)
                native = container.wait()
                diagnosis = handle.observe()
                container.reload()
                state = container.attrs.get('State', {})
                technical = (state.get('OOMKilled') or state.get('Error') or handle.errors or
                             (native['StatusCode'] >= 128 and native['StatusCode'] != 255) or container.id in handle.requested_stops)
                handle.abort(reason='Interner ' + stage + ' Job abgeschlossen')
                logs = []
                for a in self.register.all(Artifact):
                    if a.run_id == job.run_id and a.artifact_type == 'sandbox_log' and a.access_scope == 'public_development':
                        # Restrict to this execution's log observations, no other
                        # run/evaluator/global log inventory is handed to roles.
                        links = self.register.connection.execute("SELECT artifact_id FROM sandbox_observation WHERE execution_id=? AND kind='log_retained'", (handle.id,)).fetchall()
                        if any(json.loads(self.store.read(x[0])).get('artifact_id') == str(a.id) for x in links):
                            logs.append({'artifact_id': str(a.id), 'content': self.store.read(a.id).decode('utf-8', errors='replace')})
                observations.append({'stage': stage, 'execution_id': handle.id, 'exit_code': native['StatusCode'], 'native_state': state,
                                     'diagnosis_id': str(diagnosis.id), 'logs': logs, 'suite_kind': 'development'})
                if native['StatusCode'] != 0:
                    # Nonzero alone is never an attributed candidate failure.
                    # Require a fixed checker/boot failure or native PHP error
                    # evidence. Signals/OOM/manual stops remain technical.
                    text = '\n'.join(x['content'] for x in logs)
                    php_failure = any(x in text for x in ('Parse error:', 'Fatal error:', 'Uncaught ', 'PHP Parse error', 'PHP Fatal error'))
                    syntax_failure = stage == 'syntax' and 'syntax' in text.lower() and 'error' in text.lower()
                    wrapper_failure = 'in Command line code' in text
                    classification = 'technical_failure' if technical or wrapper_failure or not (php_failure or syntax_failure) else 'candidate_failure'
                    break
        except Exception as exc:
            classification = 'protection_failure' if isinstance(exc, IntegrityError) else 'technical_failure'
            observations.append({'cause': str(exc), 'diagnostic_missing': 'Job did not complete; retained sandbox journal/spools require conscious recovery'})
            with self.register.transaction():
                self.register.connection.execute("UPDATE pipeline_runner SET status='recovery_required' WHERE run_id=? AND node=?", (str(job.run_id), node))
        result = {'schema': 'internal-runner-v1', 'run_id': str(job.run_id), 'node': node,
                  'candidate_hash': candidate.tree_hash, 'internal_tests_id': str(tests.id), 'tests_hash': tests.sha256,
                  'classification': classification, 'observations': observations, 'runtime_images': self.versions}
        artifact = self.store.json(result, run_id=job.run_id, artifact_type='internal_runner_receipt')
        if classification in ('passed', 'candidate_failure'):
            with self.register.transaction():
                self.register.connection.execute("UPDATE pipeline_runner SET status='completed',result_id=? WHERE run_id=? AND node=?",
                    (str(artifact.id), str(job.run_id), node))
        return result
