import os
from unittest import mock

import pytest
from multicorn import Qual, SortKey

from trino_fdw import TrinoFDW
from trino_fdw.options import OptionError


class FakeCursor:
    def __init__(self, rows):
        self._rows = list(rows)
        self.executed = None
        self.cancelled = False
        self.closed = False

    def execute(self, sql, params=None):
        self.executed = (sql, params)

    def fetchmany(self, n):
        out, self._rows = self._rows[:n], self._rows[n:]
        return out

    def fetchall(self):
        out, self._rows = self._rows, []
        return out

    def cancel(self):
        self.cancelled = True

    def close(self):
        self.closed = True


class FakeConn:
    def __init__(self, rows):
        self.rows = rows
        self.cursors = []
        self.closed = False

    def cursor(self):
        c = FakeCursor(self.rows)
        self.cursors.append(c)
        return c

    def close(self):
        self.closed = True


@pytest.fixture
def secret(tmp_path):
    p = tmp_path / "pw"
    p.write_text("s3cret\n")
    os.chmod(p, 0o400)
    return str(p)


@pytest.fixture
def options(secret):
    return {
        "host": "trino.internal", "port": "8443", "catalog": "hive", "schema": "prod",
        "auth": "password", "user": "svc_fdw", "trino_user": "reader",
        "password_file": secret, "ca_file": "/ca.pem", "table": "audit_events",
    }


class Col:
    def __init__(self, type_name):
        self.type_name = type_name


COLUMNS = {"id": Col("bigint"), "username": Col("text")}


def test_execute_builds_sql_and_connects_with_impersonation(options, pg_log):
    conn = FakeConn([(1, "a"), (2, "b")])
    with mock.patch("trino.dbapi.connect", return_value=conn) as connect:
        fdw = TrinoFDW(options, COLUMNS)
        rows = list(fdw.execute([Qual("id", ">", 0)], ["id", "username"], sortkeys=[SortKey("id")]))

    assert rows == [{"id": 1, "username": "a"}, {"id": 2, "username": "b"}]
    kw = connect.call_args.kwargs
    assert kw["user"] == "reader" and kw["http_scheme"] == "https" and kw["verify"] == "/ca.pem"
    assert kw["auth"]._username == "svc_fdw" and kw["auth"]._password == "s3cret"
    cur = conn.cursors[0]
    assert cur.executed == ('SELECT "id", "username" FROM "hive"."prod"."audit_events" WHERE "id" > ? ORDER BY "id" ASC NULLS LAST', [0])
    assert cur.closed


def test_text_param_coerced_by_column_type(options):
    conn = FakeConn([])
    with mock.patch("trino.dbapi.connect", return_value=conn):
        fdw = TrinoFDW(options, {"id": Col("bigint"), "ts": Col("timestamp(3) with time zone")})
        list(fdw.execute([Qual("id", ">", "3000000000"), Qual("ts", ">=", "2026-10-08 10:04:00+00")], ["id"]))
    sql, params = conn.cursors[0].executed
    assert sql.endswith('WHERE "id" > ? AND "ts" >= ?')
    assert params == [3000000000, __import__("datetime").datetime(2026, 10, 8, 10, 4, tzinfo=__import__("datetime").timezone.utc)]


def test_limit_offset_pushdown(options):
    conn = FakeConn([(1, "a")])
    with mock.patch("trino.dbapi.connect", return_value=conn):
        fdw = TrinoFDW(options, COLUMNS)
        assert fdw.can_limit(5, 2) is True
        list(fdw.execute([], ["id"], limit=5, offset=2))
    assert conn.cursors[0].executed[0].endswith(' OFFSET 2 LIMIT 5')


def test_connection_is_reused(options):
    conn = FakeConn([])
    with mock.patch("trino.dbapi.connect", return_value=conn) as connect:
        fdw = TrinoFDW(options, COLUMNS)
        list(fdw.execute([], ["id"]))
        list(fdw.execute([], ["id"]))
    assert connect.call_count == 1 and len(conn.cursors) == 2


def test_retry_once_on_connection_failure(options):
    good = FakeConn([(1, "a")])

    class Dead(FakeConn):
        def cursor(self):
            raise ConnectionError("gone")

    with mock.patch("trino.dbapi.connect", side_effect=[Dead([]), good]) as connect:
        fdw = TrinoFDW(options, COLUMNS)
        assert list(fdw.execute([], ["id", "username"])) == [{"id": 1, "username": "a"}]
    assert connect.call_count == 2


def test_connection_reopened_after_max_age(options, monkeypatch):
    import trino_fdw.fdw as fdwmod
    clock = [1000.0]
    monkeypatch.setattr(fdwmod.time, "monotonic", lambda: clock[0])
    conns = [FakeConn([]), FakeConn([])]
    with mock.patch("trino.dbapi.connect", side_effect=conns) as connect:
        fdw = TrinoFDW({**options, "max_connection_age": "30"}, COLUMNS)
        list(fdw.execute([], ["id"]))
        clock[0] += 10
        list(fdw.execute([], ["id"]))
        assert connect.call_count == 1
        clock[0] += 31
        list(fdw.execute([], ["id"]))
    assert connect.call_count == 2 and conns[0].closed


def test_auth_failure_rereads_rotated_secret(options, secret):
    class HttpError(Exception):
        pass

    class Rejected(FakeConn):
        def cursor(self):
            raise HttpError("error 401: Unauthorized")

    seen = []

    def connect(**kw):
        seen.append(kw["auth"]._password)
        if len(seen) == 1:
            os.chmod(secret, 0o600)
            with open(secret, "w") as fh:
                fh.write("rotated\n")
            os.chmod(secret, 0o400)
            return Rejected([])
        return FakeConn([(1, "a")])

    with mock.patch("trino.dbapi.connect", side_effect=connect):
        fdw = TrinoFDW(options, COLUMNS)
        assert list(fdw.execute([], ["id", "username"])) == [{"id": 1, "username": "a"}]
    assert seen == ["s3cret", "rotated"]


def test_query_error_is_not_retried(options):
    class TrinoUserError(Exception):
        pass

    class Bad(FakeConn):
        def cursor(self):
            raise TrinoUserError("syntax")

    with mock.patch("trino.dbapi.connect", side_effect=[Bad([]), Bad([])]) as connect:
        fdw = TrinoFDW(options, COLUMNS)
        with pytest.raises(TrinoUserError):
            list(fdw.execute([], ["id"]))
    assert connect.call_count == 1


def test_end_scan_cancels_running_query(options):
    conn = FakeConn([(i, "x") for i in range(10)])
    with mock.patch("trino.dbapi.connect", return_value=conn):
        fdw = TrinoFDW({**options, "fetch_size": "2"}, COLUMNS)
        gen = fdw.execute([], ["id", "username"])
        next(gen)
        fdw.end_scan()
    assert conn.cursors[0].cancelled


def test_query_option_and_explain(options):
    opts = {k: v for k, v in options.items() if k != "table"}
    opts["query"] = "SELECT current_user AS u"
    fdw = TrinoFDW(opts, {"u": None})
    out = fdw.explain([], ["u"])
    assert out[0] == 'Trino SQL: SELECT "u" FROM (SELECT current_user AS u) AS trino_fdw_q'


def test_password_option_is_refused(options, pg_log):
    with pytest.raises(OptionError):
        TrinoFDW({**options, "password": "x"}, COLUMNS)
    assert any("secret-bearing" in m for _, m, _ in pg_log)


def test_import_schema(options):
    rows = [
        ("audit_events", "id", "bigint", 1),
        ("audit_events", "ts", "timestamp(3) with time zone", 2),
        ("users", "name", "varchar", 1),
    ]
    conn = FakeConn(rows)
    srv = {k: v for k, v in options.items() if k != "table"}
    with mock.patch("trino.dbapi.connect", return_value=conn):
        defs = TrinoFDW.import_schema("prod", srv, {}, "limit", ["audit_events"])
    assert len(defs) == 1
    t = defs[0]
    assert t.table_name == "audit_events" and t.options == {"table": "audit_events", "schema": "prod"}
    assert [(c.column_name, c.type_name) for c in t.columns] == [("id", "bigint"), ("ts", "timestamp with time zone")]
    assert conn.cursors[0].executed[1] == ["prod"]
    assert conn.closed
