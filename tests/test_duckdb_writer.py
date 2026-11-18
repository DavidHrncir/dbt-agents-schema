"""DuckDB writer (SPEC 6; T12)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fakes import DBT_LABS_TABLES, create_dbt_labs_tables, fixture_plan

from dbt_agents_schema.plan import PROJECT_TABLES, TABLE_COLUMNS, TABLES, Plan
from dbt_agents_schema.writers import WarehouseError, Writer
from dbt_agents_schema.writers.duckdb import DuckDBWriter

SCHEMA = "AGENTS"


@pytest.fixture
def con() -> Iterator[duckdb.DuckDBPyConnection]:
    c = duckdb.connect(":memory:")
    yield c
    c.close()


@pytest.fixture
def writer(con: duckdb.DuckDBPyConnection) -> DuckDBWriter:
    return DuckDBWriter(con, database=None, schema=SCHEMA)


def rows(con: duckdb.DuckDBPyConnection, table: str, where: str = "") -> list[dict[str, Any]]:
    cur = con.execute(f'SELECT * FROM "{SCHEMA}"."{table}" {where}')
    names = [d[0] for d in cur.description]
    return [dict(zip(names, r, strict=True)) for r in cur.fetchall()]


def columns(con: duckdb.DuckDBPyConnection, table: str) -> list[str]:
    return [
        r[0]
        for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = ? ORDER BY ordinal_position",
            [SCHEMA, table],
        ).fetchall()
    ]


def project_rows(con: duckdb.DuckDBPyConnection, project: str) -> dict[str, int]:
    return {
        t: con.execute(
            f'SELECT count(*) FROM "{SCHEMA}"."{t}" WHERE project = ?', [project]
        ).fetchone()[0]  # type: ignore[index]
        for t in PROJECT_TABLES
    }


def test_is_a_writer(writer: DuckDBWriter) -> None:
    assert isinstance(writer, Writer)


# --- structure ---------------------------------------------------------------------------


def test_ensure_structure_creates_schema_and_tables(
    con: duckdb.DuckDBPyConnection, writer: DuckDBWriter
) -> None:
    result = writer.ensure_structure()
    assert result.columns_added == {}
    for table in TABLES:
        assert columns(con, table) == list(TABLE_COLUMNS[table])
    types = dict(
        con.execute(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_schema = ? AND table_name = 'DBT_PROJECT'",
            [SCHEMA],
        ).fetchall()
    )
    assert types["published_at"] == "TIMESTAMP WITH TIME ZONE"
    assert types["spec_version"] == "BIGINT"


def test_ensure_structure_is_idempotent(writer: DuckDBWriter) -> None:
    writer.ensure_structure()
    assert writer.ensure_structure().columns_added == {}


def test_ensure_structure_migrates_dbt_labs_tables(
    con: duckdb.DuckDBPyConnection, writer: DuckDBWriter
) -> None:
    create_dbt_labs_tables(con, SCHEMA)
    result = writer.ensure_structure()
    for table, legacy in DBT_LABS_TABLES.items():
        expected = tuple(c for c in TABLE_COLUMNS[table] if c not in legacy)
        assert tuple(result.columns_added.get(table, ())) == expected
        assert set(columns(con, table)) == set(TABLE_COLUMNS[table])
    assert "DBT_PROJECT" not in result.columns_added  # newly created, not migrated
    assert result.columns_added["DBT_MODEL"] == (
        "project",
        "package_name",
        "alias",
        "relation_name",
        "access",
    )
    # Never drops anything: legacy rows are still there until swap.
    assert len(rows(con, "DBT_MODEL")) == 2


def test_ensure_structure_keeps_extra_columns(
    con: duckdb.DuckDBPyConnection, writer: DuckDBWriter
) -> None:
    con.execute(f'CREATE SCHEMA "{SCHEMA}"')
    con.execute(f'CREATE TABLE "{SCHEMA}"."ROOT" (provider VARCHAR, key VARCHAR, extra INT)')
    result = writer.ensure_structure()
    assert result.columns_added["ROOT"] == ("content",)
    assert "extra" in columns(con, "ROOT")


def test_ensure_structure_matches_existing_names_case_insensitively(
    con: duckdb.DuckDBPyConnection, writer: DuckDBWriter
) -> None:
    con.execute(f'CREATE SCHEMA "{SCHEMA}"')
    con.execute(f'CREATE TABLE "{SCHEMA}"."root" ("PROVIDER" VARCHAR, "KEY" VARCHAR)')
    result = writer.ensure_structure()
    assert result.columns_added["ROOT"] == ("content",)


def test_explicit_database(tmp_path: Path) -> None:
    con = duckdb.connect(str(tmp_path / "agents.duckdb"))
    try:
        w = DuckDBWriter(con, database="agents", schema=SCHEMA)
        w.ensure_structure()
        w.swap(fixture_plan("finance"))
        n = con.execute(f'SELECT count(*) FROM agents."{SCHEMA}"."DBT_PROJECT"').fetchone()
        assert n == (1,)
    finally:
        con.close()


# --- swap ---------------------------------------------------------------------------------


def test_swap_writes_every_plan_row(con: duckdb.DuckDBPyConnection, writer: DuckDBWriter) -> None:
    plan = fixture_plan("finance")
    writer.ensure_structure()
    result = writer.swap(plan)
    assert dict(result.rows_written) == plan.counts()
    assert result.total_legacy_removed == 0
    for table in TABLES:
        assert len(rows(con, table)) == len(plan.rows(table))


def test_swap_round_trips_values(con: duckdb.DuckDBPyConnection, writer: DuckDBWriter) -> None:
    plan = fixture_plan("finance")
    writer.ensure_structure()
    writer.swap(plan)
    model = plan.rows("DBT_MODEL")[0]
    stored = rows(con, "DBT_MODEL", f"WHERE unique_id = '{model['unique_id']}'")[0]
    assert json.loads(stored["tags"]) == model["tags"]
    assert json.loads(stored["meta"]) == model["meta"]
    assert stored["description"] == model["description"]
    project = writer.read_project_row("finance")
    assert project is not None
    expected = plan.rows("DBT_PROJECT")[0]
    assert project["published_at"] == expected["published_at"]
    assert project["spec_version"] == 1
    assert project["repo_url"] == expected["repo_url"]
    assert project["model_count"] == expected["model_count"]


def test_swap_replaces_own_rows_only(con: duckdb.DuckDBPyConnection, writer: DuckDBWriter) -> None:
    writer.ensure_structure()
    writer.swap(fixture_plan("finance"))
    writer.swap(fixture_plan("marketing"))
    before_mkt = {t: rows(con, t, "WHERE project = 'marketing'") for t in PROJECT_TABLES}
    v2 = fixture_plan("finance_v2", project_dir="finance_v2")
    writer.swap(v2)
    assert project_rows(con, "finance") == {t: len(v2.rows(t)) for t in PROJECT_TABLES}
    for t in PROJECT_TABLES:
        assert rows(con, t, "WHERE project = 'marketing'") == before_mkt[t]
    keys = {r["key"] for r in rows(con, "ROOT")}
    assert "project/marketing" in keys
    assert {r["key"] for r in v2.rows("ROOT")} <= keys


def test_swap_twice_is_stable(con: duckdb.DuckDBPyConnection, writer: DuckDBWriter) -> None:
    writer.ensure_structure()
    plan = fixture_plan("finance")
    writer.swap(plan)
    first = {t: sorted(map(str, rows(con, t))) for t in TABLES}
    writer.swap(plan)
    assert {t: sorted(map(str, rows(con, t))) for t in TABLES} == first


def test_swap_removes_legacy_rows_and_counts_them(
    con: duckdb.DuckDBPyConnection, writer: DuckDBWriter
) -> None:
    legacy = create_dbt_labs_tables(con, SCHEMA)
    writer.ensure_structure()
    result = writer.swap(fixture_plan("finance"))
    assert {t: n for t, n in result.legacy_rows_removed.items() if n} == legacy
    for t in PROJECT_TABLES:
        assert rows(con, t, "WHERE project IS NULL") == []
    keys = {r["key"] for r in rows(con, "ROOT")}
    assert "skill/legacy" in keys  # ROOT keys outside this tool's ownership survive
    overview = [r for r in rows(con, "ROOT") if r["key"] == "overview"]
    assert len(overview) == 1


def test_swap_root_prefix_is_literal(con: duckdb.DuckDBPyConnection, writer: DuckDBWriter) -> None:
    writer.ensure_structure()
    con.execute(
        f"INSERT INTO \"{SCHEMA}\".\"ROOT\" VALUES ('dbt', 'skill/financeX/a', 'x'), "
        "('other', 'skill/finance/a', 'y'), ('dbt', 'skill/finance/stale', 'z')"
    )
    writer.swap(fixture_plan("finance"))
    keys = {(r["provider"], r["key"]) for r in rows(con, "ROOT")}
    assert ("dbt", "skill/financeX/a") in keys
    assert ("other", "skill/finance/a") in keys
    assert ("dbt", "skill/finance/stale") not in keys


def test_swap_rolls_back_when_insert_fails(
    con: duckdb.DuckDBPyConnection, writer: DuckDBWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer.ensure_structure()
    writer.swap(fixture_plan("finance"))
    before = {t: sorted(map(str, rows(con, t))) for t in TABLES}
    calls: list[str] = []

    def boom(table: str, plan_rows: Any) -> None:
        calls.append(table)
        raise duckdb.Error("injected failure between delete and insert")

    monkeypatch.setattr(writer, "_insert", boom)
    with pytest.raises(WarehouseError, match="injected failure"):
        writer.swap(fixture_plan("finance_v2", project_dir="finance_v2"))
    assert calls  # the deletes ran first
    assert {t: sorted(map(str, rows(con, t))) for t in TABLES} == before


def test_swap_rolls_back_on_non_database_error(
    con: duckdb.DuckDBPyConnection, writer: DuckDBWriter, monkeypatch: pytest.MonkeyPatch
) -> None:
    writer.ensure_structure()
    writer.swap(fixture_plan("finance"))
    before = project_rows(con, "finance")

    def boom(table: str, plan_rows: Any) -> None:
        raise RuntimeError("bug")

    monkeypatch.setattr(writer, "_insert", boom)
    with pytest.raises(RuntimeError):
        writer.swap(fixture_plan("finance"))
    assert project_rows(con, "finance") == before
    # The connection is usable afterwards.
    monkeypatch.undo()
    writer.swap(fixture_plan("marketing"))


def test_values_are_bound_not_interpolated(
    con: duckdb.DuckDBPyConnection, writer: DuckDBWriter
) -> None:
    plan = fixture_plan("finance")
    evil = 'x\'); DROP TABLE "AGENTS"."ROOT"; --'
    root = tuple(
        {**r, "content": evil} if r["key"] == "project/finance" else r for r in plan.rows("ROOT")
    )
    writer.ensure_structure()
    writer.swap(Plan(plan.project, plan.warehouse, {**plan.tables, "ROOT": root}))
    stored = rows(con, "ROOT", "WHERE key = 'project/finance'")
    assert stored[0]["content"] == evil


# --- reads --------------------------------------------------------------------------------


def test_read_project_row_missing(writer: DuckDBWriter) -> None:
    writer.ensure_structure()
    assert writer.read_project_row("finance") is None
    assert writer.max_spec_version() is None


def test_max_spec_version(con: duckdb.DuckDBPyConnection, writer: DuckDBWriter) -> None:
    writer.ensure_structure()
    writer.swap(fixture_plan("finance"))
    assert writer.max_spec_version() == 1
    con.execute(f'UPDATE "{SCHEMA}"."DBT_PROJECT" SET spec_version = 2')
    assert writer.max_spec_version() == 2


def test_reads_before_structure_exists(writer: DuckDBWriter) -> None:
    assert writer.read_project_row("finance") is None
    assert writer.max_spec_version() is None


# --- unpublish ----------------------------------------------------------------------------


def test_unpublish_removes_only_that_project(
    con: duckdb.DuckDBPyConnection, writer: DuckDBWriter
) -> None:
    writer.ensure_structure()
    fin = fixture_plan("finance")
    writer.swap(fin)
    writer.swap(fixture_plan("marketing"))
    result = writer.unpublish("marketing")
    assert result.rows_deleted["DBT_PROJECT"] == 1
    assert result.rows_deleted["ROOT"] > 1
    assert project_rows(con, "marketing") == dict.fromkeys(PROJECT_TABLES, 0)
    assert project_rows(con, "finance") == {t: len(fin.rows(t)) for t in PROJECT_TABLES}
    keys = {r["key"] for r in rows(con, "ROOT")}
    assert keys == {r["key"] for r in fin.rows("ROOT")}


def test_unpublish_without_tables_is_a_no_op(writer: DuckDBWriter) -> None:
    result = writer.unpublish("finance")
    assert sum(result.rows_deleted.values()) == 0


# --- errors and lifecycle -----------------------------------------------------------------


def test_database_errors_become_warehouse_errors(con: duckdb.DuckDBPyConnection) -> None:
    w = DuckDBWriter(con, database="no_such_catalog", schema=SCHEMA)
    with pytest.raises(WarehouseError, match="no_such_catalog"):
        w.ensure_structure()


def test_connect_from_path_and_close_twice(tmp_path: Path) -> None:
    path = tmp_path / "x.duckdb"
    w = DuckDBWriter.connect(str(path), database=None, schema=SCHEMA)
    w.ensure_structure()
    w.swap(fixture_plan("finance"))
    w.close()
    w.close()
    con = duckdb.connect(str(path))
    assert con.execute(f'SELECT count(*) FROM "{SCHEMA}"."DBT_PROJECT"').fetchone() == (1,)
    con.close()


def test_connect_bad_path_is_warehouse_error(tmp_path: Path) -> None:
    with pytest.raises(WarehouseError, match="no_dir"):
        DuckDBWriter.connect(str(tmp_path / "no_dir" / "x.duckdb"), database=None, schema="A")
