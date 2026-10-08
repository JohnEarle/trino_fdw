import os
import stat

import pytest

from trino_fdw.options import (
    OptionError,
    parse_server_options,
    parse_table_options,
    read_secret_file,
)

BASE = {"host": "trino", "auth": "password", "user": "svc", "password_file": "/x"}


def test_password_auth_minimal():
    cfg = parse_server_options(BASE)
    assert cfg.port == 443 and cfg.auth == "password" and cfg.effective_user == "svc"


def test_impersonation_user():
    cfg = parse_server_options({**BASE, "trino_user": "reader"})
    assert cfg.effective_user == "reader" and cfg.user == "svc"


@pytest.mark.parametrize("bad", ["password", "Password", "token", "secret", "api_key", "key", "keytab"])
def test_secret_options_are_refused(bad):
    with pytest.raises(OptionError, match="secret-bearing"):
        parse_server_options({**BASE, bad: "hunter2"})


def test_verify_false_refused():
    with pytest.raises(OptionError, match="verify"):
        parse_server_options({**BASE, "verify": "false"})


def test_unknown_auth():
    with pytest.raises(OptionError, match="auth"):
        parse_server_options({**BASE, "auth": "header"})


@pytest.mark.parametrize(
    "auth,missing",
    [("password", "password_file"), ("jwt", "jwt_file"), ("certificate", "cert_file")],
)
def test_required_files_per_method(auth, missing):
    opts = {"host": "t", "auth": auth, "user": "u", "password_file": "/p", "jwt_file": "/j",
            "cert_file": "/c", "key_file": "/k"}
    del opts[missing]
    with pytest.raises(OptionError, match=missing):
        parse_server_options(opts)


def test_table_options():
    assert parse_table_options({"table": "t"}).table == "t"
    assert parse_table_options({"query": "select 1"}).query == "select 1"
    with pytest.raises(OptionError):
        parse_table_options({"table": "t", "query": "select 1"})
    with pytest.raises(OptionError):
        parse_table_options({})
    with pytest.raises(OptionError):
        parse_table_options({"query": "select 1; drop table x"})


def test_read_secret_file(tmp_path):
    p = tmp_path / "pw"
    p.write_text("s3cret\n")
    os.chmod(p, 0o400)
    assert read_secret_file(str(p)) == "s3cret"


def test_world_readable_warns_or_raises(tmp_path):
    p = tmp_path / "pw"
    p.write_text("x")
    os.chmod(p, 0o644)
    warnings = []
    assert read_secret_file(str(p), warn=warnings.append) == "x"
    assert warnings and "world-readable" in warnings[0]
    with pytest.raises(OptionError, match="world-readable"):
        read_secret_file(str(p), strict=True)


def test_missing_or_empty_secret(tmp_path):
    with pytest.raises(OptionError, match="not readable"):
        read_secret_file(str(tmp_path / "nope"))
    p = tmp_path / "empty"
    p.write_text("\n")
    with pytest.raises(OptionError, match="empty"):
        read_secret_file(str(p))
