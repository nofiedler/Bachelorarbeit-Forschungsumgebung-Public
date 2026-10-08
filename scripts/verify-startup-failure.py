#!/usr/bin/env python3
"""M1-v2 real entrypoint probes with synthetic, disposable control volumes."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import platform
from datetime import datetime, timezone
import time
import urllib.error
import urllib.request
import uuid

parser = argparse.ArgumentParser()
parser.add_argument("--output", required=True, type=Path)
parser.add_argument("--image", default="research-env-python:0.1.0")
args = parser.parse_args()
try:
    args.output.mkdir(parents=True, exist_ok=False)
except FileExistsError:
    parser.error("Belegordner existiert; einen neuen --output-Pfad wählen.")
results = []
identifier = uuid.uuid4().hex[:12]


def run(label, command):
    completed = subprocess.run(command, capture_output=True, text=True)
    (args.output / (label + ".log")).write_text(completed.stdout + completed.stderr)
    results.append({"id": label, "command": command, "exit": completed.returncode})
    if completed.returncode:
        raise RuntimeError(label + ": " + completed.stderr)
    return completed.stdout.strip()


def http(url):
    try:
        with urllib.request.urlopen(url) as response:
            return response.status, response.read().decode()
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode()


try:
    commit = run("software-commit", ["git", "rev-parse", "HEAD"])
    changes = run("software-changes", ["git", "status", "--short"])
    image_id = run("image-id", ["docker", "image", "inspect", args.image, "--format", "{{.Id}}"])
    for case in ("corrupt", "readonly"):
        name = f"research-env-m1-{case}-{identifier}"
        volume = name + "-control"
        run(case + "-volume", ["docker", "volume", "create", "--label", "org.bachelorarbeit.scope=m1", volume])
        try:
            if case == "corrupt":
                run(case + "-prepare", ["docker", "run", "--rm", "--name", name + "-prepare", "--label", "org.bachelorarbeit.scope=m1",
                    "--network", "none", "--entrypoint", "python", "-v", volume + ":/data/control", args.image, "-c",
                    "import pathlib,os; p=pathlib.Path('/data/control/control.sqlite3'); p.write_bytes(b'M1 CORRUPT SYNTHETIC DATABASE\\n'); os.chmod(p,0o666)"])
            run(case + "-start", ["docker", "run", "-d", "--name", name, "--label", "org.bachelorarbeit.scope=m1",
                "--read-only", "--tmpfs", "/tmp", "--user", "10001:10001", "--cap-drop", "ALL", "--security-opt", "no-new-privileges:true",
                "-e", "RESEARCH_COMPONENT=web", "-v", volume + ":/data/control" + (":ro" if case == "readonly" else ""),
                "-p", "127.0.0.1::8000", args.image, "python", "-m", "uvicorn", "research_env.web:app", "--host", "0.0.0.0", "--port", "8000"])
            address = "http://" + run(case + "-port", ["docker", "port", name, "8000/tcp"])
            for _ in range(60):
                try:
                    code, body = http(address + "/healthz")
                    break
                except OSError:
                    time.sleep(1)
            else:
                raise AssertionError("Diagnostic web not reachable in 60 observation probes")
            assert code == 503, code
            index_code, index = http(address + "/")
            assert index_code == 200 and "Diagnosemodus: Workerstart gesperrt" in index
            status_code, status = http(address + "/api/status")
            assert status_code == 200 and json.loads(status)["database_ok"] is False
            (args.output / (case + "-status.json")).write_text(status)
            (args.output / (case + "-page.html")).write_text(index)
            if case == "corrupt":
                digest = run(case + "-preserved", ["docker", "exec", name, "python", "-c",
                    "import pathlib,hashlib; print(hashlib.sha256(pathlib.Path('/data/control/control.sqlite3').read_bytes()).hexdigest())"])
                assert digest == hashlib.sha256(b"M1 CORRUPT SYNTHETIC DATABASE\n").hexdigest()
            run(case + "-logs", ["docker", "logs", name])
            results.append({"id": case + "-assertions", "state": "passed", "health_status": code, "page_status": index_code})
        finally:
            # Only our known probe container; retain the labelled fixture volume
            # and archived raw output for review instead of deleting any data.
            subprocess.run(["docker", "stop", name], capture_output=True)
            subprocess.run(["docker", "rm", name], capture_output=True)
except BaseException as error:
    results.append({"id": "execution", "state": "failed", "error": str(error)})
    raise
finally:
    (args.output / "results.json").write_text(json.dumps({"version": "M1-v2", "scope": "entrypoint failure handling",
        "image": args.image, "image_id": locals().get("image_id"), "commit": locals().get("commit"),
        "software_changes": locals().get("changes"), "platform": platform.platform(),
        "examiner": "Codex implementation agent; executed by coordinator", "completed_at": datetime.now(timezone.utc).isoformat(),
        "input_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in [Path(__file__),Path("docker/entrypoint.sh"),Path("docker/images.lock.json")]},
        "results": results}, indent=2, ensure_ascii=False) + "\n")
