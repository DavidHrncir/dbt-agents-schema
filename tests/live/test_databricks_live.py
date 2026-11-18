"""T13 scenarios against a real Databricks SQL warehouse (T18).

Runs only with `-m live` and `LIVE_DATABRICKS_CREDENTIALS` set to a
`WAREHOUSE_CREDENTIALS`-style document (SPEC 4.1); otherwise every test skips.
"""

from __future__ import annotations

import os
from collections.abc import Iterator, Sequence
from typing import Any

import pytest
from live.harness import SCENARIOS, LiveTarget, live_schema_name

VAR = "LIVE_DATABRICKS_CREDENTIALS"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get(VAR), reason=f"{VAR} is not set"),
]


@pytest.fixture
def target() -> Iterator[LiveTarget]:
    from dbt_agents_schema.credentials import from_env
    from dbt_agents_schema.dialects.databricks import DIALECT
    from dbt_agents_schema.writers.databricks import DatabricksWriter

    settings = from_env({"WAREHOUSE_CREDENTIALS": os.environ[VAR]}).settings
    catalog = str(settings["catalog"])
    schema = live_schema_name().lower()
    admin = DatabricksWriter.connect(settings, schema=schema)
    con = admin._con

    def query(sql: str, params: Sequence[Any]) -> list[tuple[Any, ...]]:
        parts = sql.split("?")
        text = parts[0] + "".join(f":p{i}{part}" for i, part in enumerate(parts[1:]))
        cur = con.cursor()
        cur.execute(text, {f"p{i}": v for i, v in enumerate(params)})
        try:
            return [tuple(r) for r in cur.fetchall()]
        except Exception:  # statements without a result set
            return []

    try:
        yield LiveTarget(
            warehouse="databricks",
            open_writer=lambda: DatabricksWriter.connect(settings, schema=schema),
            query=query,
            table=lambda name: DIALECT.qualified(catalog, schema, name),
            col=DIALECT.quote,
            starts_with=lambda c: f"startswith({c}, ?)",
        )
    finally:
        query(f"DROP SCHEMA IF EXISTS {DIALECT.qualified(catalog, schema)} CASCADE", [])
        admin.close()


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario(target: LiveTarget, name: str) -> None:
    SCENARIOS[name](target)
