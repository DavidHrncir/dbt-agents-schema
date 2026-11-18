"""Databricks dialect: backtick-quoted identifiers, backticks doubled."""

from __future__ import annotations

from dbt_agents_schema.dialects import Dialect


def _quote(identifier: str) -> str:
    return "`" + identifier.replace("`", "``") + "`"


DIALECT = Dialect(
    name="databricks",
    types={
        "varchar": "STRING",
        "text": "STRING",
        "boolean": "BOOLEAN",
        "integer": "BIGINT",
        "timestamp": "TIMESTAMP",
        "json": "STRING",
    },
    _quote=_quote,
)
