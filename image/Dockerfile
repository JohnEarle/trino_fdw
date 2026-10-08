# PostgreSQL + Multicorn2 + trino_fdw.
#
# Production (CloudNativePG):
#   docker build -f image/Dockerfile -t registry/cnpg-trino-fdw:17 .
# Local dev stack (official postgres image, has an entrypoint):
#   docker build -f image/Dockerfile --build-arg BASE=postgres:17-bookworm -t trino-fdw-dev .
#
# Build context must be the repository root.

# Base pinned by digest (ghcr.io/cloudnative-pg/postgresql:17.6-standard-bookworm, multi-arch index).
ARG BASE=ghcr.io/cloudnative-pg/postgresql:17.6-standard-bookworm@sha256:a62c149a342b2b1955dda3a9d75e2c6851622e43302a577f393271c19a3c8622
FROM ${BASE}

ARG PG_MAJOR=17
ARG MULTICORN_VERSION=3.2
ARG MULTICORN_SHA256=e31e8f6e25acafd0dc8a2faf90a8c1e29e114abe8383e5c1041f54ab0f9bb44f
ARG TRINO_CLIENT_VERSION=0.340.0
ARG WITH_KERBEROS=0
# uid 26 is what the CloudNativePG operator expects; the official postgres
# image needs "postgres" (its entrypoint drops privileges itself).
ARG RUN_USER=26

USER root
ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_ROOT_USER_ACTION=ignore

RUN set -eux; \
    apt-get update; \
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
    apt-get purge -y build-essential postgresql-server-dev-${PG_MAJOR} python3-dev python3-pip curl; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/* /root/.cache; \
    python3 -c "import trino_fdw, multicorn, trino; print('trino_fdw', trino_fdw.__version__)"

USER ${RUN_USER}
