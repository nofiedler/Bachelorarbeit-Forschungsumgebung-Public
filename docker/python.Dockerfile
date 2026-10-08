ARG RESEARCH_DEPENDENCY_IMAGE=dependencies
FROM python:3.13.16-slim-trixie@sha256:5434c2206183169a6c2b11d6156b775a02cce9a2fd00f9482bb8b9bb785e9b3f AS dependencies
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN python -m pip install --no-cache-dir --require-hashes --only-binary=:all: -r requirements.lock && python -m pip check
FROM ${RESEARCH_DEPENDENCY_IMAGE} AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/src PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
COPY requirements.lock ./requirements.lock
RUN python -c 'import importlib.metadata,re,pathlib; expected=re.findall(r"^([A-Za-z0-9_.-]+)==([^\s]+)",pathlib.Path("requirements.lock").read_text(),re.M); assert expected; mismatch=[(name,version,importlib.metadata.version(name)) for name,version in expected if importlib.metadata.version(name)!=version]; assert not mismatch,mismatch' && python -m pip check
COPY pyproject.toml compose.yaml compose.installed.yaml start.sh ./
COPY docker ./docker
COPY src ./src
COPY assets/context ./assets/context
COPY assets/study ./assets/study
COPY evaluation/development ./evaluation/development
COPY evaluation/study_holdout ./evaluation/study_holdout
COPY docs/vertraege ./docs/vertraege
COPY docker/entrypoint.sh /usr/local/bin/research-entrypoint
RUN mkdir -p /data/control /data/artifacts /data/checkpoints /data/staging /data/secrets /data/inspections && chown -R 10001:10001 /data
ENTRYPOINT ["research-entrypoint"]
CMD ["python", "-m", "research_env", "status"]
