"""Provide a stand-in ``multicorn`` module so the wrapper imports outside PostgreSQL."""

import logging
import sys
import types

_log: list = []


class ForeignDataWrapper:
    def __init__(self, options, columns):
        self.fdw_options = options
        self.fdw_columns = columns


class ColumnDefinition:
    def __init__(self, column_name, type_oid=0, typmod=0, type_name="", base_type_name="", options=None):
        self.column_name = column_name
        self.type_name = type_name
        self.options = options or {}


class TableDefinition:
    def __init__(self, table_name, schema=None, columns=None, options=None):
        self.table_name = table_name
        self.schema = schema
        self.columns = columns or []
        self.options = options or {}


class Qual:
    def __init__(self, field_name, operator, value):
        self.field_name = field_name
        self.operator = operator
        self.value = value

    @property
    def is_list_operator(self):
        return isinstance(self.operator, tuple)


class SortKey:
    def __init__(self, attname, attnum=0, is_reversed=False, nulls_first=False, collate=None):
        self.attname = attname
        self.attnum = attnum
        self.is_reversed = is_reversed
        self.nulls_first = nulls_first
        self.collate = collate


def log_to_postgres(message, level=logging.INFO, hint=None, detail=None):
    _log.append((level, message, hint))


multicorn = types.ModuleType("multicorn")
multicorn.ForeignDataWrapper = ForeignDataWrapper
multicorn.ColumnDefinition = ColumnDefinition
multicorn.TableDefinition = TableDefinition
multicorn.Qual = Qual
multicorn.SortKey = SortKey
utils = types.ModuleType("multicorn.utils")
utils.log_to_postgres = log_to_postgres
multicorn.utils = utils
sys.modules.setdefault("multicorn", multicorn)
sys.modules.setdefault("multicorn.utils", utils)


import pytest  # noqa: E402


@pytest.fixture
def pg_log():
    _log.clear()
    return _log
