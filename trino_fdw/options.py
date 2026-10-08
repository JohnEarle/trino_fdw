"""Option parsing and validation.

Multicorn hands the wrapper one flat dictionary that merges the foreign
table options, the foreign server options and the current user's user
mapping options.  This module turns that dictionary into typed config
objects and enforces the one rule that matters most for security:

    No secret is ever accepted as a SQL option.

Credentials are read from files on the database host (``password_file``,
``jwt_file``, ``key_file``, ...).  Those files are expected to be mounted
from a secret store.  A ``password`` option,
or anything that looks like one, raises an error before any connection is
attempted, so the secret never reaches the catalog, a backup or a dump.
The SQL guard in ``sql/20_guard.sql`` enforces the same rule at DDL time.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from typing import Dict, Iterable, Optional


class OptionError(ValueError):
    """Raised for an invalid, missing or forbidden option."""


#: Option names that are refused everywhere (server, table, user mapping).
#: Matching is case-insensitive on the exact name.
FORBIDDEN_OPTION_NAMES = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "pass",
        "token",
        "jwt",
        "bearer",
        "secret",
        "client_secret",
        "api_key",
        "apikey",
        "private_key",
        "key",
        "keytab",
    }
)

AUTH_METHODS = ("password", "jwt", "certificate", "kerberos")

#: Options understood at server / user-mapping level.
SERVER_OPTION_NAMES = frozenset(
    {
        "host",
        "port",
        "catalog",
        "schema",
        "auth",
        "user",
        "trino_user",
        "password_file",
        "jwt_file",
        "cert_file",
        "key_file",
        "ca_file",
        "verify",
        "request_timeout",
        "source",
        "client_tags",
        "estimated_rows",
        "estimated_row_width",
        "strict_file_permissions",
        "kerberos_service_name",
        "kerberos_principal",
        "kerberos_config",
        "fetch_size",
        "max_connection_age",
    }
)

#: Options understood at foreign-table level.
TABLE_OPTION_NAMES = frozenset({"table", "query", "schema", "catalog"})


def _as_bool(name: str, value: str) -> bool:
    v = value.strip().lower()
    if v in ("true", "t", "yes", "y", "1", "on"):
        return True
    if v in ("false", "f", "no", "n", "0", "off"):
        return False
    raise OptionError(f"option {name!r} must be a boolean, got {value!r}")


def _as_int(name: str, value: str, minimum: int = 0) -> int:
    try:
        n = int(value)
    except ValueError as exc:
        raise OptionError(f"option {name!r} must be an integer, got {value!r}") from exc
    if n < minimum:
        raise OptionError(f"option {name!r} must be >= {minimum}, got {n}")
    return n


def reject_forbidden(options: Dict[str, str]) -> None:
    """Raise if any option name is on the forbidden list."""
    bad = sorted(k for k in options if k.lower() in FORBIDDEN_OPTION_NAMES)
    if bad:
        raise OptionError(
            "secret-bearing options are not accepted as SQL options: "
            + ", ".join(bad)
            + ". Put the secret in a file on the database host and reference it "
            "with password_file / jwt_file / key_file instead."
        )


@dataclass(frozen=True)
class ServerConfig:
    host: str
    port: int = 443
    catalog: Optional[str] = None
    schema: Optional[str] = None
    auth: str = "password"
    user: Optional[str] = None
    trino_user: Optional[str] = None
    password_file: Optional[str] = None
    jwt_file: Optional[str] = None
    cert_file: Optional[str] = None
    key_file: Optional[str] = None
    ca_file: Optional[str] = None
    verify: bool = True
    request_timeout: float = 60.0
    source: str = "trino_fdw"
    client_tags: tuple = field(default_factory=tuple)
    estimated_rows: int = 100_000
    estimated_row_width: int = 200
    strict_file_permissions: bool = False
    kerberos_service_name: str = "trino"
    kerberos_principal: Optional[str] = None
    kerberos_config: Optional[str] = None
    fetch_size: int = 1000
    max_connection_age: float = 600.0

    @property
    def effective_user(self) -> str:
        """The X-Trino-User value: the impersonated user if set, else the principal."""
        return self.trino_user or self.user or ""


@dataclass(frozen=True)
class TableConfig:
    table: Optional[str] = None
    query: Optional[str] = None
    schema: Optional[str] = None
    catalog: Optional[str] = None


def parse_server_options(options: Dict[str, str]) -> ServerConfig:
    """Validate and convert the server-level subset of ``options``."""
    reject_forbidden(options)

    host = options.get("host")
    if not host:
        raise OptionError("option 'host' is required")

    auth = options.get("auth", "password").strip().lower()
    if auth not in AUTH_METHODS:
        raise OptionError(f"option 'auth' must be one of {AUTH_METHODS}, got {auth!r}")

    user = options.get("user")
    password_file = options.get("password_file")
    jwt_file = options.get("jwt_file")
    cert_file = options.get("cert_file")
    key_file = options.get("key_file")

    if auth == "password":
        if not user:
            raise OptionError("auth 'password' requires option 'user' (the authenticating principal)")
        if not password_file:
            raise OptionError("auth 'password' requires option 'password_file'")
    elif auth == "jwt":
        if not jwt_file:
            raise OptionError("auth 'jwt' requires option 'jwt_file'")
    elif auth == "certificate":
        if not cert_file or not key_file:
            raise OptionError("auth 'certificate' requires options 'cert_file' and 'key_file'")
    elif auth == "kerberos":
        pass  # ticket cache / keytab are configured through the environment

    verify = _as_bool("verify", options.get("verify", "true"))
    if not verify:
        raise OptionError(
            "option 'verify' cannot be false: TLS certificate verification is mandatory. "
            "Provide the issuing CA with 'ca_file' instead."
        )

    tags = tuple(t.strip() for t in options.get("client_tags", "").split(",") if t.strip())

    return ServerConfig(
        host=host,
        port=_as_int("port", options.get("port", "443"), minimum=1),
        catalog=options.get("catalog") or None,
        schema=options.get("schema") or None,
        auth=auth,
        user=user or None,
        trino_user=options.get("trino_user") or None,
        password_file=password_file,
        jwt_file=jwt_file,
        cert_file=cert_file,
        key_file=key_file,
        ca_file=options.get("ca_file") or None,
        verify=True,
        request_timeout=float(options.get("request_timeout", "60")),
        source=options.get("source", "trino_fdw"),
        client_tags=tags,
        estimated_rows=_as_int("estimated_rows", options.get("estimated_rows", "100000")),
        estimated_row_width=_as_int("estimated_row_width", options.get("estimated_row_width", "200")),
        strict_file_permissions=_as_bool(
            "strict_file_permissions", options.get("strict_file_permissions", "false")
        ),
        kerberos_service_name=options.get("kerberos_service_name", "trino"),
        kerberos_principal=options.get("kerberos_principal") or None,
        kerberos_config=options.get("kerberos_config") or None,
        fetch_size=_as_int("fetch_size", options.get("fetch_size", "1000"), minimum=1),
        max_connection_age=float(_as_int("max_connection_age", options.get("max_connection_age", "600"))),
    )


def parse_table_options(options: Dict[str, str]) -> TableConfig:
    """Validate the table-level subset of ``options``."""
    table = options.get("table") or None
    query = options.get("query") or None
    if table and query:
        raise OptionError("options 'table' and 'query' are mutually exclusive")
    if not table and not query:
        raise OptionError("foreign table needs option 'table' (remote table name) or 'query' (SELECT)")
    if query and ";" in query:
        raise OptionError("option 'query' must be a single SELECT statement without ';'")
    return TableConfig(
        table=table,
        query=query,
        schema=options.get("schema") or None,
        catalog=options.get("catalog") or None,
    )


def unknown_options(options: Dict[str, str]) -> Iterable[str]:
    """Names in ``options`` the wrapper does not understand (for warnings)."""
    known = SERVER_OPTION_NAMES | TABLE_OPTION_NAMES | {"wrapper"}
    return sorted(k for k in options if k not in known)


class FilePermissionWarning(Warning):
    """Emitted when a secret file is readable by other users."""


def read_secret_file(path: str, strict: bool = False, warn=None) -> str:
    """Read a secret from ``path`` with permission checks.

    * The file must exist and be a regular file.
    * If it is world-readable the wrapper raises (``strict``) or warns.
    * Group-readable files only warn, because some secret mounts are 0440
      by design.
    The content is stripped of surrounding whitespace (a trailing newline is
    the usual artefact of secret tooling).
    """
    try:
        st = os.stat(path)
    except OSError as exc:
        raise OptionError(f"secret file {path!r} is not readable: {exc.strerror}") from exc
    if not stat.S_ISREG(st.st_mode):
        raise OptionError(f"secret file {path!r} is not a regular file")

    mode = stat.S_IMODE(st.st_mode)
    if mode & stat.S_IROTH:
        msg = f"secret file {path!r} is world-readable (mode {mode:04o})"
        if strict:
            raise OptionError(msg + "; refusing to use it (strict_file_permissions=true)")
        if warn:
            warn(msg + "; tighten it to 0400")
    elif mode & stat.S_IRGRP and warn:
        warn(f"secret file {path!r} is group-readable (mode {mode:04o}); 0400 is preferred")

    with open(path, "r", encoding="utf-8") as fh:
        value = fh.read().strip()
    if not value:
        raise OptionError(f"secret file {path!r} is empty")
    return value
