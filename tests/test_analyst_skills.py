"""Built-in analyst skill bodies (SPEC 9, PLAN T21)."""

from __future__ import annotations

import re

import pytest
import sqlglot
from sqlglot import exp

from dbt_agents_schema.plan import analyst_skill

DIALECTS = ["snowflake", "bigquery", "databricks", "duckdb"]
SQL_BLOCK = re.compile(r"```sql\n(.*?)```", re.DOTALL)


def sql_blocks(warehouse: str) -> list[str]:
    return [b.strip() for b in SQL_BLOCK.findall(analyst_skill(warehouse))]


@pytest.mark.parametrize("warehouse", DIALECTS)
def test_every_sql_block_parses_in_its_dialect(warehouse: str) -> None:
    blocks = sql_blocks(warehouse)
    assert len(blocks) >= 8, f"{warehouse}: expected the full set of examples"
    for block in blocks:
        statements = [s for s in sqlglot.parse(block, read=warehouse) if s is not None]
        assert len(statements) == 1, f"{warehouse}: one statement per block:\n{block}"
        # Read-only: every example is a query, never DML or DDL.
        assert isinstance(statements[0], exp.Query), f"{warehouse}: not a SELECT:\n{block}"


@pytest.mark.parametrize("warehouse", DIALECTS)
def test_body_covers_spec_9(warehouse: str) -> None:
    body = analyst_skill(warehouse)
    lowered = body.lower()
    assert "read-only" in lowered and "select" in lowered
    assert "<!--" not in body, "placeholder comment left in the body"
    joined = "\n".join(sql_blocks(warehouse))
    # Discovery order: ROOT before DBT_PROJECT.
    assert joined.index("FROM ROOT") < joined.index("FROM DBT_PROJECT")
    assert "project = 'finance'" in joined
    assert "WITH RECURSIVE" in joined
    assert joined.count("WITH RECURSIVE") >= 2, "upstream and downstream lineage"
    assert "DBT_DEPENDENCY" in joined and "unique_id" in joined
    assert "DBT_COLUMN" in joined
    assert "LIKE 'skill/%'" in joined
    assert "FROM SKILL_USE" in joined


def test_each_dialect_has_its_own_body() -> None:
    bodies = {w: analyst_skill(w) for w in DIALECTS}
    assert len(set(bodies.values())) == len(DIALECTS)
    for warehouse, body in bodies.items():
        assert f"({warehouse})" in body.splitlines()[0].lower()
