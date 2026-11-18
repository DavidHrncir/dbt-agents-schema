"""Open the writer for a resolved connection and target."""

from __future__ import annotations

from dbt_agents_schema.config import ResolvedTarget
from dbt_agents_schema.credentials import Connection, CredentialError
from dbt_agents_schema.writers import Writer

# Warehouses with a writer in this version. Phase 3 adds snowflake, bigquery, databricks.
AVAILABLE_WRITERS: tuple[str, ...] = ("duckdb",)


def ensure_writer_available(warehouse: str) -> None:
    if warehouse not in AVAILABLE_WRITERS:
        raise CredentialError(
            f"no {warehouse} writer in this version of dbt-agents-schema; available: "
            f"{', '.join(AVAILABLE_WRITERS)}. Use --dry-run to preview the plan"
        )


def open_writer(connection: Connection, target: ResolvedTarget) -> Writer:
    ensure_writer_available(connection.warehouse)
    from dbt_agents_schema.writers.duckdb import DuckDBWriter

    return DuckDBWriter.connect(
        str(connection.settings["path"]), database=target.database, schema=target.schema
    )
