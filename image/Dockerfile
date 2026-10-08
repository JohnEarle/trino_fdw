# PostgreSQL + Multicorn2 + trino_fdw.
#
#   docker build -f image/Dockerfile -t trino_fdw:17 .
#
# BASE can be any Debian-based PostgreSQL 14-18 image that carries the PGDG apt
# repository (the official image, or a database operator's image); set RUN_USER
# to that image's postgres user or uid. Build context is the repository root.

# official image, pinned by digest (multi-arch index)
ARG BASE=postgres:17-bookworm@sha256:3645570cccdfa447589da9f57dd740faa29b30938e861289a5574b6ca6b03826
FROM ${BASE}

ARG PG_MAJOR=17
ARG MULTICORN_VERSION=3.2
ARG MULTICORN_SHA256=e31e8f6e25acafd0dc8a2faf90a8c1e29e114abe8383e5c1041f54ab0f9bb44f
ARG TRINO_CLIENT_VERSION=0.340.0
ARG WITH_KERBEROS=0
ARG RUN_USER=postgres

USER root
ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

RUN set -eux; \
    apt-get update; \
    # Debian security updates published since the base image was built
    apt-get dist-upgrade -y --no-install-recommends; \
    apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl \
        postgresql-server-dev-${PG_MAJOR} \
        python3 python3-dev python3-pip python3-setuptools; \
    if [ "${WITH_KERBEROS}" = "1" ]; then \
        apt-get install -y --no-install-recommends libkrb5-dev krb5-user; \
    fi; \
    curl -fsSL -o /tmp/multicorn.tar.gz \
        "https://github.com/pgsql-io/multicorn2/archive/refs/tags/v${MULTICORN_VERSION}.tar.gz"; \
    echo "${MULTICORN_SHA256}  /tmp/multicorn.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/multicorn.tar.gz -C /tmp; rm /tmp/multicorn.tar.gz; \
    cd "/tmp/multicorn2-${MULTICORN_VERSION}"; \
    make PYTHON_OVERRIDE=python3; \
    make install PYTHON_OVERRIDE=python3; \
    cd /; rm -rf "/tmp/multicorn2-${MULTICORN_VERSION}"; \
    pip3 install "trino==${TRINO_CLIENT_VERSION}"; \
    if [ "${WITH_KERBEROS}" = "1" ]; then pip3 install "trino[kerberos]==${TRINO_CLIENT_VERSION}"; fi

COPY pyproject.toml README.md LICENSE /tmp/trino_fdw/
COPY trino_fdw /tmp/trino_fdw/trino_fdw
RUN set -eux; \
    pip3 install /tmp/trino_fdw; \
    rm -rf /tmp/trino_fdw; \
    # multicorn.so links against libpythonX.Y; keep it when the -dev package goes
    pyver="$(python3 -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"; \
    apt-mark manual "libpython${pyver}" python3 python3-setuptools; \
    # pip is build-time only; Multicorn's installer upgrades it into /usr/local, remove that too
    python3 -m pip uninstall -y pip || true; \
    apt-get purge -y build-essential postgresql-server-dev-${PG_MAJOR} python3-dev python3-pip curl; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/* /root/.cache /usr/local/lib/python3*/dist-packages/pip*; \
    # the official image's gosu is a statically linked Go binary that carries
    # Go stdlib CVEs; util-linux setpriv does the same privilege drop. The shim
    # goes under /usr/local/sbin (earlier on PATH) so the deleted binary is a
    # plain whiteout rather than an overwrite.
    if [ -x /usr/local/bin/gosu ]; then \
        rm -f /usr/local/bin/gosu; \
        printf '%s\n' '#!/bin/sh' \
            '# gosu-compatible shim: gosu user[:group] command...' \
            'set -e' \
            'spec="$1"; shift' \
            'user="${spec%%:*}"; group="${spec#*:}"; [ "$group" = "$spec" ] && group=""' \
            'if [ -n "$group" ]; then exec setpriv --reuid="$user" --regid="$group" --clear-groups "$@"; fi' \
            'exec setpriv --reuid="$user" --regid="$user" --init-groups "$@"' \
            > /usr/local/sbin/gosu; \
        chmod 0755 /usr/local/sbin/gosu; \
    fi; \
    python3 -c "import trino_fdw, multicorn, trino; print('trino_fdw', trino_fdw.__version__)"

USER ${RUN_USER}

LABEL org.opencontainers.image.title="trino_fdw" \
      org.opencontainers.image.description="PostgreSQL with Multicorn2 and trino_fdw" \
      org.opencontainers.image.source="https://github.com/JohnEarle/trino_fdw" \
      org.opencontainers.image.licenses="Apache-2.0"
