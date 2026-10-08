"""Deparse PostgreSQL quals and sort keys into a Trino SELECT.

Only conditions that can be expressed safely with bound parameters are
pushed down.  Everything else is left for PostgreSQL, which always
re-checks every qual on the rows the wrapper returns, so partial pushdown
is correct by construction.
"""

from __future__ import annotations

import datetime as _dt
import decimal
import uuid as _uuid
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from trino_fdw.types import SKIP, bind_template, coerce_param

# PostgreSQL operator name -> Trino operator
_BINARY_OPS = {
    "=": "=",
    "<>": "<>",
    "!=": "<>",
    "<": "<",
    ">": ">",
    "<=": "<=",
    ">=": ">=",
    "~~": "LIKE",
    "!~~": "NOT LIKE",
}

_PARAM_TYPES = (
    str,
    int,
    float,
    bool,
    bytes,
    decimal.Decimal,
    _dt.date,
    _dt.datetime,
    _dt.time,
    _uuid.UUID,
)


def quote_ident(name: str) -> str:
    """Quote a single Trino identifier."""
    return '"' + name.replace('"', '""') + '"'


def quote_qualified(name: str) -> str:
    """Quote a dotted name such as ``catalog.schema.table`` part by part.

    A part that is already double-quoted is kept as-is.
    """
    parts: List[str] = []
    buf = ""
    in_quote = False
    for ch in name:
        if ch == '"':
            in_quote = not in_quote
            buf += ch
        elif ch == "." and not in_quote:
            parts.append(buf)
            buf = ""
        else:
            buf += ch
    parts.append(buf)
    out = []
    for p in parts:
        if p.startswith('"') and p.endswith('"') and len(p) >= 2:
            out.append(p)
        else:
            out.append(quote_ident(p))
    return ".".join(out)


def table_source(table: Optional[str], query: Optional[str], catalog: Optional[str], schema: Optional[str]) -> str:
    """The FROM clause target for a foreign table."""
    if query:
        return "(" + query.strip() + ") AS trino_fdw_q"
    assert table is not None
    if "." in table:
        return quote_qualified(table)
    prefix = []
    if catalog:
        prefix.append(quote_ident(catalog))
        prefix.append(quote_ident(schema) if schema else '"default"')
    elif schema:
        prefix.append(quote_ident(schema))
    return ".".join(prefix + [quote_ident(table)])


def _bindable(value: Any) -> bool:
    return isinstance(value, _PARAM_TYPES)


def deparse_quals(
    quals: Iterable[Any], column_types: Optional[Dict[str, str]] = None
) -> Tuple[List[str], List[Any], int]:
    """Translate Multicorn quals into (conditions, params, skipped_count).

    Each qual has ``field_name``, ``operator`` and ``value``.  List quals
    (``col = ANY(array)``) carry a tuple operator and ``is_list_operator``.
    ``column_types`` maps column names to their PostgreSQL type so values
    that Multicorn passes as text can be bound with the right Trino type.
    """
    conds: List[str] = []
    params: List[Any] = []
    skipped = 0
    types = column_types or {}

    def convert(name: str, v: Any) -> Any:
        v = coerce_param(v, types.get(name, ""))
        return SKIP if v is not SKIP and not _bindable(v) else v

    for q in quals:
        col = quote_ident(q.field_name)
        ph = bind_template(types.get(q.field_name, ""))
        if getattr(q, "is_list_operator", False):
            op, is_any = q.operator
            values = [convert(q.field_name, v) for v in (q.value or [])]
            if not values or any(v is SKIP or v is None for v in values):
                skipped += 1
                continue
            if op == "=" and is_any:
                conds.append(f"{col} IN ({', '.join(ph for _ in values)})")
                params.extend(values)
            elif op in ("<>", "!=") and not is_any:
                conds.append(f"{col} NOT IN ({', '.join(ph for _ in values)})")
                params.extend(values)
            else:
                skipped += 1
            continue

        op = q.operator
        value = q.value
        if value is None:
            if op == "=":
                conds.append(f"{col} IS NULL")
            elif op in ("<>", "!="):
                conds.append(f"{col} IS NOT NULL")
            else:
                skipped += 1
            continue
        trino_op = _BINARY_OPS.get(op)
        value = convert(q.field_name, value)
        if trino_op is None or value is SKIP:
            skipped += 1
            continue
        conds.append(f"{col} {trino_op} {ph}")
        params.append(value)
    return conds, params, skipped


def deparse_sortkeys(sortkeys: Optional[Sequence[Any]]) -> List[str]:
    """ORDER BY terms for Multicorn SortKey objects (attname, is_reversed, nulls_first)."""
    terms: List[str] = []
    for k in sortkeys or []:
        term = quote_ident(k.attname)
        term += " DESC" if k.is_reversed else " ASC"
        term += " NULLS FIRST" if k.nulls_first else " NULLS LAST"
        terms.append(term)
    return terms


def build_select(
    columns: Sequence[str],
    source: str,
    conds: Sequence[str],
    order_by: Sequence[str] = (),
    limit: Optional[int] = None,
    offset: Optional[int] = None,
) -> str:
    """Assemble the final statement."""
    select_list = ", ".join(quote_ident(c) for c in columns) if columns else "1"
    sql = f"SELECT {select_list} FROM {source}"
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    if order_by:
        sql += " ORDER BY " + ", ".join(order_by)
    if offset:
        sql += f" OFFSET {int(offset)}"
    if limit is not None:
        sql += f" LIMIT {int(limit)}"
    return sql
