"""Type mapping between Trino and PostgreSQL."""

from __future__ import annotations

import datetime as _dt
import json
import re
from typing import Any

_PARAM_RE = re.compile(r"^\s*([a-z_ ]+?)\s*(?:\((.*)\))?\s*(with time zone)?\s*$", re.I)


def trino_to_pg(type_name: str) -> str:
    """Map a Trino type string (as in information_schema) to a PostgreSQL type."""
    m = _PARAM_RE.match(type_name)
    if not m:
        return "text"
    base = m.group(1).strip().lower()
    args = (m.group(2) or "").strip()
    tz = bool(m.group(3))

    if base == "boolean":
        return "boolean"
    if base == "tinyint" or base == "smallint":
        return "smallint"
    if base == "integer":
        return "integer"
    if base == "bigint":
        return "bigint"
    if base == "real":
        return "real"
    if base == "double":
        return "double precision"
    if base == "decimal":
        return f"numeric({args})" if args else "numeric"
    if base == "varchar":
        return f"varchar({args})" if args and args.isdigit() else "text"
    if base == "char":
        return f"char({args})" if args else "char(1)"
    if base == "varbinary":
        return "bytea"
    if base == "json":
        return "jsonb"
    if base == "date":
        return "date"
    if base == "time":
        return "time with time zone" if tz else "time"
    if base == "timestamp":
        return "timestamp with time zone" if tz else "timestamp"
    if base == "interval year to month" or base == "interval day to second":
        return "interval"
    if base == "uuid":
        return "uuid"
    if base == "ipaddress":
        return "inet"
    if base in ("array", "map", "row"):
        return "jsonb"
    return "text"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "_asdict"):
        return {k: _jsonable(v) for k, v in value._asdict().items()}
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (_dt.datetime, _dt.date, _dt.time)):
        return value.isoformat()
    if isinstance(value, bytes):
        return value.hex()
    return value


def to_pg_value(value: Any) -> Any:
    """Convert a value from the Trino client into something Multicorn can hand to PostgreSQL.

    Scalars pass through.  Nested structures (ROW, ARRAY, MAP) are encoded as
    JSON text so they fit a ``jsonb``/``text`` column.
    """
    if value is None or isinstance(value, (str, int, float, bool, bytes, _dt.date, _dt.time)):
        return value
    if isinstance(value, (list, tuple, dict)) or hasattr(value, "_asdict"):
        return json.dumps(_jsonable(value), default=str)
    return value


# ---------------------------------------------------------------------------
# Parameter coercion: PostgreSQL constant -> Python value Trino can bind
# ---------------------------------------------------------------------------

import decimal as _decimal
import re as _re
import uuid as _uuid

#: Returned by :func:`coerce_param` when a value cannot be bound safely.
SKIP = object()

#: Floats that came from PostgreSQL ``numeric`` constants round-trip exactly
#: only up to this many significant digits; beyond it the digits are already
#: lost and pushing the value down could filter out the matching rows.
MAX_EXACT_FLOAT_DIGITS = 15

_INT_TYPES = {"smallint", "integer", "bigint", "int2", "int4", "int8", "int", "serial", "bigserial", "oid"}
_FLOAT_TYPES = {"real", "double precision", "float4", "float8", "float"}
_DECIMAL_TYPES = {"numeric", "decimal"}
_BOOL_TYPES = {"boolean", "bool"}
_TS_TYPES = {"timestamp", "timestamp without time zone"}
_TSTZ_TYPES = {"timestamp with time zone", "timestamptz"}
_TIME_TYPES = {"time", "time without time zone"}
_TEXT_TYPES = {"text", "varchar", "character varying", "char", "character", "bpchar", "name", "citext"}
_BYTEA_TYPES = {"bytea"}
_UUID_TYPES = {"uuid"}
_INET_TYPES = {"inet"}

#: Trino-side wrapper for the bound parameter, per PostgreSQL column type.
#: ``?`` means the client literal already has the right Trino type.
_BIND_TEMPLATES = {**{t: "CAST(? AS ipaddress)" for t in _INET_TYPES}}

_TYPMOD_RE = _re.compile(r"\s*\([^)]*\)")
_TS_RE = _re.compile(
    r"^(\d{4,})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,6}))?\s*"
    r"(?:([+-])(\d{2})(?::?(\d{2}))?)?$"
)


def normalize_pg_type(type_name: str) -> str:
    """'timestamp(3) with time zone' -> 'timestamp with time zone'."""
    return _TYPMOD_RE.sub("", type_name or "").strip().lower()


def _parse_ts(text: str):
    m = _TS_RE.match(text.strip())
    if not m:
        return None
    y, mo, d, h, mi, s, frac, sign, oh, om = m.groups()
    micro = int((frac or "0").ljust(6, "0"))
    tz = None
    if sign:
        off = _dt.timedelta(hours=int(oh), minutes=int(om or 0))
        tz = _dt.timezone(-off if sign == "-" else off)
    try:
        return _dt.datetime(int(y), int(mo), int(d), int(h), int(mi), int(s), micro, tzinfo=tz)
    except ValueError:
        return None


def bind_template(pg_type: str) -> str:
    """The placeholder expression for a parameter bound against ``pg_type``."""
    return _BIND_TEMPLATES.get(normalize_pg_type(pg_type), "?")


def _float_to_decimal(value: float) -> Any:
    """Recover a Decimal from a float Multicorn made out of a ``numeric`` constant.

    Any decimal with at most 15 significant digits survives the float round
    trip exactly, so those are safe.  Longer values are already damaged and
    are not pushed down.
    """
    if value != value or value in (float("inf"), float("-inf")):
        return SKIP
    d = _decimal.Decimal(repr(value))
    digits = d.as_tuple().digits
    # strip trailing zeros for the significance count
    while len(digits) > 1 and digits[-1] == 0:
        digits = digits[:-1]
    if len(digits) > MAX_EXACT_FLOAT_DIGITS:
        return SKIP
    return d


def coerce_param(value: Any, pg_type: str) -> Any:
    """Convert ``value`` for binding against a column of PostgreSQL type ``pg_type``.

    Multicorn hands the wrapper native Python objects only for int4, numeric,
    date, timestamp, text and bytea.  Everything else is the type's text
    representation.  Returns :data:`SKIP` if the value cannot be converted
    safely; PostgreSQL then evaluates that qual itself.
    """
    if value is None:
        return None
    t = normalize_pg_type(pg_type)

    if isinstance(value, bool):
        return value
    if isinstance(value, _dt.datetime):
        if t in _TSTZ_TYPES and value.tzinfo is not None:
            return value.astimezone(_dt.timezone.utc)
        return value
    if isinstance(value, float):
        return _float_to_decimal(value) if t in _DECIMAL_TYPES else value
    if isinstance(value, (int, _decimal.Decimal, _dt.date, _dt.time)):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value) if t in _BYTEA_TYPES else SKIP
    if not isinstance(value, str):
        return SKIP

    text = value.strip()
    try:
        if t in _INT_TYPES:
            return int(text)
        if t in _FLOAT_TYPES:
            return float(text)
        if t in _DECIMAL_TYPES:
            return _decimal.Decimal(text)
        if t in _BOOL_TYPES:
            lowered = text.lower()
            if lowered in ("t", "true", "y", "yes", "on", "1"):
                return True
            if lowered in ("f", "false", "n", "no", "off", "0"):
                return False
            return SKIP
        if t in _TSTZ_TYPES:
            parsed = _parse_ts(text)
            if parsed is None:
                return SKIP
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=_dt.timezone.utc)
            return parsed.astimezone(_dt.timezone.utc)
        if t in _TS_TYPES:
            parsed = _parse_ts(text)
            return SKIP if parsed is None else parsed.replace(tzinfo=None)
        if t == "date":
            return _dt.date.fromisoformat(text)
        if t in _TIME_TYPES:
            return _dt.time.fromisoformat(text)
        if t in _UUID_TYPES:
            return _uuid.UUID(text)          # client renders UUID '...'
        if t in _INET_TYPES:
            return SKIP if "/" in text else text   # CAST(? AS ipaddress); no CIDR
    except (ValueError, _decimal.InvalidOperation):
        return SKIP
    if t in _TEXT_TYPES or t == "":
        return value
    # json/jsonb, interval, time with time zone, cidr, macaddr, arrays, ...:
    # no safe Trino literal, leave the qual to PostgreSQL.
    return SKIP
