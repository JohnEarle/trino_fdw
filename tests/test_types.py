import datetime as dt

from trino_fdw.types import to_pg_value, trino_to_pg


def test_type_mapping():
    cases = {
        "bigint": "bigint", "integer": "integer", "tinyint": "smallint", "boolean": "boolean",
        "double": "double precision", "real": "real", "decimal(12,2)": "numeric(12,2)",
        "varchar(50)": "varchar(50)", "varchar": "text", "char(3)": "char(3)", "varbinary": "bytea",
        "json": "jsonb", "date": "date", "time(3)": "time", "timestamp(3)": "timestamp",
        "timestamp(6) with time zone": "timestamp with time zone", "uuid": "uuid",
        "ipaddress": "inet", "array(varchar)": "jsonb", "map(varchar, bigint)": "jsonb",
        "row(a bigint, b varchar)": "jsonb", "interval day to second": "interval", "HyperLogLog": "text",
    }
    for trino_type, pg_type in cases.items():
        assert trino_to_pg(trino_type) == pg_type, trino_type


def test_value_conversion():
    assert to_pg_value(1) == 1 and to_pg_value(None) is None and to_pg_value("x") == "x"
    d = dt.datetime(2026, 1, 1, 12)
    assert to_pg_value(d) is d
    assert to_pg_value([1, "a", d]) == '[1, "a", "2026-01-01T12:00:00"]'
    assert to_pg_value({"k": [1, 2]}) == '{"k": [1, 2]}'


def test_coerce_param_from_pg_text():
    from decimal import Decimal
    from trino_fdw.types import SKIP, coerce_param

    assert coerce_param("3000000000", "bigint") == 3000000000
    assert coerce_param("1.5", "double precision") == 1.5
    assert coerce_param("12.30", "numeric(12,2)") == Decimal("12.30")
    assert coerce_param(1.5, "numeric") == Decimal("1.5")
    assert coerce_param("t", "boolean") is True and coerce_param("f", "bool") is False
    ts = coerce_param("2026-10-08 10:04:00+00", "timestamp(3) with time zone")
    assert ts == dt.datetime(2026, 10, 8, 10, 4, tzinfo=dt.timezone.utc) and ts.tzinfo == dt.timezone.utc
    ts2 = coerce_param("2026-10-08 10:04:00.25-05:30", "timestamptz")
    assert ts2 == dt.datetime(2026, 10, 8, 15, 34, 0, 250000, tzinfo=dt.timezone.utc)
    assert coerce_param("2026-10-08 10:04:00", "timestamp") == dt.datetime(2026, 10, 8, 10, 4)
    assert coerce_param("2026-10-08", "date") == dt.date(2026, 10, 8)
    assert coerce_param("10:04:00", "time") == dt.time(10, 4)
    assert coerce_param("abc", "uuid") is SKIP and coerce_param("x", "text") == "x"
    assert coerce_param("infinity", "timestamptz") is SKIP
    assert coerce_param("notanumber", "bigint") is SKIP
    assert coerce_param("maybe", "boolean") is SKIP
    assert coerce_param(None, "bigint") is None
    aware = dt.datetime(2026, 1, 1, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    assert coerce_param(aware, "timestamptz").tzinfo == dt.timezone.utc


def test_coerce_uuid_inet_bytea_and_unsafe_types():
    import uuid
    from trino_fdw.types import SKIP, bind_template, coerce_param

    u = coerce_param("8b1e9b4a-2a7a-4b7e-9c1d-1f2e3d4c5b6a", "uuid")
    assert isinstance(u, uuid.UUID) and bind_template("uuid") == "?"
    assert coerce_param("not-a-uuid", "uuid") is SKIP
    assert coerce_param("10.0.0.2", "inet") == "10.0.0.2" and bind_template("inet") == "CAST(? AS ipaddress)"
    assert coerce_param("10.0.0.0/24", "inet") is SKIP
    assert coerce_param(memoryview(b"\x01"), "bytea") == b"\x01"
    assert coerce_param(b"x", "text") is SKIP
    for t in ("jsonb", "json", "interval", "time with time zone", "cidr", "macaddr", "bigint[]", "mystery"):
        assert coerce_param("anything", t) is SKIP, t
    assert coerce_param("plain", "character varying(20)") == "plain"


def test_numeric_precision_guard():
    from decimal import Decimal
    from trino_fdw.types import SKIP, coerce_param

    assert coerce_param(99.99, "numeric(10,2)") == Decimal("99.99")
    assert coerce_param(123456789012345.0, "numeric") == Decimal("123456789012345")
    assert coerce_param(1234567890123456.78, "numeric") is SKIP
    assert coerce_param(float("nan"), "numeric") is SKIP
    assert coerce_param(2.5, "bigint") == 2.5          # double literal, Trino coerces
