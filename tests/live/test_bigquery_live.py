"""T13 scenarios against real BigQuery (T17).

Runs only with `-m live` and `LIVE_BIGQUERY_CREDENTIALS` set to a
`WAREHOUSE_CREDENTIALS`-style document (SPEC 4.1); otherwise every test skips.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from live.harness import SCENARIOS, LiveTarget, live_schema_name

VAR = "LIVE_BIGQUERY_CREDENTIALS"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get(VAR), reason=f"{VAR} is not set"),
]


@pytest.fixture
def target() -> Iterator[LiveTarget]:
    from google.cloud import bigquery

    from dbt_agents_schema.credentials import from_env
    from dbt_agents_schema.dialects.bigquery import DIALECT
    from dbt_agents_schema.writers.bigquery import BigQueryWriter

    settings = from_env({"WAREHOUSE_CREDENTIALS": os.environ[VAR]}).settings
    project = str(settings["project_id"])
    dataset = live_schema_name()
    admin = BigQueryWriter.connect(settings, dataset=dataset)
    client = admin._client

    def query(sql: str, params: Sequence[Any]) -> list[tuple[Any, ...]]:
        parts = sql.split("?")
        text = parts[0] + "".join(f"@p{i}{part}" for i, part in enumerate(parts[1:]))
        config = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter(f"p{i}", "STRING", v) for i, v in enumerate(params)
            ]
        )
        job = client.query(text, job_config=config, location=settings.get("location"))
        return [tuple(r.values()) for r in job.result()]

    try:
        yield LiveTarget(
            warehouse="bigquery",
            open_writer=lambda: BigQueryWriter.connect(settings, dataset=dataset),
            query=query,
            table=lambda name: DIALECT.qualified(project, dataset, name),
            col=DIALECT.quote,
            starts_with=lambda c: f"STARTS_WITH({c}, ?)",
        )
    finally:
        query(f"DROP SCHEMA IF EXISTS {DIALECT.qualified(project, dataset)} CASCADE", [])
        admin.close()


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario(target: LiveTarget, name: str) -> None:
    SCENARIOS[name](target)
