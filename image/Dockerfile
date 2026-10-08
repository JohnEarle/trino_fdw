# PostgreSQL + Multicorn2 + trino_fdw.
#
# Production (CloudNativePG):
#   docker build -f image/Dockerfile -t registry/cnpg-trino-fdw:17 .
# Local dev stack (official postgres image, has an entrypoint):
#   docker build -f image/Dockerfile --build-arg BASE=postgres:17-bookworm -t trino-fdw-dev .
#
# Build context must be the repository root.

# Base pinned by digest (ghcr.io/cloudnative-pg/postgresql:17.11-standard-bookworm, multi-arch index).
ARG BASE=ghcr.io/cloudnative-pg/postgresql:17.11-standard-bookworm@sha256:49c1dd79092d7d8dad03d07151ef7157416e233576c639af8d60ec02c4ae297d
FROM ${BASE} AS build

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
    # pull Debian security updates published since the base image was built
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
    # pip is only needed at build time; Multicorn's installer upgrades it into
    # /usr/local, so remove that copy too (it vendors its own msgpack etc.)
    python3 -m pip uninstall -y pip || true; \
    apt-get purge -y build-essential postgresql-server-dev-${PG_MAJOR} python3-dev python3-pip curl; \
    apt-get autoremove -y; \
    rm -rf /var/lib/apt/lists/* /root/.cache /usr/local/lib/python3*/dist-packages/pip*; \
    # Debian's placeholder TLS key; PostgreSQL gets real certificates from the operator
    rm -f /etc/ssl/private/ssl-cert-snakeoil.key /etc/ssl/certs/ssl-cert-snakeoil.pem; \
    python3 -c "import trino_fdw, multicorn, trino; print('trino_fdw', trino_fdw.__version__)"

USER ${RUN_USER}

# ---------------------------------------------------------------------------
# dev: keeps the base image's entrypoint/config (used by dev/docker-compose.yaml
# with BASE=postgres:17-bookworm so PostgreSQL can run standalone).
FROM build AS dev

# ---------------------------------------------------------------------------
# release (default): the finished filesystem flattened into one layer, so
# files deleted during the build (Debian's placeholder TLS key, pip) are not
# left behind in lower layers. Config matches the CloudNativePG base image.
FROM scratch AS release
ARG PG_MAJOR=17
ARG RUN_USER=26
COPY --from=build / /
ENV PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/usr/lib/postgresql/${PG_MAJOR}/bin
USER ${RUN_USER}
CMD ["bash"]
LABEL org.opencontainers.image.title="trino_fdw PostgreSQL" \
      org.opencontainers.image.description="PostgreSQL with Multicorn2 and trino_fdw, built on the CloudNativePG standard image" \
      org.opencontainers.image.source="https://github.com/JohnEarle/trino_fdw" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.opencontainers.image.base.name="ghcr.io/cloudnative-pg/postgresql:17.11-standard-bookworm"
