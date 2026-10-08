# Authentication

Every method takes its material from the database host's filesystem or
process environment. The wrapper refuses secrets passed as SQL options, so a
mistyped `CREATE USER MAPPING ... OPTIONS (password ...)` fails instead of
landing in the catalog.

## Password (LDAP or password file on Trino)

```sql
OPTIONS (auth 'password', user 'svc_fdw', password_file '/projected/trino/password')
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
OPTIONS (auth 'jwt', jwt_file '/projected/trino/token')
```

Any bearer token Trino's JWT authenticator accepts. Two zero-static-secret
setups:

* **Kubernetes service account token.** Add a projected `serviceAccountToken`
  source with `audience: trino` to the CNPG cluster's
  `projectedVolumeTemplate`, and point Trino's `http-server.authentication.jwt.key-file`
  at the cluster's JWKS URL. The token is rotated by the kubelet.
* **IdP client credentials.** A sidecar or CronJob refreshes a token file from
  Keycloak, Entra or Okta. The wrapper picks up the new file automatically.

## Client certificate (mTLS)

```sql
OPTIONS (auth 'certificate', cert_file '/projected/trino/tls.crt', key_file '/projected/trino/tls.key')
```

Issue the certificate with cert-manager and project it into the pod. The
certificate subject is the Trino principal.

## Kerberos

```sql
OPTIONS (auth 'kerberos', kerberos_service_name 'trino', kerberos_principal 'svc_fdw@EXAMPLE.COM')
```

Build the image with `WITH_KERBEROS=1`. Provide a keytab through the
`KRB5_CLIENT_KTNAME` environment variable on the cluster pods and a
`krb5.conf` via `kerberos_config` or `KRB5_CONFIG`.

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

* Project secrets with `defaultMode: 0400` and set `strict_file_permissions 'true'`.
* Pin the Trino CA with `ca_file`; never disable verification.
* The connecting PostgreSQL role is not a superuser; grant only `USAGE` on the
  server and `SELECT` on the foreign tables.
* On Trino, give the service principal `read-only` catalog rules scoped to the
  needed schema, and a query time and memory limit via resource groups.
* Only superusers can create a Multicorn server. Treat defining a wrapper as
  superuser-level access, because it runs Python inside the backend.
