"""Build a ``trino.auth`` object from a :class:`ServerConfig`.

Every method sources its material from the filesystem or the process
environment, never from SQL options.  TLS is mandatory for all of them.
"""

from __future__ import annotations

from typing import Any, Callable, Optional

from trino_fdw.options import OptionError, ServerConfig, read_secret_file


def build_auth(cfg: ServerConfig, warn: Optional[Callable[[str], None]] = None) -> Any:
    """Return an authentication object for ``trino.dbapi.connect``."""
    from trino import auth as trino_auth  # imported lazily; heavy

    strict = cfg.strict_file_permissions

    if cfg.auth == "password":
        secret = read_secret_file(cfg.password_file, strict=strict, warn=warn)
        return trino_auth.BasicAuthentication(cfg.user, secret)

    if cfg.auth == "jwt":
        token = read_secret_file(cfg.jwt_file, strict=strict, warn=warn)
        return trino_auth.JWTAuthentication(token)

    if cfg.auth == "certificate":
        # The private key is read by the TLS layer; only check its permissions here.
        read_secret_file(cfg.key_file, strict=strict, warn=warn)
        return trino_auth.CertificateAuthentication(cfg.cert_file, cfg.key_file)

    if cfg.auth == "kerberos":
        try:
            kwargs = {
                "service_name": cfg.kerberos_service_name,
                "mutual_authentication": True,
                "ca_bundle": cfg.ca_file,
            }
            if cfg.kerberos_principal:
                kwargs["principal"] = cfg.kerberos_principal
            if cfg.kerberos_config:
                kwargs["config"] = cfg.kerberos_config
            return trino_auth.KerberosAuthentication(**kwargs)
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise OptionError(
                "auth 'kerberos' needs the 'trino[kerberos]' extra installed in the image"
            ) from exc

    raise OptionError(f"unsupported auth method {cfg.auth!r}")
