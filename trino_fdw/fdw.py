"""The Multicorn foreign data wrapper class."""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence

from multicorn import ColumnDefinition, ForeignDataWrapper, TableDefinition
from multicorn.utils import log_to_postgres

from trino_fdw.auth import build_auth
from trino_fdw.options import (
    OptionError,
    ServerConfig,
    TableConfig,
    parse_server_options,
    parse_table_options,
    unknown_options,
)
from trino_fdw.sql import (
    build_select,
    deparse_quals,
    deparse_sortkeys,
    quote_ident,
    table_source,
)
from trino_fdw.types import normalize_pg_type, to_pg_value, trino_to_pg


def _warn(msg: str) -> None:
    log_to_postgres(f"trino_fdw: {msg}", logging.WARNING)


def _debug(msg: str) -> None:
    log_to_postgres(f"trino_fdw: {msg}", logging.DEBUG)


def _error(msg: str, hint: Optional[str] = None) -> None:
    log_to_postgres(f"trino_fdw: {msg}", logging.ERROR, hint=hint)


def _connect(cfg: ServerConfig):
    """Open a Trino DB-API connection according to ``cfg``.

    ``user`` becomes the X-Trino-User header (the identity queries run as).
    The authenticating principal comes from the auth object.  When they
    differ, Trino applies its impersonation rules.
    """
    import trino.dbapi

    auth = build_auth(cfg, warn=_warn)
    return trino.dbapi.connect(
        host=cfg.host,
        port=cfg.port,
        user=cfg.effective_user,
        catalog=cfg.catalog,
        schema=cfg.schema,
        http_scheme="https",
        verify=cfg.ca_file if cfg.ca_file else True,
        auth=auth,
        source=cfg.source,
        request_timeout=cfg.request_timeout,
        client_tags=list(cfg.client_tags) or None,
        legacy_primitive_types=False,
    )


class TrinoFDW(ForeignDataWrapper):
    """Read-only foreign data wrapper for Trino."""

    def __init__(self, options: Dict[str, str], columns: Dict[str, Any]):
        super().__init__(options, columns)
        try:
            self.server: ServerConfig = parse_server_options(options)
            self.table: TableConfig = parse_table_options(options)
        except OptionError as exc:
            _error(str(exc))
            raise
        for name in unknown_options(options):
            _warn(f"ignoring unknown option {name!r}")
        self.columns = columns
        self.column_types: Dict[str, str] = {
            name: normalize_pg_type(_column_type(col)) for name, col in columns.items()
        }
        self._conn = None
        self._conn_opened = 0.0
        self._cursor = None
        self._source = table_source(
            self.table.table,
            self.table.query,
            self.table.catalog or self.server.catalog,
            self.table.schema or self.server.schema,
        )

    # -- connection lifecycle -------------------------------------------------

    def _connection(self):
        """Cached connection, re-opened when older than ``max_connection_age``.

        Re-opening re-reads the credential file, so a rotated secret is
        picked up without restarting the backend.  An authentication
        failure on a cached connection also triggers a re-open (see
        :meth:`_open_cursor`).
        """
        age = time.monotonic() - self._conn_opened
        if self._conn is not None and self.server.max_connection_age and age > self.server.max_connection_age:
            _debug(f"connection is {age:.0f}s old; reconnecting")
            self._reset_connection()
        if self._conn is None:
            self._conn = _connect(self.server)
            self._conn_opened = time.monotonic()
        return self._conn

    def _reset_connection(self) -> None:
        conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # pragma: no cover - best effort
                pass

    # -- planner hooks ---------------------------------------------------------

    def get_rel_size(self, quals, columns):
        return (self.server.estimated_rows, self.server.estimated_row_width)

    def can_sort(self, sortkeys):
        # Trino can order by any plain column; collations cannot be honoured.
        return [k for k in sortkeys if getattr(k, "collate", None) is None]

    def can_limit(self, limit, offset):
        # Multicorn only asks when the whole query is pushable; Trino supports both.
        return True

    def explain(self, quals, columns, sortkeys=None, verbose=False, **kwargs):
        sql, params = self._build(quals, columns, sortkeys, kwargs.get("limit"), kwargs.get("offset"))
        return [f"Trino SQL: {sql}", f"Bound parameters: {len(params)}"]

    # -- scan ------------------------------------------------------------------

    def _build(self, quals, columns, sortkeys, limit, offset):
        conds, params, skipped = deparse_quals(quals, self.column_types)
        if skipped:
            _debug(f"{skipped} qual(s) not pushed down; PostgreSQL will re-check them")
        order_by = deparse_sortkeys(sortkeys)
        sql = build_select(list(columns), self._source, conds, order_by, limit, offset)
        return sql, params

    def execute(self, quals, columns, sortkeys=None, **kwargs) -> Iterable[Dict[str, Any]]:
        limit = kwargs.get("limit")
        offset = kwargs.get("offset")
        cols = list(columns)
        sql, params = self._build(quals, cols, sortkeys, limit, offset)
        _debug(f"executing: {sql}")

        cursor = self._open_cursor(sql, params)
        self._cursor = cursor
        try:
            while True:
                rows = cursor.fetchmany(self.server.fetch_size)
                if not rows:
                    break
                for row in rows:
                    yield {name: to_pg_value(value) for name, value in zip(cols, row)}
        finally:
            self._cursor = None
            try:
                cursor.close()
            except Exception:  # pragma: no cover
                pass

    def _open_cursor(self, sql: str, params: Sequence[Any]):
        """Run ``sql``; retry once on a dead connection."""
        last_exc: Optional[Exception] = None
        for attempt in (1, 2):
            try:
                cur = self._connection().cursor()
                cur.execute(sql, list(params) if params else None)
                return cur
            except Exception as exc:  # noqa: BLE001 - we re-raise after cleanup
                last_exc = exc
                self._reset_connection()
                if attempt == 2 or _is_query_error(exc):
                    break
        _error(f"query failed: {last_exc}", hint="Check Trino logs with the query source 'trino_fdw'.")
        raise last_exc  # type: ignore[misc]

    def end_scan(self):
        """Called by Multicorn when the scan ends early (LIMIT, error, cancel)."""
        cur, self._cursor = self._cursor, None
        if cur is not None:
            try:
                cur.cancel()
            except Exception:  # pragma: no cover - best effort
                pass

    # -- schema import ---------------------------------------------------------

    @classmethod
    def import_schema(cls, schema, srv_options, options, restriction_type, restricts):
        """IMPORT FOREIGN SCHEMA support via Trino's information_schema."""
        cfg = parse_server_options(dict(srv_options))
        catalog = options.get("catalog") or cfg.catalog
        if not catalog:
            raise OptionError("IMPORT FOREIGN SCHEMA needs a catalog (server option or OPTIONS (catalog '...'))")

        sql = (
            f"SELECT table_name, column_name, data_type, ordinal_position "
            f"FROM {quote_ident(catalog)}.information_schema.columns "
            f"WHERE table_schema = ? ORDER BY table_name, ordinal_position"
        )
        conn = _connect(cfg)
        try:
            cur = conn.cursor()
            cur.execute(sql, [schema])
            rows = cur.fetchall()
        finally:
            conn.close()

        wanted = set(restricts or [])
        tables: Dict[str, List[ColumnDefinition]] = {}
        for table_name, column_name, data_type, _pos in rows:
            if restriction_type == "limit" and table_name not in wanted:
                continue
            if restriction_type == "except" and table_name in wanted:
                continue
            tables.setdefault(table_name, []).append(
                ColumnDefinition(column_name, type_name=trino_to_pg(data_type))
            )

        defs = []
        for table_name, cols in tables.items():
            table_opts = {"table": table_name, "schema": schema}
            if catalog != cfg.catalog:
                table_opts["catalog"] = catalog
            defs.append(TableDefinition(table_name, columns=cols, options=table_opts))
        return defs


def _column_type(col: Any) -> str:
    """Type name of a Multicorn ColumnDefinition (tolerates plain strings/None)."""
    if col is None:
        return ""
    if isinstance(col, str):
        return col
    return getattr(col, "type_name", "") or getattr(col, "base_type_name", "") or ""


def _is_query_error(exc: Exception) -> bool:
    """True for errors that will not be fixed by reconnecting (syntax, permission...)."""
    name = type(exc).__name__
    return name in {"TrinoUserError", "TrinoQueryError", "TrinoDataError"}
