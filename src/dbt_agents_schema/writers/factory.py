"""Open the writer for a resolved connection and target."""

from __future__ import annotations

from dbt_agents_schema.config import ResolvedTarget
from dbt_agents_schema.credentials import Connection, CredentialError
from dbt_agents_schema.writers import Writer

# Warehouses with a writer in this version.
AVAILABLE_WRITERS: tuple[str, ...] = ("duckdb", "snowflake", "bigquery", "databricks")


def ensure_writer_available(warehouse: str) -> None:
    if warehouse not in AVAILABLE_WRITERS:
        raise CredentialError(
            f"no {warehouse} writer in this version of dbt-agents-schema; available: "
            f"{', '.join(AVAILABLE_WRITERS)}. Use --dry-run to preview the plan"
        )


def open_writer(connection: Connection, target: ResolvedTarget) -> Writer:
    ensure_writer_available(connection.warehouse)
    if connection.warehouse == "snowflake":
        from dbt_agents_schema.writers.snowflake import SnowflakeWriter

        return SnowflakeWriter.connect(
            connection.settings, database=target.database, schema=target.schema
        )
    if connection.warehouse == "bigquery":
        from dbt_agents_schema.writers.bigquery import BigQueryWriter

        return BigQueryWriter.connect(
            connection.settings, database=target.database, dataset=target.schema
        )
    if connection.warehouse == "databricks":
        from dbt_agents_schema.writers.databricks import DatabricksWriter

        return DatabricksWriter.connect(
            connection.settings, database=target.database, schema=target.schema
        )
    from dbt_agents_schema.writers.duckdb import DuckDBWriter

    return DuckDBWriter.connect(
        str(connection.settings["path"]), database=target.database, schema=target.schema
    )
