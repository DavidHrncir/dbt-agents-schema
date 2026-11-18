"""Databricks writer against a fake DB-API connection (T18, SPEC 6 step 7)."""

from __future__ import annotations

import re
import sys
import types
from typing import Any

import pytest
from fake_dbapi import FakeConnection, FakeDBError
from fakes import fixture_plan

from dbt_agents_schema.config import ResolvedTarget
from dbt_agents_schema.credentials import Connection
from dbt_agents_schema.plan import PROJECT_TABLES, TABLE_COLUMNS, TABLES, Plan
from dbt_agents_schema.writers import MissingExtraError, WarehouseError, Writer
from dbt_agents_schema.writers.databricks import DatabricksWriter

INFO = r"information_schema\.columns"
T = "`main`.`agents`"


def all_columns(_params: Any) -> list[tuple[Any, ...]]:
    return [(t.lower(), c) for t in TABLES for c in TABLE_COLUMNS[t]]


def make(con: FakeConnection, **kw: Any) -> DatabricksWriter:
    kw.setdefault("catalog", "main")
    kw.setdefault("schema", "agents")
    return DatabricksWriter(con, error_types=(FakeDBError,), **kw)


@pytest.fixture
def plan() -> Plan:
    return fixture_plan("finance", warehouse="databricks")


def replaces(con: FakeConnection) -> list[Any]:
    return con.matching(r"^INSERT INTO \S+ REPLACE WHERE")


def test_is_a_writer() -> None:
    assert isinstance(make(FakeConnection()), Writer)


# --- structure -----------------------------------------------------------------------------


def test_ensure_structure_creates_schema_and_tables() -> None:
    con = FakeConnection()
    assert make(con).ensure_structure().columns_added == {}
    assert con.statements[0].sql == f"CREATE SCHEMA IF NOT EXISTS {T}"
    creates = [s.sql for s in con.matching(r"^CREATE TABLE IF NOT EXISTS")]
    assert len(creates) == len(TABLES)
    model = next(s for s in creates if "`DBT_MODEL`" in s)
    assert model.startswith(f"CREATE TABLE IF NOT EXISTS {T}.`DBT_MODEL` (")
    assert "`tags` STRING" in model and "`project` STRING" in model
    project = next(s for s in creates if "`DBT_PROJECT`" in s)
    assert "`published_at` TIMESTAMP" in project and "`spec_version` BIGINT" in project
    info = con.matching(INFO)[0]
    assert info.sql.startswith("SELECT lower(table_name), lower(column_name) FROM `main`.")
    assert info.params == {"schema": "agents"}


def test_ensure_structure_migrates_with_add_column() -> None:
    legacy = [("dbt_model", c) for c in ("unique_id", "name", "tags")] + [
        ("root", c) for c in ("provider", "key", "content")
    ]
    con = FakeConnection(results=[(INFO, legacy)])
    result = make(con).ensure_structure()
    assert set(result.columns_added) == {"DBT_MODEL"}
    alters = [s.sql for s in con.matching(r"^ALTER TABLE")]
    assert f"ALTER TABLE {T}.`DBT_MODEL` ADD COLUMN `project` STRING" in alters
    assert not any("`tags`" in a for a in alters)
    assert not con.matching("DROP")


def test_ensure_structure_is_idempotent() -> None:
    con = FakeConnection(results=[(INFO, all_columns)])
    assert make(con).ensure_structure().columns_added == {}
    assert not con.matching(r"^(CREATE TABLE|ALTER)")


def test_odd_identifiers_are_quoted() -> None:
    con = FakeConnection()
    make(con, catalog="my cat", schema="we`ird").ensure_structure()
    assert con.statements[0].sql == "CREATE SCHEMA IF NOT EXISTS `my cat`.`we``ird`"


# --- swap: one REPLACE WHERE per table -------------------------------------------------------


def test_one_replace_statement_per_table(plan: Plan) -> None:
    con = FakeConnection()
    result = make(con).swap(plan)
    stmts = replaces(con)
    tables = [re.match(r"^INSERT INTO \S+\.`(\w+)` ", s.sql).group(1) for s in stmts]  # type: ignore[union-attr]
    assert tables == [*PROJECT_TABLES, "ROOT"]
    assert result.rows_written == plan.counts()
    # No other statement deletes or overwrites data tables.
    assert not con.matching(r"^(DELETE|MERGE|TRUNCATE|INSERT OVERWRITE)")
    assert not con.matching(r"^BEGIN")


def test_exact_predicates(plan: Plan) -> None:
    con = FakeConnection()
    make(con).swap(plan)
    by_table = {re.match(r"^INSERT INTO \S+\.`(\w+)` ", s.sql).group(1): s for s in replaces(con)}  # type: ignore[union-attr]
    for table in PROJECT_TABLES:
        s = by_table[table]
        assert s.sql.startswith(
            f"INSERT INTO {T}.`{table}` REPLACE WHERE `project` = :project OR `project` IS NULL "
            "SELECT "
        )
        assert s.params == {"project": "finance"}
    root = by_table["ROOT"]
    shared = ("overview", "project", "model", "column", "dependency", "skill_use")
    keys = ", ".join(f":root_key_{i}" for i in range(len(shared) + 2))
    assert root.sql.startswith(
        f"INSERT INTO {T}.`ROOT` REPLACE WHERE `provider` = :provider AND "
        f"(`key` IN ({keys}) OR startswith(`key`, :skill_prefix)) SELECT "
    )
    assert "LIKE" not in root.sql
    assert root.params["provider"] == "dbt"
    assert root.params["skill_prefix"] == "skill/finance/"
    values = [root.params[f"root_key_{i}"] for i in range(len(shared) + 2)]
    assert set(shared) <= set(values)
    assert "skill/dbt-agents-schema-analyst" in values
    assert values[-1] == "project/finance"


def test_replace_selects_from_staged_table(plan: Plan) -> None:
    con = FakeConnection()
    make(con).swap(plan)
    for s in replaces(con):
        m = re.search(r" FROM (\S+)$", s.sql)
        assert m, s.sql
        stage = m.group(1)
        assert stage.startswith(f"{T}.`_DBT_AGENTS_SCHEMA_STAGE_")
        # The stage was created and filled before the replace, dropped after.
        created = con.matching(rf"^CREATE TABLE {re.escape(stage)} ")
        assert created
        assert con.statements.index(created[0]) < con.statements.index(s)
        drops = con.matching(rf"^DROP TABLE IF EXISTS {re.escape(stage)}$")
        assert drops and con.statements.index(drops[0]) > con.statements.index(s)


def test_stage_inserts_are_bound_and_batched(plan: Plan) -> None:
    con = FakeConnection()
    make(con, batch_rows=3, catalog="wh_catalog").swap(plan)  # `main` is a git branch value
    values: set[str] = set()
    for table in TABLES:
        for row in plan.rows(table):
            values |= {v for v in row.values() if isinstance(v, str) and len(v) > 3}
    idents = [c for t in TABLES for c in TABLE_COLUMNS[t]]
    values = {v for v in values if not any(v in i for i in idents)}
    assert len(values) > 20
    # Data travels only in INSERT/DELETE statements; plain words such as `table` (a
    # SKILL_USE use_kind) also occur in catalog queries, which carry no row values.
    for s in con.matching(r"^(INSERT|DELETE)"):
        for v in values:
            assert v not in s.sql, f"{v!r} in {s.sql[:100]}"
    for table in TABLES:
        fills = [
            s
            for s in con.matching(r"^INSERT INTO \S+`_DBT_AGENTS_SCHEMA_STAGE_\w+` VALUES")
            if f"_STAGE_{table}_" in s.sql
        ]
        rows = plan.rows(table)
        assert len(fills) == (len(rows) + 2) // 3, table
        assert sum(len(f.params) for f in fills) == len(rows) * len(TABLE_COLUMNS[table])


def test_json_is_bound_as_text(plan: Plan) -> None:
    con = FakeConnection()
    make(con).swap(plan)
    fill = next(s for s in con.matching(r"VALUES") if "_STAGE_DBT_MODEL_" in s.sql)
    tags_index = TABLE_COLUMNS["DBT_MODEL"].index("tags")
    first_tags = fill.params[f"v0_{tags_index}"]
    assert isinstance(first_tags, str) and first_tags.startswith("[")


def test_empty_table_is_still_replaced(plan: Plan) -> None:
    con = FakeConnection()
    empty = Plan(
        project=plan.project,
        warehouse=plan.warehouse,
        tables={**plan.tables, "SKILL_USE": ()},
    )
    make(con).swap(empty)
    assert any("`SKILL_USE` REPLACE WHERE" in s.sql for s in replaces(con))


def test_legacy_counts(plan: Plan) -> None:
    con = FakeConnection(results=[(r"IS NULL$", [(2,)])])
    result = make(con).swap(plan)
    assert result.legacy_rows_removed == dict.fromkeys(PROJECT_TABLES, 2)


def test_failure_is_warehouse_error_and_stages_dropped(plan: Plan) -> None:
    con = FakeConnection(fail_on=r"^INSERT INTO \S+`DBT_COLUMN` REPLACE")
    with pytest.raises(WarehouseError, match="Databricks: could not replace the rows"):
        make(con).swap(plan)
    created = con.matching(r"^CREATE TABLE \S+_STAGE_")
    dropped = con.matching(r"^DROP TABLE IF EXISTS \S+_STAGE_")
    assert len(created) == len(dropped) == len(TABLES)


def test_staging_fails_before_any_replace(plan: Plan) -> None:
    con = FakeConnection(fail_on=r"_STAGE_SKILL_USE_\w+` VALUES")
    with pytest.raises(WarehouseError):
        make(con).swap(plan)
    assert not replaces(con)


# --- reads and unpublish -------------------------------------------------------------------


def test_read_project_row() -> None:
    cols = TABLE_COLUMNS["DBT_PROJECT"]
    row = tuple(f"v{i}" for i in range(len(cols)))
    con = FakeConnection(results=[(INFO, all_columns), (r"^SELECT `project`", [row])])
    assert make(con).read_project_row("finance") == dict(zip(cols, row, strict=True))
    q = con.matching(r"^SELECT `project`")[0]
    assert "WHERE `project` = :project" in q.sql and q.params == {"project": "finance"}


def test_reads_before_structure_exists() -> None:
    con = FakeConnection()
    w = make(con)
    assert w.read_project_row("finance") is None
    assert w.max_spec_version() is None


def test_max_spec_version() -> None:
    con = FakeConnection(results=[(INFO, all_columns), (r"max\(", [(1,)])])
    assert make(con).max_spec_version() == 1


def test_unpublish() -> None:
    con = FakeConnection(results=[(INFO, all_columns), (r"count\(\*\)", [(3,)])])
    result = make(con).unpublish("finance")
    assert result.rows_deleted == dict.fromkeys(TABLES, 3)
    deletes = con.matching(r"^DELETE FROM")
    assert [re.match(r"^DELETE FROM \S+\.`(\w+)`", d.sql).group(1) for d in deletes] == [  # type: ignore[union-attr]
        *PROJECT_TABLES,
        "ROOT",
    ]
    assert deletes[0].sql == f"DELETE FROM {T}.`DBT_PROJECT` WHERE `project` = :project"
    root = deletes[-1]
    assert root.sql == (
        f"DELETE FROM {T}.`ROOT` WHERE `provider` = :provider AND "
        "(`key` IN (:root_key_0) OR startswith(`key`, :skill_prefix))"
    )
    assert root.params == {
        "provider": "dbt",
        "root_key_0": "project/finance",
        "skill_prefix": "skill/finance/",
    }


def test_close_twice() -> None:
    con = FakeConnection()
    w = make(con)
    w._owns_connection = True  # type: ignore[attr-defined]
    w.close()
    w.close()
    assert con.closed


# --- connect -------------------------------------------------------------------------------


class FakeDatabricksSQL:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.exc = types.SimpleNamespace(Error=FakeDBError)

    def connect(self, **kwargs: Any) -> FakeConnection:
        self.kwargs = kwargs
        return FakeConnection()


@pytest.fixture
def fake_sql(monkeypatch: pytest.MonkeyPatch) -> FakeDatabricksSQL:
    fake = FakeDatabricksSQL()
    monkeypatch.setattr("dbt_agents_schema.writers.databricks._sql", lambda: fake)
    return fake


SETTINGS = {
    "host": "https://dbc-123.cloud.databricks.com/",
    "http_path": "/sql/1.0/warehouses/abc",
    "catalog": "main",
    "token": "dapiSECRET",
}


def test_connect(fake_sql: FakeDatabricksSQL) -> None:
    w = DatabricksWriter.connect(SETTINGS, schema="agents")
    kw = fake_sql.kwargs
    assert kw["server_hostname"] == "dbc-123.cloud.databricks.com"
    assert kw["http_path"] == "/sql/1.0/warehouses/abc"
    assert kw["access_token"] == "dapiSECRET"
    assert kw["catalog"] == "main"
    assert w._catalog == "main"  # type: ignore[attr-defined]


def test_connect_target_database_overrides_catalog(fake_sql: FakeDatabricksSQL) -> None:
    w = DatabricksWriter.connect(SETTINGS, schema="agents", database="dev")
    assert w._catalog == "dev"  # type: ignore[attr-defined]


def test_connect_failure_hides_token(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeDatabricksSQL()

    def boom(**kwargs: Any) -> None:
        raise FakeDBError(f"bad token {kwargs['access_token']}")

    fake.connect = boom  # type: ignore[method-assign,assignment]
    monkeypatch.setattr("dbt_agents_schema.writers.databricks._sql", lambda: fake)
    with pytest.raises(WarehouseError) as info:
        DatabricksWriter.connect(SETTINGS, schema="agents")
    assert "dapiSECRET" not in str(info.value)
    assert "dbc-123.cloud.databricks.com" in str(info.value)


def test_missing_extra_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "databricks.sql", None)
    with pytest.raises(MissingExtraError, match=r"dbt-agents-schema\[databricks\]"):
        DatabricksWriter.connect(SETTINGS, schema="agents")


def test_factory_opens_databricks_writer(fake_sql: FakeDatabricksSQL) -> None:
    from dbt_agents_schema.writers.factory import open_writer

    connection = Connection(warehouse="databricks", source="t", settings=SETTINGS)
    w = open_writer(connection, ResolvedTarget(environment="prod", database=None, schema="s"))
    assert isinstance(w, DatabricksWriter)
    assert w._schema == "s"  # type: ignore[attr-defined]
