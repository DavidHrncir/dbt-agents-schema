"""SQL dialects: identifier quoting and logical-to-warehouse type maps (SPEC 5).

One module per warehouse. Each exposes `DIALECT`, the only place that warehouse's
identifiers are quoted and its column types are named.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from dbt_agents_schema.plan import WAREHOUSES

DIALECTS = WAREHOUSES

LOGICAL = ("varchar", "text", "boolean", "integer", "timestamp", "json")

_T = "text"
_V = "varchar"

# Logical type of every column, in TABLE_COLUMNS order (SPEC 5.1-5.6).
LOGICAL_TYPES: Mapping[str, Mapping[str, str]] = {
    "ROOT": {"provider": _V, "key": _V, "content": _T},
    "DBT_PROJECT": {
        "project": _V,
        "dbt_project_name": _V,
        "owner": _V,
        "description": _T,
        "environment": _V,
        "repo_url": _V,
        "git_sha": _V,
        "git_branch": _V,
        "dbt_version": _V,
        "manifest_generated_at": "timestamp",
        "publisher_version": _V,
        "spec_version": "integer",
        "published_at": "timestamp",
        "model_count": "integer",
        "column_count": "integer",
        "dependency_count": "integer",
        "skill_count": "integer",
    },
    "DBT_MODEL": {
        "project": _V,
        "unique_id": _V,
        "name": _V,
        "package_name": _V,
        "database_name": _V,
        "schema_name": _V,
        "alias": _V,
        "relation_name": _V,
        "materialization": _V,
        "access": _V,
        "description": _T,
        "file_path": _V,
        "tags": "json",
        "meta": "json",
    },
    "DBT_COLUMN": {
        "project": _V,
        "model_id": _V,
        "column_name": _V,
        "data_type": _V,
        "description": _T,
        "meta": "json",
    },
    "DBT_DEPENDENCY": {
        "project": _V,
        "upstream_id": _V,
        "downstream_id": _V,
        "upstream_type": _V,
        "downstream_type": _V,
        "upstream_project": _V,
    },
    "SKILL_USE": {
        "project": _V,
        "provider": _V,
        "skill_key": _V,
        "use_kind": _V,
        "object_ref": _V,
    },
}


@dataclass(frozen=True)
class Dialect:
    """Quoting and type naming for one warehouse."""

    name: str
    types: Mapping[str, str]
    _quote: Callable[[str], str]

    def quote(self, identifier: str) -> str:
        """Quote one identifier part. Never pass values through this."""
        if not identifier:
            raise ValueError(f"{self.name}: empty SQL identifier")
        if "\x00" in identifier:
            raise ValueError(f"{self.name}: SQL identifier contains a NUL byte: {identifier!r}")
        return self._quote(identifier)

    def qualified(self, *parts: str | None) -> str:
        """Dot-join the quoted non-empty parts, e.g. database, schema, table."""
        return ".".join(self.quote(p) for p in parts if p)

    def column_type(self, table: str, column: str) -> str:
        """Warehouse type for a column of one of the six tables."""
        return self.types[LOGICAL_TYPES[table][column]]


def get_dialect(name: str) -> Dialect:
    if name not in DIALECTS:
        raise ValueError(f"unknown SQL dialect {name!r}; expected one of: {', '.join(DIALECTS)}")
    module = importlib.import_module(f"dbt_agents_schema.dialects.{name}")
    dialect: Dialect = module.DIALECT
    return dialect
