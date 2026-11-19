"""BigQuery writer against a fake client (T17, SPEC 5 and 6)."""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from typing import Any

import pytest
from fakes import fixture_plan
from google.cloud import bigquery

from dbt_agents_schema.config import ResolvedTarget
from dbt_agents_schema.credentials import Connection
from dbt_agents_schema.plan import PROJECT_TABLES, TABLE_COLUMNS, TABLES, Plan
from dbt_agents_schema.writers import MissingExtraError, WarehouseError, Writer
from dbt_agents_schema.writers.bigquery import BigQueryWriter


class FakeAPIError(Exception):
    pass


class FakeNotFound(FakeAPIError):
    pass


@dataclass
class Query:
    sql: str
    params: dict[str, Any]
    location: str | None


@dataclass
class Load:
    rows: list[dict[str, Any]]
    destination: str
    schema: list[tuple[str, str]]
    location: str | None


class FakeJob:
    def __init__(self, rows: list[tuple[Any, ...]]) -> None:
        self._rows = rows

    def result(self) -> list[tuple[Any, ...]]:
        return self._rows


@dataclass
class FakeClient:
    results: list[tuple[str, Any]] = field(default_factory=list)
    fail_on: str | None = None
    fail_times: int | None = None
    fail_message: str = "scripted failure"
    not_found_on: str | None = None
    queries: list[Query] = field(default_factory=list)
    loads: list[Load] = field(default_factory=list)
    events: list[str] = field(default_factory=list)
    closed: bool = False

    def query(self, sql: str, job_config: Any = None, location: str | None = None) -> FakeJob:
        params = {p.name: p for p in (job_config.query_parameters if job_config else [])}
        self.queries.append(Query(sql, params, location))
        self.events.append(sql.split()[0] if sql.split() else "")
        if self.not_found_on and re.search(self.not_found_on, sql):
            raise FakeNotFound("Not found: Dataset")
        failing = self.fail_on and re.search(self.fail_on, sql)
        if failing and (self.fail_times is None or self.fail_times > 0):
            if self.fail_times is not None:
                self.fail_times -= 1
            raise FakeAPIError(self.fail_message)
        for pattern, rows in self.results:
            if re.search(pattern, sql):
                return FakeJob(rows(params) if callable(rows) else list(rows))
        return FakeJob([])

    def load_table_from_json(
        self,
        rows: Any,
        destination: str,
        job_config: Any = None,
        location: str | None = None,
    ) -> FakeJob:
        schema = [(f.name, f.field_type) for f in job_config.schema]
        self.loads.append(Load(list(rows), destination, schema, location))
        self.events.append("LOAD")
        return FakeJob([])

    def close(self) -> None:
        self.closed = True


INFO = r"INFORMATION_SCHEMA\.COLUMNS"


def all_columns(_params: Any) -> list[tuple[Any, ...]]:
    return [(t, c) for t in TABLES for c in TABLE_COLUMNS[t]]


def make(client: FakeClient, **kw: Any) -> BigQueryWriter:
    kw.setdefault("project", "my-proj")
    kw.setdefault("dataset", "AGENTS")
    return BigQueryWriter(
        client,
        location="EU",
        error_types=(FakeAPIError,),
        not_found_types=(FakeNotFound,),
        **kw,
    )


@pytest.fixture
def plan() -> Plan:
    return fixture_plan("finance", warehouse="bigquery")


def scripts(client: FakeClient) -> list[Query]:
    return [q for q in client.queries if q.sql.startswith("BEGIN\n")]


def test_is_a_writer() -> None:
    assert isinstance(make(FakeClient()), Writer)


# --- structure: DDL outside any transaction --------------------------------------------------


def test_ensure_structure_creates_dataset_and_tables() -> None:
    client = FakeClient()
    assert make(client).ensure_structure().columns_added == {}
    assert client.queries[0].sql == "CREATE SCHEMA IF NOT EXISTS `my-proj`.`AGENTS`"
    creates = [q.sql for q in client.queries if q.sql.startswith("CREATE TABLE")]
    assert len(creates) == len(TABLES)
    model = next(s for s in creates if "`DBT_MODEL`" in s)
    assert model.startswith("CREATE TABLE IF NOT EXISTS `my-proj`.`AGENTS`.`DBT_MODEL` (")
    assert "`tags` JSON" in model and "`project` STRING" in model
    project = next(s for s in creates if "`DBT_PROJECT`" in s)
    assert "`published_at` TIMESTAMP" in project and "`spec_version` INT64" in project
    assert all(q.location == "EU" for q in client.queries)
    assert not any("TRANSACTION" in q.sql for q in client.queries)


def test_ensure_structure_migrates_with_add_column() -> None:
    legacy = {"DBT_MODEL": ["unique_id", "name", "tags"], "ROOT": ["provider", "key", "content"]}
    client = FakeClient(results=[(INFO, [(t, c) for t, cs in legacy.items() for c in cs])])
    result = make(client).ensure_structure()
    assert set(result.columns_added) == {"DBT_MODEL"}
    alters = [q.sql for q in client.queries if q.sql.startswith("ALTER")]
    assert (
        "ALTER TABLE `my-proj`.`AGENTS`.`DBT_MODEL` ADD COLUMN IF NOT EXISTS `project` STRING"
        in alters
    )
    assert not any("`tags`" in a for a in alters)
    assert not any("DROP" in q.sql for q in client.queries)


def test_ensure_structure_is_idempotent() -> None:
    client = FakeClient(results=[(INFO, all_columns)])
    assert make(client).ensure_structure().columns_added == {}
    assert not [q for q in client.queries if q.sql.startswith(("CREATE TABLE", "ALTER"))]


def test_odd_identifiers_are_quoted() -> None:
    client = FakeClient()
    make(client, project="p-1", dataset="we`ird\\set").ensure_structure()
    assert client.queries[0].sql == "CREATE SCHEMA IF NOT EXISTS `p-1`.`we\\`ird\\\\set`"


# --- swap: one transaction script ------------------------------------------------------------


def test_swap_is_one_script_with_rollback(plan: Plan) -> None:
    client = FakeClient()
    make(client).swap(plan)
    [script] = scripts(client)
    lines = [ln.strip() for ln in script.sql.splitlines() if ln.strip()]
    assert lines[:2] == ["BEGIN", "BEGIN TRANSACTION;"]
    assert lines[-5:] == [
        "COMMIT TRANSACTION;",
        "EXCEPTION WHEN ERROR THEN",
        "ROLLBACK TRANSACTION;",
        "RAISE USING MESSAGE = @@error.message;",
        "END;",
    ]
    body = [ln for ln in lines if ln.startswith(("DELETE", "INSERT"))]
    tables = [re.search(r"`(\w+)` ", ln).group(1) for ln in body]  # type: ignore[union-attr]
    deletes = tables[: len(PROJECT_TABLES) + 1]
    assert deletes == [*PROJECT_TABLES, "ROOT"]
    assert tables[len(deletes) :] == [t for t in TABLES if plan.rows(t)]
    assert all(ln.startswith("DELETE") for ln in body[: len(deletes)])
    # Only the script and the legacy pre-count run against data tables.
    assert not client.loads


def test_swap_predicates_and_parameters(plan: Plan) -> None:
    client = FakeClient()
    make(client).swap(plan)
    [script] = scripts(client)
    assert (
        "DELETE FROM `my-proj`.`AGENTS`.`DBT_MODEL` WHERE `project` = @project "
        "OR `project` IS NULL;" in script.sql
    )
    assert (
        "DELETE FROM `my-proj`.`AGENTS`.`ROOT` WHERE `provider` = @provider AND "
        "(`key` IN UNNEST(@root_keys) OR STARTS_WITH(`key`, @skill_prefix));" in script.sql
    )
    assert "LIKE" not in script.sql
    p = script.params
    assert p["project"].value == "finance" and p["project"].type_ == "STRING"
    assert p["provider"].value == "dbt"
    assert p["skill_prefix"].value == "skill/finance/"
    assert "project/finance" in p["root_keys"].values and "overview" in p["root_keys"].values
    assert script.location == "EU"


def test_inline_rows_are_struct_array_parameters(plan: Plan) -> None:
    client = FakeClient()
    make(client).swap(plan)
    [script] = scripts(client)
    for table in TABLES:
        rows = plan.rows(table)
        if not rows:
            assert f"rows_{table}" not in script.params
            continue
        param = script.params[f"rows_{table}"]
        assert isinstance(param, bigquery.ArrayQueryParameter)
        assert param.array_type == "STRUCT"
        assert len(param.values) == len(rows)
        assert list(param.values[0].struct_types) == list(TABLE_COLUMNS[table])
    model = script.params["rows_DBT_MODEL"].values[0]
    assert model.struct_types["tags"] == "STRING"  # JSON bound as text, parsed in SQL
    assert isinstance(model.struct_values["tags"], str)
    assert "PARSE_JSON(r.`tags`)" in script.sql
    assert "FROM UNNEST(@rows_DBT_MODEL) AS r" in script.sql
    project = script.params["rows_DBT_PROJECT"].values[0]
    assert project.struct_types["published_at"] == "TIMESTAMP"
    assert project.struct_types["model_count"] == "INT64"


def test_values_are_bound_never_in_sql_text(plan: Plan) -> None:
    client = FakeClient()
    make(client).swap(plan)
    values: set[str] = set()
    for table in TABLES:
        for row in plan.rows(table):
            values |= {v for v in row.values() if isinstance(v, str) and len(v) > 3}
    # Some ROOT keys (`project`, `model`, ...) are also column names, i.e. identifiers.
    idents = [c for t in TABLES for c in TABLE_COLUMNS[t]]
    values = {v for v in values if not any(v in i for i in idents)}
    assert len(values) > 20
    for q in client.queries:
        for v in values:
            assert v not in q.sql, f"{v!r} in {q.sql[:100]}"


def test_large_plan_uses_load_job_staging(plan: Plan) -> None:
    client = FakeClient()
    make(client, staging_rows=5).swap(plan)
    big = [t for t in TABLES if len(plan.rows(t)) > 5]
    small = [t for t in TABLES if 0 < len(plan.rows(t)) <= 5]
    assert big and small
    [script] = scripts(client)
    loaded = {ld.destination.rsplit(".", 1)[-1]: ld for ld in client.loads}
    assert len(loaded) == len(big)
    for table in big:
        [stage] = [n for n in loaded if n.startswith(f"_DBT_AGENTS_SCHEMA_STAGE_{table}_")]
        load = loaded[stage]
        assert load.destination.startswith("my-proj.AGENTS.")
        assert len(load.rows) == len(plan.rows(table))
        assert [n for n, _ in load.schema] == list(TABLE_COLUMNS[table])
        assert f"rows_{table}" not in script.params
        assert f"FROM `my-proj`.`AGENTS`.`{stage}`" in script.sql
        create = [
            q.sql for q in client.queries if q.sql.startswith("CREATE TABLE") and stage in q.sql
        ]
        assert create and "expiration_timestamp" in create[0]
        assert any(
            q.sql == f"DROP TABLE IF EXISTS `my-proj`.`AGENTS`.`{stage}`" for q in client.queries
        )
    for table in small:
        assert f"rows_{table}" in script.params
    # Staging happens before the transaction, cleanup after it.
    script_at = client.events.index("BEGIN")
    assert all(i < script_at for i, e in enumerate(client.events) if e == "LOAD")
    assert client.events[-1] == "DROP"


def test_staging_chosen_by_bytes(plan: Plan) -> None:
    client = FakeClient()
    make(client, staging_bytes=100).swap(plan)
    assert client.loads


def test_staged_json_and_timestamps(plan: Plan) -> None:
    client = FakeClient()
    make(client, staging_rows=0).swap(plan)
    loads = {
        ld.destination.split(".")[-1].split("_STAGE_")[1].rsplit("_", 1)[0]: ld
        for ld in client.loads
    }
    model = loads["DBT_MODEL"]
    assert dict(model.schema)["tags"] == "STRING"
    assert isinstance(model.rows[0]["tags"], str)
    proj = loads["DBT_PROJECT"]
    assert dict(proj.schema)["published_at"] == "TIMESTAMP"
    assert proj.rows[0]["published_at"] == "2026-01-02T03:04:05+00:00"
    [script] = scripts(client)
    assert "PARSE_JSON(r.`tags`)" in script.sql


def test_swap_failure_is_warehouse_error_and_cleans_up(plan: Plan) -> None:
    client = FakeClient(fail_on=r"^BEGIN\n")
    with pytest.raises(WarehouseError, match="BigQuery: could not replace the rows"):
        make(client, staging_rows=5).swap(plan)
    assert client.loads
    drops = [q for q in client.queries if q.sql.startswith("DROP TABLE IF EXISTS")]
    assert len(drops) == len(client.loads)


def test_legacy_counts(plan: Plan) -> None:
    client = FakeClient(results=[(r"IS NULL", [(t, 2) for t in PROJECT_TABLES])])
    result = make(client).swap(plan)
    assert result.legacy_rows_removed == dict.fromkeys(PROJECT_TABLES, 2)
    assert result.rows_written == plan.counts()


# --- reads and unpublish -------------------------------------------------------------------


def test_reads_before_dataset_exists() -> None:
    client = FakeClient(not_found_on=INFO)
    writer = make(client)
    assert writer.read_project_row("finance") is None
    assert writer.max_spec_version() is None


def test_read_project_row_binds_project() -> None:
    cols = TABLE_COLUMNS["DBT_PROJECT"]
    row = tuple(f"v{i}" for i in range(len(cols)))
    client = FakeClient(results=[(INFO, all_columns), (r"^SELECT `project`", [row])])
    assert make(client).read_project_row("finance") == dict(zip(cols, row, strict=True))
    q = next(q for q in client.queries if q.sql.startswith("SELECT `project`"))
    assert "WHERE `project` = @project" in q.sql
    assert q.params["project"].value == "finance"


def test_max_spec_version() -> None:
    client = FakeClient(results=[(INFO, all_columns), (r"max\(", [(3,)])])
    assert make(client).max_spec_version() == 3


def test_unpublish_script() -> None:
    counts = [(t, 4) for t in TABLES]
    client = FakeClient(results=[(INFO, all_columns), (r"count\(\*\)", counts)])
    result = make(client).unpublish("finance")
    assert result.rows_deleted == dict.fromkeys(TABLES, 4)
    [script] = scripts(client)
    assert "BEGIN TRANSACTION;" in script.sql and "ROLLBACK TRANSACTION;" in script.sql
    assert "IS NULL" not in script.sql
    assert script.params["root_keys"].values == ["project/finance"]
    assert script.params["skill_prefix"].value == "skill/finance/"


def test_unpublish_without_dataset_is_a_no_op() -> None:
    client = FakeClient(not_found_on=INFO)
    assert make(client).unpublish("finance").rows_deleted == dict.fromkeys(TABLES, 0)
    assert not scripts(client)


def test_close_twice() -> None:
    client = FakeClient()
    writer = make(client)
    writer._owns_client = True  # type: ignore[attr-defined]
    writer.close()
    writer.close()
    assert client.closed


# --- connect -------------------------------------------------------------------------------


class Captured:
    def __init__(self) -> None:
        self.kwargs: dict[str, Any] = {}


@pytest.fixture
def captured(monkeypatch: pytest.MonkeyPatch) -> Captured:
    cap = Captured()

    def fake_client(**kwargs: Any) -> FakeClient:
        cap.kwargs = kwargs
        return FakeClient()

    monkeypatch.setattr(bigquery, "Client", fake_client)
    return cap


SA = {
    "type": "service_account",
    "project_id": "sa-proj",
    "private_key_id": "x",
    "client_email": "svc@sa-proj.iam.gserviceaccount.com",
    "client_id": "1",
    "token_uri": "https://oauth2.googleapis.com/token",
}


def test_connect_service_account(captured: Captured, monkeypatch: pytest.MonkeyPatch) -> None:
    from google.oauth2 import service_account

    seen: dict[str, Any] = {}

    def from_info(info: dict[str, Any], **kw: Any) -> str:
        seen.update(info)
        return "CREDS"

    monkeypatch.setattr(service_account.Credentials, "from_service_account_info", from_info)
    writer = BigQueryWriter.connect(
        {"project_id": "data-proj", "location": "EU", "credentials_json": SA}, dataset="AGENTS"
    )
    assert captured.kwargs == {"project": "data-proj", "credentials": "CREDS", "location": "EU"}
    assert seen["client_email"] == SA["client_email"]
    assert writer._project == "data-proj"  # type: ignore[attr-defined]


def test_connect_adc(captured: Captured, monkeypatch: pytest.MonkeyPatch) -> None:
    import google.auth

    monkeypatch.setattr(google.auth, "default", lambda **kw: ("ADC", "adc-proj"))
    writer = BigQueryWriter.connect(
        {"project_id": "data-proj", "auth_method": "adc"}, dataset="AGENTS", database="other"
    )
    assert captured.kwargs["credentials"] == "ADC"
    assert captured.kwargs["project"] == "data-proj"
    assert writer._project == "other"  # type: ignore[attr-defined]


def test_connect_keyfile_path(captured: Captured, monkeypatch: pytest.MonkeyPatch) -> None:
    from google.oauth2 import service_account

    monkeypatch.setattr(
        service_account.Credentials, "from_service_account_file", lambda path, **kw: f"F:{path}"
    )
    BigQueryWriter.connect({"project_id": "p", "keyfile": "/k.json"}, dataset="AGENTS")
    assert captured.kwargs["credentials"] == "F:/k.json"


def test_bad_service_account_names_key_not_value(captured: Captured) -> None:
    with pytest.raises(WarehouseError) as info:
        BigQueryWriter.connect(
            {"project_id": "p", "credentials_json": {"private_key": "SECRETKEY"}},
            dataset="AGENTS",
        )
    assert "credentials_json" in str(info.value)
    assert "SECRETKEY" not in str(info.value)


def test_missing_extra_names_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "google.cloud.bigquery", None)
    with pytest.raises(MissingExtraError, match=r"dbt-agents-schema\[bigquery\]"):
        BigQueryWriter.connect({"project_id": "p", "auth_method": "adc"}, dataset="AGENTS")


def test_factory_opens_bigquery_writer(captured: Captured, monkeypatch: pytest.MonkeyPatch) -> None:
    import google.auth

    from dbt_agents_schema.writers.factory import open_writer

    monkeypatch.setattr(google.auth, "default", lambda **kw: ("ADC", None))
    connection = Connection(
        warehouse="bigquery", source="t", settings={"project_id": "p", "auth_method": "adc"}
    )
    writer = open_writer(connection, ResolvedTarget(environment="prod", database=None, schema="D"))
    assert isinstance(writer, BigQueryWriter)
    assert writer._project == "p"  # type: ignore[attr-defined]
    assert writer._dataset == "D"  # type: ignore[attr-defined]


# --- T20 review fixes ---------------------------------------------------------------------


def test_new_tables_declare_primary_keys() -> None:
    client = FakeClient()
    make(client).ensure_structure()
    model = next(
        q.sql
        for q in client.queries
        if q.sql.startswith("CREATE TABLE IF NOT EXISTS") and "`DBT_MODEL`" in q.sql
    )
    assert model.endswith(", PRIMARY KEY (`project`, `unique_id`) NOT ENFORCED)")


def test_default_threshold_large_plan_is_staged(plan: Plan) -> None:
    template = plan.rows("DBT_COLUMN")[0]
    rows = tuple(dict(template, column_name=f"col_{i:05d}") for i in range(2100))
    tables = {**plan.tables, "DBT_COLUMN": rows}
    big = Plan(project=plan.project, warehouse=plan.warehouse, tables=tables)
    client = FakeClient()
    make(client).swap(big)  # default staging thresholds
    [load] = client.loads
    assert "_STAGE_DBT_COLUMN_" in load.destination
    assert len(load.rows) == 2100
    [script] = scripts(client)
    assert "rows_DBT_COLUMN" not in script.params
    assert "rows_DBT_MODEL" in script.params


def test_total_inline_budget_spans_tables(plan: Plan) -> None:
    client = FakeClient()
    make(client, inline_budget_bytes=1).swap(plan)
    assert len(client.loads) == sum(1 for t in TABLES if plan.rows(t))
    [script] = scripts(client)
    assert not [n for n in script.params if n.startswith("rows_")]


CONFLICT = "Transaction is aborted due to concurrent update against table my-proj.AGENTS.ROOT"


def test_concurrent_update_is_retried(plan: Plan) -> None:
    sleeps: list[float] = []
    client = FakeClient(fail_on=r"^BEGIN\n", fail_times=1, fail_message=CONFLICT)
    result = make(client, sleep=sleeps.append).swap(plan)
    assert result.rows_written == plan.counts()
    assert len(scripts(client)) == 2
    assert len(sleeps) == 1


def test_retries_are_bounded(plan: Plan) -> None:
    sleeps: list[float] = []
    client = FakeClient(fail_on=r"^BEGIN\n", fail_message=CONFLICT)
    with pytest.raises(WarehouseError, match="concurrent update"):
        make(client, sleep=sleeps.append, conflict_retries=2).swap(plan)
    assert len(scripts(client)) == 3
    assert len(sleeps) == 2


def test_other_errors_are_not_retried(plan: Plan) -> None:
    sleeps: list[float] = []
    client = FakeClient(fail_on=r"^BEGIN\n", fail_times=1, fail_message="Syntax error")
    with pytest.raises(WarehouseError):
        make(client, sleep=sleeps.append).swap(plan)
    assert len(scripts(client)) == 1
    assert not sleeps
