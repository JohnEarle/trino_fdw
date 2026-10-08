# trino_fdw

A read-only PostgreSQL foreign data wrapper for [Trino](https://trino.io),
built on [Multicorn2](https://github.com/pgsql-io/multicorn2).

It lets anything that speaks PostgreSQL query Trino tables live, with
`WHERE`, `ORDER BY` and `LIMIT` pushed down to Trino. Typical uses are
tools that support PostgreSQL but not Trino (BI connectors, log and data
collectors, legacy reporting), and joining Trino data with local tables.

## Security model

* **No secret is ever accepted as a SQL option.** `password`, `token`, `key`
  and friends are refused both by the wrapper at runtime and by an event
  trigger at DDL time ([sql/20_guard.sql](sql/20_guard.sql)). Credentials are
  read from files on the database host, which on CloudNativePG are projected
  Kubernetes Secrets. Nothing sensitive reaches the catalog, backups, dumps
  or `log_statement` output.
* **TLS is mandatory.** Plain HTTP and `verify=false` are rejected. Supply
  the issuing CA with `ca_file`.
* **All Trino authentication methods** are supported: password (LDAP or file),
  JWT, client certificate and Kerberos. See [docs/auth.md](docs/auth.md).
* **One-to-one identity via impersonation.** The wrapper authenticates as one
  service principal and sets `X-Trino-User` from the user mapping option
  `trino_user`, so Trino enforces per-user permissions and audits the real
  user without per-user secrets.

## Quick start

```sql
CREATE EXTENSION multicorn;
\i sql/20_guard.sql

CREATE SERVER trino FOREIGN DATA WRAPPER multicorn OPTIONS (
    wrapper 'trino_fdw.TrinoFDW',
    host 'trino.example.internal', port '8443',
    catalog 'hive', schema 'prod',
    auth 'password', user 'svc_fdw',
    password_file '/projected/trino/password',
    ca_file '/projected/trino/ca.pem'
);
CREATE USER MAPPING FOR reader SERVER trino OPTIONS (trino_user 'reader');
GRANT USAGE ON FOREIGN SERVER trino TO reader;

IMPORT FOREIGN SCHEMA prod LIMIT TO (audit_events) FROM SERVER trino INTO public;
SELECT * FROM audit_events WHERE id > 100 ORDER BY id;
```

## Options

Server (and user mapping) options:

| Option | Default | Notes |
|---|---|---|
| `host` | required | Trino coordinator hostname |
| `port` | `443` | HTTPS port |
| `catalog`, `schema` | | Defaults for unqualified table names and `IMPORT FOREIGN SCHEMA` |
| `auth` | `password` | `password`, `jwt`, `certificate` or `kerberos` |
| `user` | | Authenticating principal for `password` auth |
| `trino_user` | `user` | Identity queries run as (impersonation). Set it per role in the user mapping |
| `password_file`, `jwt_file`, `cert_file`, `key_file` | | Credential material on the database host |
| `ca_file` | system CAs | PEM bundle used to verify the Trino certificate |
| `strict_file_permissions` | `false` | Refuse world-readable secret files instead of warning |
| `request_timeout` | `60` | Seconds per HTTP request |
| `source`, `client_tags` | `trino_fdw` | Shown in Trino's query log |
| `estimated_rows`, `estimated_row_width` | `100000`, `200` | Planner hints |
| `fetch_size` | `1000` | Rows pulled per batch |
| `max_connection_age` | `600` | Seconds before a cached Trino connection is re-opened, re-reading the credential file. An authentication failure also forces a re-open, so a rotated secret is picked up either way |
| `kerberos_service_name`, `kerberos_principal`, `kerberos_config` | | Kerberos only |

Foreign table options: `table` (optionally `schema.table` or
`catalog.schema.table`), or `query` for a raw `SELECT`; `schema` and
`catalog` override the server defaults.

## Pushdown

Equality, comparison, `LIKE`, `IS [NOT] NULL` and `= ANY(...)` quals are sent
to Trino as bound parameters, typed from the foreign column's declared type
(Multicorn passes bigint, boolean, float and timestamptz constants as text;
`trino_fdw.types.coerce_param` turns them back into typed values).

What is and is not pushed, by PostgreSQL column type:

| Column type | Pushed as |
|---|---|
| integers, real/double, boolean, text/varchar/char, date, timestamp, timestamptz, time, bytea | typed Trino literal |
| `uuid` | `UUID '...'` literal (value must parse as a UUID) |
| `inet` | `CAST(? AS ipaddress)`; values with a `/` prefix stay local |
| `numeric` | `DECIMAL` literal when the constant has at most 15 significant digits. Multicorn turns numeric constants into floats first, so longer values would be inexact and are evaluated by PostgreSQL instead |
| `json`/`jsonb`, `interval`, `time with time zone`, `cidr`, `macaddr`, arrays | not pushed; PostgreSQL filters the rows |

A qual that is not pushed is never wrong, only slower: PostgreSQL re-checks every
qual on the rows the wrapper returns. `ORDER BY` on plain columns and `LIMIT`/`OFFSET`
are pushed when PostgreSQL offers them. Anything else is evaluated by
PostgreSQL after fetch, which is always correct because PostgreSQL re-checks
every qual on returned rows. `EXPLAIN VERBOSE` shows the Trino SQL.

## Why this repository ships a Dockerfile

A Multicorn wrapper cannot be installed into a running PostgreSQL the way a
Python package is installed into an application. Multicorn is a C extension
compiled against the server headers, and the wrapper and the Trino client
have to live in the same Python the server embeds. On CloudNativePG that means
an image. The image is therefore the distribution format of this component,
the same way `mysql_fdw` ships `.deb` and `.rpm` packages. It contains nothing
but a pinned PostgreSQL base, Multicorn, the Trino client and this package.

## Building the image

```bash
docker build -f image/Dockerfile -t registry.example/trino_fdw:17 .
```

The base image is pinned by digest and the Multicorn tarball is verified by
SHA-256. Pass `--build-arg WITH_KERBEROS=1` to include the Kerberos client
libraries. Tagged releases (`v*`) build a multi-arch image, scan it with Trivy,
attach an SBOM and provenance, and publish it as
`ghcr.io/<owner>/trino_fdw:17-v<version>` and `ghcr.io/<owner>/trino_fdw:17`.
Example CloudNativePG manifests are in [deploy/](deploy/); real cluster
values belong in your own infrastructure repository.

## Development

```bash
make test          # unit tests, no PostgreSQL needed
make integration   # docker compose: Trino (TLS + password auth) and PostgreSQL
```

## License

Apache 2.0.
