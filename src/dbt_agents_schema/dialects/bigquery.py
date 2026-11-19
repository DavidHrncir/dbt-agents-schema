"""BigQuery dialect: backtick-quoted identifiers with backslash escapes."""

from __future__ import annotations

from dbt_agents_schema.dialects import Dialect


def _quote(identifier: str) -> str:
    return "`" + identifier.replace("\\", "\\\\").replace("`", "\\`") + "`"


DIALECT = Dialect(
    name="bigquery",
    types={
        "varchar": "STRING",
        "text": "STRING",
        "boolean": "BOOL",
        "integer": "INT64",
        "timestamp": "TIMESTAMP",
        "json": "JSON",
    },
    _quote=_quote,
)
