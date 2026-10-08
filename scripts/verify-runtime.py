#!/usr/bin/env python3
"""M1-v1: real Compose smoke/persistence/absence probes; no model calls.

Run on the host from a fresh checked-out commit after building the images.
Uses a separate Compose project and preserves its named volumes after testing.
It stops only its own services. No docker down -v / global cleanup is used.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import time
import urllib.request
import uuid

parser = argparse.ArgumentParser()
parser.add_argument("--output", type=Path, required=True)
parser.add_argument("--project", default="research-env-m1-check-" + uuid.uuid4().hex[:12])
args = parser.parse_args()
try:
    args.output.mkdir(parents=True, exist_ok=False)
except FileExistsError:
    parser.error("Belegordner existiert bereits; neuen --output-Pfad verwenden. Frühere Belege bleiben unverändert.")
compose = ["docker", "compose", "-p", args.project]
results = []


def command(identifier, arguments, *, expected=0):
    result = subprocess.run(arguments, capture_output=True, text=True)
    (args.output / (identifier + ".log")).write_text(result.stdout + result.stderr)
    results.append({"id": identifier, "command": arguments, "expected_exit": expected,
                    "actual_exit": result.returncode, "state": "passed" if result.returncode == expected else "failed"})
    if result.returncode != expected:
        raise RuntimeError(f"{identifier}: {result.stderr}")
    return result.stdout


def status():
    with urllib.request.urlopen("http://127.0.0.1:8000/api/status") as response:
        return json.load(response)


def save_status(identifier):
    data = status()
    (args.output / (identifier + ".json")).write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return data


def worker_ready(data):
    return any(c["name"] == "Worker" and c["state"] == "ok" for c in data["components"])


def wait_ready():
    # This test observation window never cancels research work. M1 has no jobs.
    for _ in range(60):
        try:
            data = status()
            if worker_ready(data):
                return data
        except (OSError, ValueError):
            pass
        time.sleep(1)
    raise AssertionError("Worker readiness not observed in 60 diagnostic probes")


try:
    existing = command("fresh-volumes", ["docker", "volume", "ls", "--filter", "label=com.docker.compose.project=" + args.project, "--format", "{{.Name}}"] )
    if existing.strip():
        raise RuntimeError("Compose-Projekt hat bereits Volumes; neue eindeutige Projektkennung für den frischen Nachweis wählen.")
    commit = command("software-commit", ["git", "rev-parse", "HEAD"]).strip()
    changes = command("software-changes", ["git", "status", "--short"])
    command("docker-version", ["docker", "version"])
    command("compose-version", ["docker", "compose", "version"])
    command("compose-config", compose + ["config"])
    command("start", compose + ["up", "--build", "-d"])
    wait_ready()
    before = save_status("before")
    assert any(c["name"] == "Docker" and c["state"] == "ok" for c in before["components"]), "Worker hat keinen Dockerzugriff"
    marker = "M1-SYNTHETIC-" + str(uuid.uuid4())
    command("marker-write", compose + ["exec", "-T", "web", "python", "-m", "research_env", "synthetic-marker", marker])
    command("worker-marker-write", compose + ["exec", "-T", "worker", "python", "-m", "research_env", "synthetic-marker", marker])
    command("worker-permissions-and-socket", compose + ["exec", "-T", "worker", "python", "-c",
        "import os,json,pathlib,docker; "
        "paths=['/data/control','/data/control/control.sqlite3','/var/run/docker.sock']; "
        "print(json.dumps({'uid':os.getuid(),'gid':os.getgid(),'groups':os.getgroups(),'paths':{p:{'uid':os.stat(p).st_uid,'gid':os.stat(p).st_gid,'mode':oct(os.stat(p).st_mode),'writable':os.access(p,os.W_OK)} for p in paths}})); "
        "assert os.getuid()==10001; assert all(os.access(p,os.W_OK) for p in paths); "
        "client=docker.from_env(timeout=None); assert client.ping(); client.close(); print('Docker ping: ok')"])
    assert save_status("shared-control-write")["synthetic_persistence_marker"] == marker
    results.append({"id": "shared-control-and-socket", "state": "passed", "marker": marker})
    command("runtime-inventory", compose + ["exec", "-T", "web", "python", "-m", "research_env", "status"])
    command("installed-python", compose + ["exec", "-T", "web", "python", "-m", "pip", "freeze", "--all"])
    command("system-packages", compose + ["exec", "-T", "web", "dpkg-query", "-W"])
    web = command("web-id", compose + ["ps", "-q", "web"]).strip()
    worker = command("worker-id", compose + ["ps", "-q", "worker"]).strip()
    inspection = json.loads(command("container-inspection", ["docker", "inspect", web, worker]))
    web_info, worker_info = inspection
    assert web_info["NetworkSettings"]["Ports"]["8000/tcp"][0]["HostIp"] == "127.0.0.1"
    assert not any(m["Destination"] in ("/var/run/docker.sock", "/data/checkpoints") for m in web_info["Mounts"])
    assert any(m["Destination"] == "/data/artifacts" and not m["RW"] for m in web_info["Mounts"])
    assert any(m["Destination"] == "/var/run/docker.sock" for m in worker_info["Mounts"])
    assert not any("OPENROUTER" in value for container in inspection for value in container["Config"]["Env"])
    results.append({"id": "isolation-mounts", "state": "passed", "scope": "compose boundaries only; full M4 isolation untested"})
    command("stop", compose + ["stop"])
    command("restart", compose + ["up", "-d"])
    wait_ready()
    after = save_status("after-restart")
    assert before["installation_id"] == after["installation_id"]
    assert after["synthetic_persistence_marker"] == marker
    assert after["real_calls"] == 0
    results.append({"id": "persistence-restart", "state": "passed", "marker": marker})
    command("remove-containers-preserve-volumes", compose + ["down"])
    command("recreate-containers", compose + ["up", "-d"])
    wait_ready()
    recreated = save_status("after-recreate")
    assert recreated["installation_id"] == before["installation_id"]
    assert recreated["synthetic_persistence_marker"] == marker
    results.append({"id": "persistence-recreate", "state": "passed", "marker": marker})
    command("worker-stop", compose + ["stop", "worker"])
    missing = save_status("worker-missing")
    assert not worker_ready(missing)
    results.append({"id": "worker-absence", "state": "passed"})
    command("worker-recover", compose + ["up", "-d", "worker"])
    wait_ready()
    save_status("worker-recovered")
    command("service-logs", compose + ["logs", "--no-color"])
except BaseException as error:
    results.append({"id": "execution", "state": "failed", "detail": str(error)})
    raise
finally:
    report = {"detail_version": "M1-v2", "technical_examiner": "Codex implementation agent; commands executed by coordinator",
              "host": platform.platform(), "architecture": platform.machine(), "completed_unix": time.time(),
              "software_commit": locals().get("commit"), "software_changes": locals().get("changes"),
              "results": results, "scope": "M1 only, not full G01 or S1",
              "input_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                               for p in [Path("compose.yaml"), Path("requirements.lock"), Path("docker/images.lock.json"),
                                         Path("docker/assets.lock.json"), Path("scripts/verify-runtime.py")]}}
    (args.output / "results.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
