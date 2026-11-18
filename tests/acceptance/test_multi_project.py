"""Acceptance: many dbt projects share one agents schema (PLAN T13).

This is the project's definition of done. Never weaken it; only add scenarios.

Runs the publish pipeline (load, check, plan, guards, swap) against a DuckDB file.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fakes import MANIFESTS, PROJECTS, create_dbt_labs_tables

from dbt_agents_schema.config import ResolvedTarget
from dbt_agents_schema.plan import PROJECT_TABLES, TABLES
from dbt_agents_schema.publish import GitInfo, PublishOutcome, publish, unpublish
from dbt_agents_schema.writers.duckdb import DuckDBWriter

SCHEMA = "AGENTS"
REPOS = {
    "finance": "https://github.com/example/finance",
    "finance_v2": "https://github.com/example/finance",
    "marketing": "https://github.com/example/marketing",
}


class Publisher:
    """Runs `publish` for a fixture project against one DuckDB file; keeps its output."""

    def __init__(self, db: Path) -> None:
        self.db = db
        self.output: dict[str, str] = {}

    def __call__(
        self, name: str, *, repo_url: str | None = None, force_takeover: bool = False
    ) -> PublishOutcome:
        out, err = io.StringIO(), io.StringIO()
        outcome = publish(
            PROJECTS / name,
            env="prod",
            manifest_path=MANIFESTS / f"{name}.json",
            warehouse="duckdb",
            writer_factory=lambda t: factory(self.db, t),
            git=GitInfo(repo_url=repo_url or REPOS[name], sha="a" * 40, branch="main"),
            now=lambda: datetime(2026, 11, 17, 12, 0, tzinfo=UTC),
            force_takeover=force_takeover,
            out=out,
            err=err,
        )
        self.output[name] = out.getvalue() + err.getvalue()
        print(f"--- publish {name} -> exit {outcome.exit_code}\n{self.output[name]}")
        return outcome


def factory(db: Path, target: ResolvedTarget) -> DuckDBWriter:
    return DuckDBWriter.connect(str(db), database=target.database, schema=target.schema)


@pytest.fixture
def db(tmp_path: Path) -> Path:
    return tmp_path / "warehouse.duckdb"


@pytest.fixture
def do_publish(db: Path) -> Publisher:
    return Publisher(db)


def snapshot(db: Path, project: str | None = None) -> dict[str, list[tuple[Any, ...]]]:
    """Every row of every table (optionally one project's rows), sorted."""
    c = duckdb.connect(str(db))
    try:
        result: dict[str, list[tuple[Any, ...]]] = {}
        for table in TABLES:
            sql = f'SELECT * FROM "{SCHEMA}"."{table}"'
            params: list[Any] = []
            if project is not None:
                if table == "ROOT":
                    sql += " WHERE key = ? OR starts_with(key, ?)"
                    params = [f"project/{project}", f"skill/{project}/"]
                else:
                    sql += " WHERE project = ?"
                    params = [project]
            result[table] = sorted(c.execute(sql, params).fetchall(), key=repr)
        return result
    finally:
        c.close()


def query(db: Path, sql: str, params: list[Any] | None = None) -> list[tuple[Any, ...]]:
    c = duckdb.connect(str(db))
    try:
        return c.execute(sql, params or []).fetchall()
    finally:
        c.close()


def projects_in(db: Path, table: str) -> set[str | None]:
    return {r[0] for r in query(db, f'SELECT DISTINCT project FROM "{SCHEMA}"."{table}"')}


def root_keys(db: Path) -> set[str]:
    return {r[0] for r in query(db, f'SELECT key FROM "{SCHEMA}"."ROOT"')}


# --- scenario 1 ---------------------------------------------------------------------------


def test_1_two_projects_share_one_schema(db: Path, do_publish: Publisher) -> None:
    assert do_publish("finance").exit_code == 0
    assert do_publish("marketing").exit_code == 0

    for table in PROJECT_TABLES:
        assert projects_in(db, table) == {"finance", "marketing"}, table
    keys = root_keys(db)
    assert {"project/finance", "project/marketing"} <= keys
    assert {
        "skill/finance/revenue_overview",
        "skill/finance/payments/reconciliation",
        "skill/marketing/attribution",
        "skill/marketing/campaigns/weekly_review",
    } <= keys
    shared = query(db, f'SELECT key, count(*) FROM "{SCHEMA}"."ROOT" GROUP BY key')
    assert all(n == 1 for _, n in shared)


# --- scenario 2 ---------------------------------------------------------------------------


def test_2_republish_one_project_leaves_the_other_untouched(
    db: Path, do_publish: Publisher
) -> None:
    do_publish("finance")
    do_publish("marketing")
    marketing_before = snapshot(db, "marketing")
    finance_before = snapshot(db, "finance")

    assert do_publish("finance_v2").exit_code == 0

    assert snapshot(db, "marketing") == marketing_before  # byte-identical

    def finance_set(table: str, column: str) -> set[Any]:
        sql = f'SELECT {column} FROM "{SCHEMA}"."{table}" WHERE project = ?'
        return {r[0] for r in query(db, sql, ["finance"])}

    models_before = {r[1] for r in finance_before["DBT_MODEL"]}
    models_after = finance_set("DBT_MODEL", "unique_id")
    removed = models_before - models_after
    assert len(removed) == 1
    # Removed model: no columns, no dependency edges as downstream.
    assert not finance_set("DBT_COLUMN", "model_id") & removed
    assert not finance_set("DBT_DEPENDENCY", "downstream_id") & removed

    cols_before = {(r[1], r[2]) for r in finance_before["DBT_COLUMN"]}
    cols_after = {
        (m, c)
        for m, c in query(
            db,
            f'SELECT model_id, column_name FROM "{SCHEMA}"."DBT_COLUMN" WHERE project = ?',
            ["finance"],
        )
    }
    assert {c for c in cols_after - cols_before if c[0] not in removed}  # new column present

    keys = root_keys(db)
    assert "skill/finance/payments/reconciliation" not in keys
    assert "skill/finance/revenue_overview" in keys
    assert "skill/finance/payments/reconciliation" not in finance_set("SKILL_USE", "skill_key")
    assert finance_before["SKILL_USE"] and any(
        r[2] == "skill/finance/payments/reconciliation" for r in finance_before["SKILL_USE"]
    )
    project = query(
        db,
        f'SELECT model_count, skill_count FROM "{SCHEMA}"."DBT_PROJECT" '
        "WHERE project = 'finance'",
    )
    assert project == [(len(models_after), 1)]


# --- scenario 3 ---------------------------------------------------------------------------


def test_3_ownership_guard(db: Path, do_publish: Publisher) -> None:
    do_publish("finance")
    before = snapshot(db)

    other = "https://gitlab.example.com/someone-else/finance"
    refused = do_publish("finance", repo_url=other)
    assert refused.exit_code == 4
    assert "--force-takeover" in do_publish.output["finance"]
    assert snapshot(db) == before

    taken = do_publish("finance", repo_url=other, force_takeover=True)
    assert taken.exit_code == 0
    repo = query(db, f'SELECT repo_url FROM "{SCHEMA}"."DBT_PROJECT" WHERE project = \'finance\'')
    assert repo == [(other,)]


# --- scenario 4 ---------------------------------------------------------------------------


def test_4_migrates_dbt_labs_tables(db: Path, do_publish: Publisher) -> None:
    c = duckdb.connect(str(db))
    legacy = create_dbt_labs_tables(c, SCHEMA)
    c.close()
    total_legacy = sum(legacy.values())

    outcome = do_publish("finance")
    assert outcome.exit_code == 0

    assert outcome.ensure is not None
    assert "project" in outcome.ensure.columns_added["DBT_MODEL"]
    assert "upstream_project" in outcome.ensure.columns_added["DBT_DEPENDENCY"]
    for table in PROJECT_TABLES:
        assert None not in projects_in(db, table), table
        assert projects_in(db, table) == {"finance"}
    assert outcome.swap is not None
    assert outcome.swap.total_legacy_removed == total_legacy
    output = do_publish.output["finance"]
    assert f"legacy rows removed: {total_legacy}" in output
    assert "skill/legacy" in root_keys(db)  # ROOT rows outside our keys are kept


# --- scenario 5 ---------------------------------------------------------------------------


def test_5_newer_spec_version_refuses(db: Path, do_publish: Publisher) -> None:
    do_publish("marketing")
    c = duckdb.connect(str(db))
    c.execute(f'UPDATE "{SCHEMA}"."DBT_PROJECT" SET spec_version = 2')
    c.close()
    before = snapshot(db)

    outcome = do_publish("finance")
    assert outcome.exit_code == 2
    assert "spec_version 2" in do_publish.output["finance"]
    assert snapshot(db) == before


# --- scenario 6 ---------------------------------------------------------------------------


def test_6_unpublish_removes_only_that_project(db: Path, do_publish: Publisher) -> None:
    do_publish("finance")
    finance_alone = snapshot(db)
    do_publish("marketing")

    out, err = io.StringIO(), io.StringIO()
    code = unpublish(
        PROJECTS / "marketing",
        env="prod",
        warehouse="duckdb",
        writer_factory=lambda t: factory(db, t),
        out=out,
        err=err,
    )
    print(out.getvalue() + err.getvalue())
    assert code == 0
    for table in PROJECT_TABLES:
        assert projects_in(db, table) == {"finance"}
    keys = root_keys(db)
    assert "project/marketing" not in keys
    assert not any(k.startswith("skill/marketing/") for k in keys)
    assert snapshot(db) == finance_alone
