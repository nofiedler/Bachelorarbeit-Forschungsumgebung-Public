# Trusted namespace guard only. No candidate source, keys, suite or Docker socket.
FROM python:3.13.16-slim-trixie@sha256:5434c2206183169a6c2b11d6156b775a02cce9a2fd00f9482bb8b9bb785e9b3f
RUN apt-get update && apt-get install --no-install-recommends -y iptables=1.8.11-2 util-linux=2.41.5-0+deb13u1 libxtables12=1.8.11-2 && rm -rf /var/lib/apt/lists/*
COPY docker/sandbox-guard.sh /usr/local/bin/sandbox-guard
ENTRYPOINT ["/bin/sh", "/usr/local/bin/sandbox-guard"]
