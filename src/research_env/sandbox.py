"""Trusted, internal runtime catalogue. No arbitrary job/mount/command interface.

M1 probes remain read-only. M4 lifecycle accepts register identities and fixed
trusted image/asset configuration; no user Docker/mount/command surface.
"""
from contextlib import closing
import json
from pathlib import Path

IMAGE_LOCK = Path(__file__).with_name("pipeline_runtime.lock.json")


def internal_images():
    lock = json.loads(IMAGE_LOCK.read_text())
    return {
        "Laravel / PHP": lock['php'],
        "MySQL": lock['mysql'],
        "Prüfclient": lock['client'],
        "Netzwerkisolation": lock['guard'],
    }


def docker_components():
    import docker
    from docker.errors import DockerException, ImageNotFound

    try:
        # A daemon probe does not impose a runtime limit on future research jobs.
        with closing(docker.from_env(timeout=None)) as client:
            client.ping()
            result = [{"name": "Docker", "state": "ok", "detail": "Daemon erreichbar."}]
            for label, reference in internal_images().items():
                try:
                    client.images.get(reference)
                    result.append({"name": label, "state": "ok", "detail": "Vorbereitetes Image vorhanden."})
                except ImageNotFound:
                    result.append({"name": label, "state": "missing", "detail":
                                   "Image fehlt. Vorbereitungsbefehle in der README ausführen; Basisstatus bleibt nutzbar."})
            return result
    except (DockerException, OSError):
        return [{"name": "Docker", "state": "missing", "detail":
                 "Docker-Daemon für Worker nicht erreichbar. Docker Desktop und Socket-Mount prüfen, danach Worker neu starten."}]


# Trusted worker integration surface; web status never instantiates this service.
from .sandbox_runtime import RuntimeImages, Sandbox
