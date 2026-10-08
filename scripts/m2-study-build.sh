#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
# This only builds the new image, starts no project and changes no M1 image.
docker build -f docker/study.Dockerfile -t research-env-study:m2-v0.1 .
