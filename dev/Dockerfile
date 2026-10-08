# Test fixture for dev/docker-compose.yaml: PostgreSQL with Multicorn2 and
# this package installed. Not a distributable image; install trino_fdw into
# your own PostgreSQL as described in the README.
FROM postgres:17-bookworm

ARG PG_MAJOR=17
ARG MULTICORN_VERSION=3.2
ARG MULTICORN_SHA256=e31e8f6e25acafd0dc8a2faf90a8c1e29e114abe8383e5c1041f54ab0f9bb44f

ENV PIP_BREAK_SYSTEM_PACKAGES=1 PIP_NO_CACHE_DIR=1 PIP_ROOT_USER_ACTION=ignore

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl postgresql-server-dev-${PG_MAJOR} \
        python3 python3-dev python3-pip python3-setuptools; \
    curl -fsSL -o /tmp/multicorn.tar.gz \
        "https://github.com/pgsql-io/multicorn2/archive/refs/tags/v${MULTICORN_VERSION}.tar.gz"; \
    echo "${MULTICORN_SHA256}  /tmp/multicorn.tar.gz" | sha256sum -c -; \
    tar -xzf /tmp/multicorn.tar.gz -C /tmp; \
    cd "/tmp/multicorn2-${MULTICORN_VERSION}"; \
    make PYTHON_OVERRIDE=python3; make install PYTHON_OVERRIDE=python3; \
    rm -rf /tmp/multicorn* /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE /tmp/trino_fdw/
COPY trino_fdw /tmp/trino_fdw/trino_fdw
RUN pip3 install /tmp/trino_fdw && rm -rf /tmp/trino_fdw

USER postgres
