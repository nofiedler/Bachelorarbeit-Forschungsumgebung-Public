from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient
from research_env.config import Settings
from research_env.database import connect, migrate, save_worker_status, set_diagnostic_marker
from research_env.status import get_status
from research_env.web import create_app


@pytest.fixture
def settings(tmp_path):
    value = Settings(*(tmp_path / name for name in ("control", "artifacts", "checkpoints", "staging")))
    for path in (value.control, value.artifacts, value.checkpoints, value.staging):
        path.mkdir()
    migrate(value)
    return value


def test_migration_restart_preserves_identity_and_marker(settings):
    before = get_status(settings)["installation_id"]
    set_diagnostic_marker(settings, "M1-SYNTHETIC-PERSISTENCE-v1")
    migrate(settings)
    after = get_status(settings)
    assert after["installation_id"] == before
    assert after["synthetic_persistence_marker"] == "M1-SYNTHETIC-PERSISTENCE-v1"
    with closing(connect(settings)) as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "delete"
        assert conn.execute("PRAGMA synchronous").fetchone()[0] == 2
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"


def test_migration_tampering_and_downgrade_are_rejected(settings, tmp_path):
    with closing(connect(settings)) as conn:
        conn.execute("UPDATE schema_migrations SET sha256='tampered'")
    with pytest.raises(RuntimeError, match="Veränderte"):
        migrate(settings)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(RuntimeError, match="neuer als Software"):
        migrate(settings, empty)


def test_failed_migration_rolls_back_all_changes(settings, tmp_path):
    from research_env.database import MIGRATIONS
    other = tmp_path / "migrations"
    other.mkdir()
    for path in MIGRATIONS.glob("*.sql"):
        (other / path.name).write_bytes(path.read_bytes())
    (other / "002_failure.sql").write_text("CREATE TABLE must_not_survive (id INTEGER);\nINVALID SQL;\n")
    with pytest.raises(sqlite3.Error):
        migrate(settings, other)
    with closing(connect(settings)) as conn:
        assert conn.execute("SELECT name FROM sqlite_master WHERE name='must_not_survive'").fetchall() == []
        assert conn.execute("SELECT count(*) FROM schema_migrations").fetchone()[0] == len(list(MIGRATIONS.glob('*.sql')))


def test_worker_missing_stale_stopped_and_recovered(settings):
    assert next(c for c in get_status(settings)["components"] if c["name"] == "Worker")["state"] == "missing"
    save_worker_status(settings, [{"name": "Docker", "state": "missing", "detail": "Daemon fehlt."}])
    assert next(c for c in get_status(settings)["components"] if c["name"] == "Worker")["state"] == "ok"
    with closing(connect(settings)) as conn:
        conn.execute("UPDATE worker_status SET observed_at=?", (time.time() - 60,))
    stale = get_status(settings)
    assert next(c for c in stale['components'] if c['name']=='Worker')['state']=='unknown'
    assert not any(c['name']=='Docker' for c in stale['components'])
    with TestClient(create_app(settings)) as client:
        html=client.get('/').text
        assert 'Längere Vorbereitung' in html and 'Worker starten' not in html
    save_worker_status(settings, [], state="stopped")
    assert next(c for c in get_status(settings)["components"] if c["name"] == "Worker")["state"] == "missing"
    save_worker_status(settings, [])
    assert next(c for c in get_status(settings)["components"] if c["name"] == "Worker")["state"] == "ok"


def test_read_only_browser_routes_do_not_mutate_database_or_expose_keys(settings, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "M1-SYNTHETIC-SECRET-DO-NOT-DISPLAY")
    before = hashlib.sha256(settings.database.read_bytes()).hexdigest()
    with TestClient(create_app(settings)) as client:
        for route in ("/", "/status", "/api/status", "/healthz", "/static/vendor/htmx.min.js"):
            response = client.get(route)
            assert response.status_code == 200
            assert "M1-SYNTHETIC-SECRET-DO-NOT-DISPLAY" not in response.text
        status = client.get("/api/status").json()
        assert status["real_calls"] == 0
        assert status["model_access"] == "worker_only"
        assert client.post("/").status_code == 405
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
        assert "script-src 'self'" in client.get("/").headers["Content-Security-Policy"]
    assert hashlib.sha256(settings.database.read_bytes()).hexdigest() == before


def test_missing_storage_and_database_are_explained(settings):
    settings.staging.rmdir()
    settings.database.unlink()
    with TestClient(create_app(settings)) as client:
        assert client.get("/healthz").status_code == 503
        status = client.get("/api/status").json()
        assert any(c["name"] == "Staging" and c["state"] == "missing" for c in status["components"])
        assert any(c["name"] == "SQLite" and c["state"] == "error" for c in status["components"])
        assert "Daten nicht löschen" in client.get("/").text


def test_local_assets_and_image_catalogue_match():
    root = Path(__file__).parents[1]
    package = root / "src/research_env"
    assert (package / "images.lock.json").read_bytes() == (root / "docker/images.lock.json").read_bytes()
    images = json.loads((package / "images.lock.json").read_text())["images"]
    for image in images.values():
        assert "@sha256:" in image["reference"]
        assert set(image["platforms"]) == {"arm64", "amd64"}
    assets = json.loads((root / "docker/assets.lock.json").read_text())
    for relative, digest in assets["files"].items():
        assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == digest


def test_docker_unavailable_is_explained_without_raw_exception(monkeypatch):
    import docker
    from research_env.sandbox import docker_components
    def unavailable(**kwargs):
        assert kwargs["timeout"] is None
        raise docker.errors.DockerException("secret must not leak")
    monkeypatch.setattr(docker, "from_env", unavailable)
    components = docker_components()
    assert components[0]["state"] == "missing"
    assert "Docker Desktop" in components[0]["detail"]
    assert "secret" not in json.dumps(components)


def test_migration_failure_cannot_report_healthy_even_with_readable_metadata(settings):
    from dataclasses import replace
    with TestClient(create_app(replace(settings, migration_failed=True))) as client:
        assert client.get("/").status_code == 200
        assert client.get("/healthz").status_code == 503
        assert not client.get("/api/status").json()["database_ok"]
        assert "Diagnosemodus: Workerstart gesperrt" in client.get("/").text


@pytest.mark.parametrize("script", ["verify-runtime.py", "verify-startup-failure.py"])
def test_existing_report_directory_is_rejected_without_overwriting(tmp_path, script):
    import subprocess
    import sys
    old_evidence = tmp_path / "failed.log"
    old_evidence.write_text("Preserve original failure\n")
    result = subprocess.run([sys.executable, str(Path(__file__).parents[1] / "scripts" / script),
                             "--output", str(tmp_path)], capture_output=True, text=True)
    assert result.returncode == 2
    assert "Belegordner existiert" in result.stderr
    assert list(tmp_path.iterdir()) == [old_evidence]
    assert old_evidence.read_text() == "Preserve original failure\n"


def test_docker_sdk_happy_path_closes_client_without_context_manager(monkeypatch):
    import docker
    from research_env.sandbox import docker_components, internal_images
    calls = []
    class Images:
        def get(self, reference):
            calls.append(reference)
            return object()
    class Client:
        images = Images()
        # DockerClient exposes close(), but no context-manager protocol.
        def ping(self):
            calls.append("ping")
            return True
        def close(self):
            calls.append("close")
    monkeypatch.setattr(docker, "from_env", lambda **kwargs: Client())
    components = docker_components()
    assert calls == ["ping", *internal_images().values(), "close"]
    assert all(component["state"] == "ok" for component in components)
