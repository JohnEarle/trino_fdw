# Authentication

Every method takes its material from the database host's filesystem or
process environment. The wrapper refuses secrets passed as SQL options, so a
mistyped `CREATE USER MAPPING ... OPTIONS (password ...)` fails instead of
landing in the catalog.

## Password (LDAP or password file on Trino)

```sql
OPTIONS (auth 'password', user 'svc_fdw', password_file '/etc/trino_fdw/password')
```

Trino only accepts password authentication over TLS, and the wrapper only
speaks HTTPS. World-readable files produce a warning, or an error with
`strict_file_permissions 'true'`.

### Rotation

The file is read each time a connection is opened. Connections are cached per
backend and re-opened after `max_connection_age` seconds (default 600). If
Trino rejects a cached connection because the password changed underneath
it, the wrapper discards the connection, re-reads the file and retries the
query once. So a rotation is: update the password on Trino, update the
Secret, done. The integration test exercises exactly this on a live session.

## JWT

```sql
OPTIONS (auth 'jwt', jwt_file '/etc/trino_fdw/token')
```

Any bearer token Trino's JWT authenticator accepts. Two zero-static-secret
setups:

* **Platform workload identity.** If your platform can mount a short-lived
  signed token for the database host (a Kubernetes service account token, a
  cloud instance identity token), point Trino's
  `http-server.authentication.jwt.key-file` at that issuer's JWKS URL. The
  platform rotates the token; the wrapper reads the file on each connection.
* **IdP client credentials.** A sidecar or scheduled job refreshes a token
  file from Keycloak, Entra or Okta. The wrapper picks up the new file
  automatically.

## Client certificate (mTLS)

```sql
OPTIONS (auth 'certificate', cert_file '/etc/trino_fdw/tls.crt', key_file '/etc/trino_fdw/tls.key')
```

Issue the certificate from your PKI and mount it on the database host. The
certificate subject is the Trino principal.

## Kerberos

```sql
OPTIONS (auth 'kerberos', kerberos_service_name 'trino', kerberos_principal 'svc_fdw@EXAMPLE.COM')
```

Install the `trino_fdw[kerberos]` extra (or build the image with
`WITH_KERBEROS=1`). Provide a keytab through the `KRB5_CLIENT_KTNAME`
environment variable of the PostgreSQL server process and a `krb5.conf` via
`kerberos_config` or `KRB5_CONFIG`.

## Impersonation (one-to-one identity)

With any method above the wrapper authenticates as one service principal.
The user mapping option `trino_user` sets the `X-Trino-User` header, so the
query runs as that user once Trino's impersonation rules allow it:

```json
{ "impersonation": [ { "original_user": "svc_fdw", "new_user": "reader|analyst" } ] }
```

Trino's query log then records `reader`, and its table rules apply to
`reader`. No per-user secret exists anywhere. Without `trino_user` queries run
as the principal itself.

## Hardening checklist

* Mount secret files with mode 0400 and set `strict_file_permissions 'true'`.
* Pin the Trino CA with `ca_file`; never disable verification.
* The connecting PostgreSQL role is not a superuser; grant only `USAGE` on the
  server and `SELECT` on the foreign tables.
* On Trino, give the service principal `read-only` catalog rules scoped to the
  needed schema, and a query time and memory limit via resource groups.
* Only superusers can create a Multicorn server. Treat defining a wrapper as
  superuser-level access, because it runs Python inside the backend.
