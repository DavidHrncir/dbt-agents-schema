"""DuckDB dialect: double-quoted identifiers."""

from __future__ import annotations

from dbt_agents_schema.dialects import Dialect


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


DIALECT = Dialect(
    name="duckdb",
    types={
        "varchar": "VARCHAR",
        "text": "VARCHAR",
        "boolean": "BOOLEAN",
        "integer": "BIGINT",
        "timestamp": "TIMESTAMPTZ",
        "json": "JSON",
    },
    _quote=_quote,
)
