# Existing locked Python dependencies. Root verifies this own local alias against
# the exact 2ffd... image ID before AND after build; no mutable upstream image.
# BuildKit cannot use a bare local sha256 image ID in FROM.
ARG BASE_RUNTIME=research-env-m4-python-base:2ffd3236561b05e7ee97ee23ae69671f9ea5062aedcb6bbce73190dac59e97cc
FROM ${BASE_RUNTIME}
COPY src /app/src
ENTRYPOINT []
CMD ["python", "-m", "research_env.sandbox_client"]
