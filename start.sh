#!/bin/sh
# Complete local installation. Requires only Docker/Compose and a POSIX shell.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
docker info >/dev/null
docker compose version >/dev/null
mkdir -p local
docker build --target dependencies -f docker/python.Dockerfile -t research-env-installed-dependencies:prepared .
docker build --build-arg BASE_RUNTIME=research-env-installed-dependencies:prepared -f docker/sandbox-client.Dockerfile -t research-env-installed-client:prepared .
docker build -f docker/sandbox-guard.Dockerfile -t research-env-installed-guard:prepared .
docker build -f docker/study.Dockerfile -t research-env-installed-php:prepared .
research_mysql='mysql:8.4.11-oraclelinux9@sha256:6ea90827b1100f8f2ae306a539f86d2c264a26ed435a2a9f75551dd5c3aeb242'
docker image inspect "$research_mysql" >/dev/null 2>&1 || docker pull "$research_mysql"
research_php=$(docker image inspect research-env-installed-php:prepared --format '{{.Id}}')
research_client=$(docker image inspect research-env-installed-client:prepared --format '{{.Id}}')
research_guard=$(docker image inspect research-env-installed-guard:prepared --format '{{.Id}}')
printf '{"php":"%s","mysql":"%s","client":"%s","guard":"%s"}\n' "$research_php" "$research_mysql" "$research_client" "$research_guard" > local/runtime-images.json.new
mv local/runtime-images.json.new local/runtime-images.json
docker compose -f compose.yaml -f compose.installed.yaml up --build -d --wait
printf '\nForschungsumgebung bereit: http://127.0.0.1:%s\n' "${RESEARCH_PORT:-8000}"
printf 'Noch keine Modellaufrufe gestartet. Grundlagen anschließend unter Einstellungen vorbereiten.\n'
