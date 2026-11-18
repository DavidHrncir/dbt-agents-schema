"""T13 scenarios against a real Snowflake account (T16).

Runs only with `-m live` and `LIVE_SNOWFLAKE_CREDENTIALS` set to a
`WAREHOUSE_CREDENTIALS`-style document (SPEC 4.1); otherwise every test skips.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator, Sequence
from typing import Any

import pytest
from live.harness import SCENARIOS, LiveTarget, live_schema_name

VAR = "LIVE_SNOWFLAKE_CREDENTIALS"

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(not os.environ.get(VAR), reason=f"{VAR} is not set"),
]


@pytest.fixture
def target() -> Iterator[LiveTarget]:
    from dbt_agents_schema.credentials import from_env
    from dbt_agents_schema.dialects.snowflake import DIALECT
    from dbt_agents_schema.writers.snowflake import SnowflakeWriter

    settings = from_env({"WAREHOUSE_CREDENTIALS": os.environ[VAR]}).settings
    database = str(settings["database"])
    schema = live_schema_name()
    admin = SnowflakeWriter.connect(settings, schema=schema)
    con = admin._con

    def query(sql: str, params: Sequence[Any]) -> list[tuple[Any, ...]]:
        cur = con.cursor()
        cur.execute(sql, list(params))
        try:
            return [tuple(r) for r in cur.fetchall()]
        except Exception:  # statements without a result set
            return []

    def table(name: str) -> str:
        return DIALECT.qualified(database, schema, name.upper())

    def col(name: str) -> str:
        return DIALECT.quote(name.upper())

    open_writer: Callable[[], Any] = lambda: SnowflakeWriter.connect(settings, schema=schema)  # noqa: E731
    try:
        yield LiveTarget(
            warehouse="snowflake",
            open_writer=open_writer,
            query=query,
            table=table,
            col=col,
            starts_with=lambda c: f"STARTSWITH({c}, ?)",
            schema_ref=DIALECT.qualified(database, schema),
            json_type="VARIANT",
        )
    finally:
        query(f"DROP SCHEMA IF EXISTS {DIALECT.qualified(database, schema)}", [])
        admin.close()


@pytest.mark.parametrize("name", list(SCENARIOS))
def test_scenario(target: LiveTarget, name: str) -> None:
    SCENARIOS[name](target)
