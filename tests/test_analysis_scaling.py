"""Keep multi-run analysis proportional to its selected evidence, without skipping hashes."""
from collections import Counter

import pytest
import m7_fixture
from research_env.adapter import CallJournal
from research_env.analysis_store import Analyses
from research_env.artifacts import ArtifactStore, IntegrityError
from research_env.domain import MeasurementAttempt, CriterionReviewRevision, MetricObservation
from research_env.review_ui import selection_history


def test_result_pages_do_not_decode_foreign_run_payloads(tmp_path, monkeypatch):
    from research_env.workflow_views import results, closure_view
    from research_env.domain import TestResult, Event
    env = m7_fixture.make_environment(tmp_path / 'instance')
    r = env[0]
    try:
        run, comp, raw = m7_fixture.start(env, tmp_path)
        m7_fixture.measurement(env, run, comp, raw)
        expected = results(r, run.id)
        original = r.all
        def bounded_all(cls):
            assert cls not in (MeasurementAttempt, CriterionReviewRevision, TestResult, Event)
            return original(cls)
        monkeypatch.setattr(r, 'all', bounded_all)
        assert results(r, run.id) == expected
        assert closure_view(r, run.id) is None
    finally:
        r.close()


def test_analysis_tab_does_not_prepare_hidden_result_table(tmp_path, monkeypatch):
    from research_env import analysis_ui
    from test_preparation import client
    env = m7_fixture.make_environment(tmp_path / 'instance')
    try:
        def unexpected(*args, **kwargs):
            raise AssertionError('Hidden per-run results must not delay the analysis form')
        monkeypatch.setattr(analysis_ui, 'runs', unexpected)
        response = client(env[0]).get('/analyses?view=analysis')
        assert response.status_code == 200
        assert 'Datenstand zur Prüfung vorbereiten' in response.text
    finally:
        env[0].close()


def test_analysis_and_history_do_not_rescan_other_runs(tmp_path, monkeypatch):
    env = m7_fixture.make_environment(tmp_path / 'instance')
    r, settings, store, _, freeze, _ = env
    try:
        run, comp, raw = m7_fixture.start(env, tmp_path)
        m7_fixture.measurement(env, run, comp, raw)
        m7_fixture.review(env, run, comp, raw, 'T1')
        service = Analyses(r, CallJournal.cost_view(r))
        with r.transaction():
            expected = service.snapshot(freeze.id)
        expected_history = selection_history(r, expected)
        original = r.all

        def bounded_all(cls):
            assert cls not in (MeasurementAttempt, CriterionReviewRevision, MetricObservation), \
                'Analysis must select the relevant run before loading and hashing payloads'
            return original(cls)

        monkeypatch.setattr(r, 'all', bounded_all)
        with r.transaction():
            actual = service.snapshot(freeze.id)
        assert actual == expected
        assert selection_history(r, actual) == expected_history
    finally:
        r.close()


def test_snapshot_rechecks_shared_evidence_on_next_snapshot(tmp_path, monkeypatch):
    env = m7_fixture.make_environment(tmp_path / 'instance')
    r, settings, store, _, freeze, _ = env
    try:
        run, comp, raw = m7_fixture.start(env, tmp_path)
        m7_fixture.measurement(env, run, comp, raw)
        m7_fixture.review(env, run, comp, raw, 'T1')
        service = Analyses(r, CallJournal.cost_view(r))
        reads = Counter()
        original = service.store.read

        def counted(identity):
            reads[identity] += 1
            return original(identity)

        monkeypatch.setattr(service.store, 'read', counted)
        with r.transaction():
            service.snapshot(freeze.id)
        assert reads[raw.id] == 1, 'Shared proof bytes must be verified once per snapshot'
        path = settings.artifacts / ArtifactStore.object_path(raw.sha256)
        path.chmod(0o600)
        path.write_bytes(b'corrupt synthetic proof')
        path.chmod(0o444)
        with pytest.raises(IntegrityError), r.transaction():
            service.snapshot(freeze.id)
    finally:
        r.close()
