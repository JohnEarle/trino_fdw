"""PostgreSQL foreign data wrapper for Trino, built on Multicorn2.

Usage from SQL::

    CREATE EXTENSION multicorn;
    CREATE SERVER trino FOREIGN DATA WRAPPER multicorn OPTIONS (
        wrapper 'trino_fdw.TrinoFDW',
        host 'trino.example.internal', port '8443',
        catalog 'hive', schema 'prod',
        auth 'password', user 'svc_fdw',
        password_file '/projected/trino/password',
        ca_file '/projected/trino/ca.pem'
    );

Secrets are never accepted as SQL options; see :mod:`trino_fdw.options`.
"""

from trino_fdw.fdw import TrinoFDW

__all__ = ["TrinoFDW"]
__version__ = "0.1.0"
