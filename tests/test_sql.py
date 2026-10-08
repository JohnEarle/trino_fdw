import datetime as dt
from decimal import Decimal

from multicorn import Qual, SortKey

from trino_fdw.sql import build_select, deparse_quals, deparse_sortkeys, quote_qualified, table_source


def test_quote_qualified():
    assert quote_qualified("hive.prod.events") == '"hive"."prod"."events"'
    assert quote_qualified('hive."we.ird".t') == '"hive"."we.ird"."t"'
    assert quote_qualified('a"b') == '"a""b"'


def test_table_source():
    assert table_source("t", None, "hive", "prod") == '"hive"."prod"."t"'
    assert table_source("t", None, None, "prod") == '"prod"."t"'
    assert table_source("s.t", None, "hive", "prod") == '"s"."t"'
    assert table_source(None, " select 1 ", None, None) == "(select 1) AS trino_fdw_q"


def test_simple_quals_pushdown():
    quals = [
        Qual("id", ">", 10),
        Qual("name", "~~", "a%"),
        Qual("ts", ">=", dt.datetime(2026, 1, 1)),
        Qual("amt", "<=", Decimal("1.5")),
        Qual("flag", "=", True),
    ]
    conds, params, skipped = deparse_quals(quals)
    assert conds == ['"id" > ?', '"name" LIKE ?', '"ts" >= ?', '"amt" <= ?', '"flag" = ?']
    assert params == [10, "a%", dt.datetime(2026, 1, 1), Decimal("1.5"), True]
    assert skipped == 0


def test_null_tests():
    conds, params, skipped = deparse_quals([Qual("a", "=", None), Qual("b", "<>", None), Qual("c", "<", None)])
    assert conds == ['"a" IS NULL', '"b" IS NOT NULL'] and params == [] and skipped == 1


def test_list_quals():
    conds, params, skipped = deparse_quals(
        [Qual("id", ("=", True), [1, 2, 3]), Qual("x", ("<>", False), ["a"]), Qual("y", ("<", True), [1])]
    )
    assert conds == ['"id" IN (?, ?, ?)', '"x" NOT IN (?)']
    assert params == [1, 2, 3, "a"] and skipped == 1


def test_typed_coercion_of_text_params():
    types = {"id": "bigint", "flag": "boolean", "ts": "timestamp with time zone", "score": "double precision"}
    conds, params, skipped = deparse_quals(
        [Qual("id", ">", "3000000000"), Qual("flag", "=", "t"), Qual("ts", ">", "2026-10-08 10:04:00+00"),
         Qual("score", "<", "0.5"), Qual("id", ("=", True), ["1", "2"]), Qual("ts", "=", "infinity")],
        types,
    )
    assert conds == ['"id" > ?', '"flag" = ?', '"ts" > ?', '"score" < ?', '"id" IN (?, ?)']
    assert params[0] == 3000000000 and params[1] is True and params[3] == 0.5 and params[4:] == [1, 2]
    assert params[2] == dt.datetime(2026, 10, 8, 10, 4, tzinfo=dt.timezone.utc)
    assert skipped == 1


def test_inet_cast_template_and_unsafe_types_skipped():
    types = {"ip": "inet", "details": "jsonb", "ips": "inet"}
    conds, params, skipped = deparse_quals(
        [Qual("ip", "=", "10.0.0.2"), Qual("details", "=", '{"a":1}'), Qual("ips", ("=", True), ["10.0.0.1", "10.0.0.3"])],
        types,
    )
    assert conds == ['"ip" = CAST(? AS ipaddress)', '"ips" IN (CAST(? AS ipaddress), CAST(? AS ipaddress))']
    assert params == ["10.0.0.2", "10.0.0.1", "10.0.0.3"] and skipped == 1


def test_unsupported_operator_or_type_is_skipped():
    conds, params, skipped = deparse_quals([Qual("a", "@>", [1]), Qual("b", "=", memoryview(b"x"))])
    assert conds == [] and skipped == 2


def test_sortkeys_and_select():
    order = deparse_sortkeys([SortKey("ts", is_reversed=True), SortKey("id", nulls_first=True)])
    assert order == ['"ts" DESC NULLS LAST', '"id" ASC NULLS FIRST']
    sql = build_select(["id", "ts"], '"hive"."p"."t"', ['"id" > ?'], order, limit=5, offset=2)
    assert sql == 'SELECT "id", "ts" FROM "hive"."p"."t" WHERE "id" > ? ORDER BY "ts" DESC NULLS LAST, "id" ASC NULLS FIRST OFFSET 2 LIMIT 5'
    assert build_select([], "t", []) == "SELECT 1 FROM t"
