"""Snowflake dialect: double-quoted identifiers (case is preserved as given)."""

from __future__ import annotations

from dbt_agents_schema.dialects import Dialect


def _quote(identifier: str) -> str:
    return '"' + identifier.replace('"', '""') + '"'


DIALECT = Dialect(
    name="snowflake",
    types={
        "varchar": "VARCHAR",
        "text": "VARCHAR",
        "boolean": "BOOLEAN",
        "integer": "NUMBER(38,0)",
        "timestamp": "TIMESTAMP_TZ",
        "json": "VARIANT",
    },
    _quote=_quote,
)
