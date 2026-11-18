"""Snowflake writer against a fake DB-API connection (T16, SPEC 5 and 6)."""

from __future__ import annotations

import re
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from fake_dbapi import FakeConnection, FakeDBError, Statement
from fakes import fixture_plan

from dbt_agents_schema.config import ResolvedTarget
from dbt_agents_schema.credentials import Connection
from dbt_agents_schema.plan import PROJECT_TABLES, TABLE_COLUMNS, TABLES, Plan
from dbt_agents_schema.writers import MissingExtraError, WarehouseError, Writer
from dbt_agents_schema.writers.snowflake import SnowflakeWriter

INFO = r"\"INFORMATION_SCHEMA\"\.\"COLUMNS\""


def all_columns(params: Any) -> list[tuple[Any, ...]]:
    """information_schema answer: every table exists with every column (uppercase)."""
    table = params[-1]
    return [(c.upper(),) for c in TABLE_COLUMNS[table]]


def make(
    con: FakeConnection, *, database: str | None = "ANALYTICS", schema: str = "AGENTS", **kw: Any
) -> SnowflakeWriter:
    return SnowflakeWriter(con, database=database, schema=schema, error_types=(FakeDBError,), **kw)


@pytest.fixture
def plan() -> Plan:
    return fixture_plan("finance", warehouse="snowflake")


def kinds(con: FakeConnection) -> list[str]:
    """First keyword(s) of each statement, with the table for DML."""
    out = []
    for s in con.statements:
        m = re.match(r"\s*(DELETE FROM|INSERT INTO)\s+\S*?\"(\w+)\"\s", s.sql)
        if m:
            out.append(f"{m.group(1).split()[0]} {m.group(2)}")
        else:
            out.append(s.sql.split()[0].upper())
    return out


def test_is_a_writer() -> None:
    assert isinstance(make(FakeConnection()), Writer)


# --- structure -----------------------------------------------------------------------------


def test_ensure_structure_creates_schema_and_tables() -> None:
    con = FakeConnection()
    result = make(con).ensure_structure()
    assert result.columns_added == {}
    assert con.statements[0].sql == 'CREATE SCHEMA IF NOT EXISTS "ANALYTICS"."AGENTS"'
    creates = con.matching(r"^CREATE TABLE")
    assert [re.search(r'"(\w+)" \(', s.sql).group(1) for s in creates] == list(TABLES)  # type: ignore[union-attr]
    model = next(s.sql for s in creates if '"DBT_MODEL"' in s.sql)
    assert model.startswith('CREATE TABLE IF NOT EXISTS "ANALYTICS"."AGENTS"."DBT_MODEL" (')
    assert '"PROJECT" VARCHAR' in model
    assert '"TAGS" VARIANT' in model
    project = next(s.sql for s in creates if '"DBT_PROJECT"' in s.sql)
    assert '"PUBLISHED_AT" TIMESTAMP_TZ' in project
    assert '"SPEC_VERSION" NUMBER(38,0)' in project


def test_identifiers_are_uppercase_and_quoted() -> None:
    con = FakeConnection()
    make(con).ensure_structure()
    for s in con.matching(r"^CREATE TABLE"):
        for ident in re.findall(r'"([^"]+)"', s.sql):
            assert ident == ident.upper(), s.sql


def test_odd_identifiers_are_quoted() -> None:
    con = FakeConnection()
    writer = make(con, database='my "db"', schema="we.ird schema")
    writer.ensure_structure()
    assert con.statements[0].sql == 'CREATE SCHEMA IF NOT EXISTS "my ""db"""."we.ird schema"'
    assert any('"my ""db"""."we.ird schema"."ROOT"' in s for s in con.sql())
    info = con.matching(INFO)[0]
    assert info.sql.startswith('SELECT "COLUMN_NAME" FROM "my ""db""".')
    assert info.params == ["we.ird schema", "ROOT"]


def test_no_database_uses_session_database() -> None:
    con = FakeConnection()
    make(con, database=None).ensure_structure()
    assert con.statements[0].sql == 'CREATE SCHEMA IF NOT EXISTS "AGENTS"'
    assert con.matching(INFO)[0].sql.startswith('SELECT "COLUMN_NAME" FROM "INFORMATION_SCHEMA"')


def test_ensure_structure_is_idempotent() -> None:
    con = FakeConnection(results=[(INFO, all_columns)])
    assert make(con).ensure_structure().columns_added == {}
    assert not con.matching(r"^(CREATE TABLE|ALTER)")


def test_ensure_structure_migrates_dbt_labs_tables() -> None:
    legacy = {
        "ROOT": ["PROVIDER", "KEY", "CONTENT"],
        "DBT_MODEL": ["unique_id", "NAME", "DATABASE_NAME", "SCHEMA_NAME", "MATERIALIZATION"],
    }

    def answer(params: Any) -> list[tuple[Any, ...]]:
        return [(c,) for c in legacy.get(params[-1], [])]

    con = FakeConnection(results=[(INFO, answer)])
    result = make(con).ensure_structure()
    assert set(result.columns_added) == {"DBT_MODEL"}
    assert "project" in result.columns_added["DBT_MODEL"]
    assert "unique_id" not in result.columns_added["DBT_MODEL"]
    alters = con.matching(r"^ALTER TABLE")
    assert alters[0].sql == (
        'ALTER TABLE "ANALYTICS"."AGENTS"."DBT_MODEL" ADD COLUMN "PROJECT" VARCHAR'
    )
    assert any('ADD COLUMN "TAGS" VARIANT' in s.sql for s in alters)
    assert not con.matching(r"DROP")
    # Tables that did not exist are created.
    assert {re.search(r'"(\w+)" \(', s.sql).group(1) for s in con.matching("^CREATE TABLE")} == {  # type: ignore[union-attr]
        "DBT_PROJECT",
        "DBT_COLUMN",
        "DBT_DEPENDENCY",
        "SKILL_USE",
    }


# --- swap ----------------------------------------------------------------------------------


def test_swap_statement_order(plan: Plan) -> None:
    con = FakeConnection(results=[(r"count\(\*\)", [(0,)])])
    result = make(con).swap(plan)
    k = [x for x in kinds(con) if x != "SELECT"]
    assert k[0] == "BEGIN"
    assert k[-1] == "COMMIT"
    assert "ROLLBACK" not in k
    deletes = [x for x in k if x.startswith("DELETE")]
    inserts = [x for x in k if x.startswith("INSERT")]
    assert deletes == [f"DELETE {t}" for t in (*PROJECT_TABLES, "ROOT")]
    assert k.index(inserts[0]) > k.index(deletes[-1])
    assert list(dict.fromkeys(inserts)) == [f"INSERT {t}" for t in TABLES if plan.rows(t)]
    assert result.rows_written == plan.counts()


def test_swap_deletes_own_and_legacy_rows(plan: Plan) -> None:
    con = FakeConnection(results=[(r"IS NULL", [(3,)]), (r"count\(\*\)", [(0,)])])
    result = make(con).swap(plan)
    delete = con.matching(r'^DELETE FROM "ANALYTICS"."AGENTS"."DBT_MODEL"')[0]
    assert delete.sql.endswith('WHERE "PROJECT" = ? OR "PROJECT" IS NULL')
    assert delete.params == ["finance"]
    assert result.legacy_rows_removed == dict.fromkeys(PROJECT_TABLES, 3)


def test_swap_root_delete_uses_bound_prefix_not_like(plan: Plan) -> None:
    con = FakeConnection(results=[(r"count\(\*\)", [(0,)])])
    make(con).swap(plan)
    root = con.matching(r'^DELETE FROM "ANALYTICS"."AGENTS"."ROOT"')[0]
    assert "LIKE" not in root.sql.upper()
    assert 'STARTSWITH("KEY", ?)' in root.sql
    assert root.params[0] == "dbt"
    assert "project/finance" in root.params
    assert "overview" in root.params
    assert root.params[-1] == "skill/finance/"


def test_values_are_bound_never_in_sql_text(plan: Plan) -> None:
    con = FakeConnection(results=[(r"count\(\*\)", [(0,)])])
    make(con).swap(plan)
    values: set[str] = set()
    for table in TABLES:
        for row in plan.rows(table):
            values |= {v for v in row.values() if isinstance(v, str) and len(v) > 3}
    for s in con.statements:
        for v in values:
            assert v not in s.sql, f"value {v!r} interpolated into: {s.sql[:120]}"
    bound = [p for s in con.matching(r"^INSERT") for p in s.params]
    assert "finance" in bound


def test_inserts_parse_json_and_timestamps(plan: Plan) -> None:
    con = FakeConnection(results=[(r"count\(\*\)", [(0,)])])
    make(con).swap(plan)
    model = con.matching(r'^INSERT INTO "ANALYTICS"."AGENTS"."DBT_MODEL"')[0]
    assert model.sql.startswith(
        'INSERT INTO "ANALYTICS"."AGENTS"."DBT_MODEL" ("PROJECT", "UNIQUE_ID",'
    )
    assert "PARSE_JSON($13)" in model.sql and "PARSE_JSON($14)" in model.sql
    assert " FROM VALUES (?, ?" in model.sql
    project = con.matching(r'^INSERT INTO "ANALYTICS"."AGENTS"."DBT_PROJECT"')[0]
    assert "TO_TIMESTAMP_TZ($13)" in project.sql
    published = project.params[TABLE_COLUMNS["DBT_PROJECT"].index("published_at")]
    assert published == "2026-01-02T03:04:05+00:00"
    # JSON values are bound as JSON text.
    tags = model.params[TABLE_COLUMNS["DBT_MODEL"].index("tags")]
    assert isinstance(tags, str) and tags.startswith("[")


def test_inserts_are_batched(plan: Plan) -> None:
    con = FakeConnection(results=[(r"count\(\*\)", [(0,)])])
    make(con, batch_rows=2).swap(plan)
    for table in TABLES:
        rows = plan.rows(table)
        stmts = con.matching(rf'^INSERT INTO "ANALYTICS"."AGENTS"."{table}"')
        assert len(stmts) == (len(rows) + 1) // 2, table
        ncols = len(TABLE_COLUMNS[table])
        assert sum(len(s.params) for s in stmts) == len(rows) * ncols
        for s in stmts:
            assert s.sql.count("(?") == len(s.params) // ncols


def test_swap_rolls_back_on_error(plan: Plan) -> None:
    con = FakeConnection(
        results=[(r"count\(\*\)", [(0,)])], fail_on=r'^INSERT INTO \S+"DBT_COLUMN"'
    )
    with pytest.raises(WarehouseError, match=r"Snowflake: could not replace the rows"):
        make(con).swap(plan)
    k = kinds(con)
    assert k[0] == "BEGIN"
    assert k[-1] == "ROLLBACK"
    assert "COMMIT" not in k


def test_swap_rolls_back_on_non_database_error(plan: Plan) -> None:
    class Boom(Exception):
        pass

    con = FakeConnection(results=[(r"count\(\*\)", [(0,)])])
    writer = make(con)
    original = writer._insert  # type: ignore[attr-defined]

    def failing(table: str, rows: Any) -> None:
        if table == "DBT_COLUMN":
            raise Boom
        original(table, rows)

    writer._insert = failing  # type: ignore[attr-defined]
    with pytest.raises(Boom):
        writer.swap(plan)
    assert con.statements[-1].sql == "ROLLBACK"


def test_error_message_names_schema_not_values(plan: Plan) -> None:
    con = FakeConnection(fail_on=r"^CREATE SCHEMA")
    with pytest.raises(WarehouseError) as info:
        make(con).ensure_structure()
    assert '"ANALYTICS"."AGENTS"' in str(info.value)


# --- reads and unpublish ----------------------------------------------------------------


def test_read_project_row() -> None:
    cols = TABLE_COLUMNS["DBT_PROJECT"]
    row = tuple(f"v{i}" for i in range(len(cols)))
    con = FakeConnection(results=[(INFO, all_columns), (r'^SELECT "PROJECT"', [row])])
    found = make(con).read_project_row("finance")
    assert found == dict(zip(cols, row, strict=True))
    select = con.matching(r'^SELECT "PROJECT"')[0]
    assert select.params == ["finance"]
    assert 'WHERE "PROJECT" = ?' in select.sql


def test_reads_before_structure_exists() -> None:
    con = FakeConnection()
    writer = make(con)
    assert writer.read_project_row("finance") is None
    assert writer.max_spec_version() is None
    assert not con.matching(r'FROM "ANALYTICS"."AGENTS"."DBT_PROJECT"')


def test_max_spec_version() -> None:
    con = FakeConnection(results=[(INFO, all_columns), (r"max\(", [(2,)])])
    assert make(con).max_spec_version() == 2


def test_unpublish() -> None:
    con = FakeConnection(results=[(INFO, all_columns), (r"count\(\*\)", [(4,)])])
    result = make(con).unpublish("finance")
    k = [x for x in kinds(con) if x != "SELECT"]
    assert k == ["BEGIN", *(f"DELETE {t}" for t in (*PROJECT_TABLES, "ROOT")), "COMMIT"]
    assert result.rows_deleted == dict.fromkeys(TABLES, 4)
    root = con.matching(r"^DELETE FROM \S+\"ROOT\"")[0]
    assert root.params == ["dbt", "project/finance", "skill/finance/"]
    assert "overview" not in root.params


def test_close_twice() -> None:
    con = FakeConnection()
    writer = make(con)
    writer._owns_connection = True  # type: ignore[attr-defined]
    writer.close()
    writer.close()
    assert con.closed


# --- connect and key-pair auth ----------------------------------------------------------


class FakeSnowflake:
    """Stands in for `snowflake.connector`, capturing connect() kwargs."""

    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}
        self.errors = types.SimpleNamespace(Error=FakeDBError)
        self.Error = FakeDBError

    def connect(self, **kwargs: Any) -> FakeConnection:
        self.kwargs = kwargs
        return FakeConnection()


@pytest.fixture
def fake_sf(monkeypatch: pytest.MonkeyPatch) -> FakeSnowflake:
    fake = FakeSnowflake()
    monkeypatch.setattr(
        "dbt_agents_schema.writers.snowflake._connector", lambda: fake, raising=True
    )
    return fake


def _pem(passphrase: bytes | None) -> tuple[str, bytes]:
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    enc: serialization.KeySerializationEncryption = (
        serialization.BestAvailableEncryption(passphrase)
        if passphrase
        else serialization.NoEncryption()
    )
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc)
    der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    return pem.decode(), der


BASE = {"account": "acme-xy123", "user": "PUBLISHER", "warehouse": "WH", "database": "ANALYTICS"}


def test_connect_with_password(fake_sf: FakeSnowflake) -> None:
    writer = SnowflakeWriter.connect({**BASE, "password": "s3cret", "role": "R"}, schema="AGENTS")
    kw = fake_sf.kwargs
    assert kw["account"] == "acme-xy123"
    assert kw["user"] == "PUBLISHER"
    assert kw["password"] == "s3cret"
    assert kw["role"] == "R"
    assert kw["warehouse"] == "WH"
    assert kw["database"] == "ANALYTICS"
    assert kw["paramstyle"] == "qmark"
    assert kw["session_parameters"]["TIMEZONE"] == "UTC"
    assert "private_key" not in kw
    writer.close()


def test_connect_with_encrypted_private_key_pem(fake_sf: FakeSnowflake) -> None:
    pem, der = _pem(b"pass")
    SnowflakeWriter.connect(
        {**BASE, "private_key_pem": pem, "private_key_passphrase": "pass"}, schema="AGENTS"
    )
    assert fake_sf.kwargs["private_key"] == der
    assert "password" not in fake_sf.kwargs


def test_connect_with_private_key_path(fake_sf: FakeSnowflake, tmp_path: Path) -> None:
    pem, der = _pem(None)
    key = tmp_path / "rsa_key.p8"
    key.write_text(pem)
    SnowflakeWriter.connect({**BASE, "private_key_path": str(key)}, schema="AGENTS")
    assert fake_sf.kwargs["private_key"] == der


def test_connect_target_database_overrides_credentials(fake_sf: FakeSnowflake) -> None:
    writer = SnowflakeWriter.connect(
        {**BASE, "password": "x"}, database="OTHER_DB", schema="AGENTS"
    )
    assert fake_sf.kwargs["database"] == "OTHER_DB"
    assert writer._database == "OTHER_DB"  # type: ignore[attr-defined]


def test_bad_private_key_names_key_not_value(fake_sf: FakeSnowflake) -> None:
    with pytest.raises(WarehouseError) as info:
        SnowflakeWriter.connect({**BASE, "private_key_pem": "not a key"}, schema="AGENTS")
    assert "private_key_pem" in str(info.value)
    assert "not a key" not in str(info.value)


def test_wrong_passphrase(fake_sf: FakeSnowflake) -> None:
    pem, _ = _pem(b"right")
    with pytest.raises(WarehouseError, match="private_key_passphrase"):
        SnowflakeWriter.connect(
            {**BASE, "private_key_pem": pem, "private_key_passphrase": "wrong"}, schema="AGENTS"
        )


def test_missing_key_file(fake_sf: FakeSnowflake, tmp_path: Path) -> None:
    with pytest.raises(WarehouseError, match="private_key_path"):
        SnowflakeWriter.connect(
            {**BASE, "private_key_path": str(tmp_path / "nope.p8")}, schema="AGENTS"
        )


def test_connect_failure_hides_password(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeSnowflake()

    def boom(**kwargs: Any) -> None:
        raise FakeDBError(f"login failed for {kwargs['user']} with s3cret")

    fake.connect = boom  # type: ignore[method-assign,assignment]
    monkeypatch.setattr("dbt_agents_schema.writers.snowflake._connector", lambda: fake)
    with pytest.raises(WarehouseError) as info:
        SnowflakeWriter.connect({**BASE, "password": "s3cret"}, schema="AGENTS")
    assert "s3cret" not in str(info.value)
    assert "acme-xy123" in str(info.value)


def test_missing_extra_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "snowflake.connector", None)
    with pytest.raises(MissingExtraError, match=r"dbt-agents-schema\[snowflake\]"):
        SnowflakeWriter.connect({**BASE, "password": "x"}, schema="AGENTS")


def test_factory_opens_snowflake_writer(fake_sf: FakeSnowflake) -> None:
    from dbt_agents_schema.writers.factory import open_writer

    connection = Connection(
        warehouse="snowflake", source="test", settings={**BASE, "password": "x"}
    )
    writer = open_writer(connection, ResolvedTarget(environment="prod", database=None, schema="A"))
    assert isinstance(writer, SnowflakeWriter)
    assert fake_sf.kwargs["database"] == "ANALYTICS"


def test_statement_dataclass_is_recorded() -> None:
    con = FakeConnection()
    con.cursor().execute("SELECT 1", [1])
    assert con.statements == [Statement("SELECT 1", [1], False)]
