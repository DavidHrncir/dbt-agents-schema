"""Plan builder tests (SPEC 5 and 6 step 3, T07).

Golden files live in tests/golden/. Regenerate them only with `UPDATE_GOLDEN=1`.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from dbt_agents_schema import __version__
from dbt_agents_schema.config import load_config
from dbt_agents_schema.manifest import load_manifest
from dbt_agents_schema.plan import (
    ANALYST_SKILL_KEY,
    PRIMARY_KEYS,
    SHARED_ROOT_KEYS,
    SPEC_VERSION,
    TABLE_COLUMNS,
    TABLES,
    Plan,
    RunMetadata,
    build_plan,
    plan_to_json,
)
from dbt_agents_schema.skills import load_skills

HERE = Path(__file__).parent
PROJECTS = HERE / "fixtures" / "projects"
MANIFESTS = HERE / "fixtures" / "manifests"
GOLDEN = HERE / "golden"

RUN = RunMetadata(
    environment="prod",
    warehouse="duckdb",
    published_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    repo_url="https://github.com/example/analytics",
    git_sha="0123456789abcdef0123456789abcdef01234567",
    git_branch="main",
    publisher_version="0.1.0",
)


def build(name: str, project_dir: str | None = None, run: RunMetadata = RUN) -> Plan:
    manifest = load_manifest(MANIFESTS / f"{name}.json")
    root = PROJECTS / (project_dir or name)
    config = load_config(root, manifest_project_name=manifest.project_name)
    skills = load_skills(root, config.skills_dir, config.project)
    return build_plan(config, manifest, skills, run)


def root_keys(plan: Plan) -> list[str]:
    return [r["key"] for r in plan.rows("ROOT")]


# --- golden files ------------------------------------------------------------------


@pytest.mark.parametrize("name", ["finance", "marketing"])
def test_golden(name: str) -> None:
    actual = json.dumps(plan_to_json(build(name)), indent=2, sort_keys=True) + "\n"
    path = GOLDEN / f"{name}.json"
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.parent.mkdir(exist_ok=True)
        path.write_text(actual, encoding="utf-8")
    assert path.exists(), f"missing {path}; run with UPDATE_GOLDEN=1 to create it"
    assert actual == path.read_text(encoding="utf-8")


def test_plan_identical_across_runs() -> None:
    first, second = build("finance"), build("finance")
    assert first == second
    assert plan_to_json(first) == plan_to_json(second)


# --- table shape -------------------------------------------------------------------


def test_tables_and_columns_match_spec() -> None:
    assert TABLES == (
        "ROOT",
        "DBT_PROJECT",
        "DBT_MODEL",
        "DBT_COLUMN",
        "DBT_DEPENDENCY",
        "SKILL_USE",
    )
    assert TABLE_COLUMNS["DBT_MODEL"] == (
        "project",
        "unique_id",
        "name",
        "package_name",
        "database_name",
        "schema_name",
        "alias",
        "relation_name",
        "materialization",
        "access",
        "description",
        "file_path",
        "tags",
        "meta",
    )
    assert TABLE_COLUMNS["ROOT"] == ("provider", "key", "content")
    assert PRIMARY_KEYS["DBT_COLUMN"] == ("project", "model_id", "column_name")
    assert PRIMARY_KEYS["SKILL_USE"] == ("project", "skill_key", "use_kind", "object_ref")


@pytest.mark.parametrize("table", TABLES)
def test_rows_have_exact_columns(table: str) -> None:
    plan = build("finance")
    for row in plan.rows(table):
        assert tuple(row) == TABLE_COLUMNS[table]


@pytest.mark.parametrize("table", TABLES)
def test_rows_sorted_and_unique_by_primary_key(table: str) -> None:
    plan = build("finance")
    keys = [tuple(row[c] for c in PRIMARY_KEYS[table]) for row in plan.rows(table)]
    assert keys == sorted(keys)
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("table", [t for t in TABLES if t != "ROOT"])
def test_project_scoped_rows_carry_project(table: str) -> None:
    plan = build("marketing")
    assert plan.rows(table)
    assert {row["project"] for row in plan.rows(table)} == {"marketing"}


# --- DBT_PROJECT -------------------------------------------------------------------


def test_project_row_counts_match_other_tables() -> None:
    for name in ("finance", "marketing", "finance_v2"):
        plan = build(name, project_dir=name)
        (row,) = plan.rows("DBT_PROJECT")
        assert row["model_count"] == len(plan.rows("DBT_MODEL"))
        assert row["column_count"] == len(plan.rows("DBT_COLUMN"))
        assert row["dependency_count"] == len(plan.rows("DBT_DEPENDENCY"))
        project_skills = [k for k in root_keys(plan) if k.startswith(f"skill/{row['project']}/")]
        assert row["skill_count"] == len(project_skills)


def test_project_row_fields() -> None:
    (row,) = build("finance").rows("DBT_PROJECT")
    assert row["project"] == "finance"
    assert row["dbt_project_name"] == "finance"
    assert row["owner"] == "finance-data@example.com"
    assert row["description"] == "Finance marts"
    assert row["environment"] == "prod"
    assert row["repo_url"] == RUN.repo_url
    assert row["git_sha"] == RUN.git_sha
    assert row["git_branch"] == "main"
    assert row["dbt_version"] == "1.12.7"
    assert isinstance(row["manifest_generated_at"], datetime)
    assert row["publisher_version"] == "0.1.0"
    assert row["spec_version"] == SPEC_VERSION == 1
    assert row["published_at"] == RUN.published_at


def test_default_publisher_version_is_package_version() -> None:
    run = RunMetadata(environment="dev", warehouse="duckdb", published_at=RUN.published_at)
    assert run.publisher_version == __version__
    assert run.repo_url is None and run.git_sha is None and run.git_branch is None


def test_naive_published_at_rejected() -> None:
    with pytest.raises(ValueError, match="timezone"):
        RunMetadata(environment="dev", warehouse="duckdb", published_at=datetime(2026, 1, 1))


def test_unknown_warehouse_rejected() -> None:
    with pytest.raises(ValueError, match="redshift"):
        RunMetadata(environment="dev", warehouse="redshift", published_at=RUN.published_at)


# --- ROOT --------------------------------------------------------------------------


def test_all_root_rows_present() -> None:
    plan = build("finance")
    keys = root_keys(plan)
    for shared in SHARED_ROOT_KEYS:
        assert shared in keys
    assert SHARED_ROOT_KEYS == ("overview", "project", "model", "column", "dependency", "skill_use")
    assert ANALYST_SKILL_KEY in keys
    assert "project/finance" in keys
    assert "skill/finance/payments/reconciliation" in keys
    assert "skill/finance/revenue_overview" in keys
    assert len(keys) == len(SHARED_ROOT_KEYS) + 1 + 1 + 2
    assert {r["provider"] for r in plan.rows("ROOT")} == {"dbt"}


def test_shared_root_rows_identical_across_projects() -> None:
    fin = {r["key"]: r["content"] for r in build("finance").rows("ROOT")}
    mkt = {r["key"]: r["content"] for r in build("marketing").rows("ROOT")}
    for key in (*SHARED_ROOT_KEYS, ANALYST_SKILL_KEY):
        assert fin[key] == mkt[key]
        assert fin[key].strip()
    assert "project" in fin["overview"]


@pytest.mark.parametrize("warehouse", ["snowflake", "bigquery", "databricks", "duckdb"])
def test_analyst_skill_matches_warehouse(warehouse: str) -> None:
    run = RunMetadata(environment="prod", warehouse=warehouse, published_at=RUN.published_at)
    content = {r["key"]: r["content"] for r in build("finance", run=run).rows("ROOT")}
    body = content[ANALYST_SKILL_KEY]
    assert body.strip()
    assert "SELECT" in body.upper()


def test_project_overview_content() -> None:
    content = {r["key"]: r["content"] for r in build("finance").rows("ROOT")}
    text = content["project/finance"]
    for expected in (
        "finance",
        "finance-data@example.com",
        "Finance marts",
        "prod",
        RUN.repo_url,
        RUN.git_sha,
        "2026-01-02T03:04:05+00:00",
        "project = 'finance'",
    ):
        assert expected is not None and expected in text
    (row,) = build("finance").rows("DBT_PROJECT")
    for count in ("model_count", "column_count", "dependency_count", "skill_count"):
        assert str(row[count]) in text


def test_overview_handles_unknowns() -> None:
    run = RunMetadata(environment="dev", warehouse="duckdb", published_at=RUN.published_at)
    content = {r["key"]: r["content"] for r in build("marketing", run=run).rows("ROOT")}
    assert "unknown" in content["project/marketing"]


def test_skill_root_content_is_file_text() -> None:
    content = {r["key"]: r["content"] for r in build("finance").rows("ROOT")}
    expected = (PROJECTS / "finance" / "agents" / "skills" / "revenue_overview.md").read_text()
    assert content["skill/finance/revenue_overview"] == expected


# --- SKILL_USE / models ------------------------------------------------------------


def test_skill_use_rows() -> None:
    rows = build("finance").rows("SKILL_USE")
    assert [(r["skill_key"], r["use_kind"], r["object_ref"]) for r in rows] == [
        ("skill/finance/payments/reconciliation", "schema", "finance_marts"),
        ("skill/finance/payments/reconciliation", "table", "FINANCE_MARTS.FCT_PAYMENTS"),
    ]
    assert {r["provider"] for r in rows} == {"dbt"}


def test_finance_v2_drops_skill_use() -> None:
    plan = build("finance_v2")
    assert plan.rows("SKILL_USE") == ()
    assert "skill/finance/payments/reconciliation" not in root_keys(plan)
    assert "model.finance.fct_payments" not in {r["unique_id"] for r in plan.rows("DBT_MODEL")}


def test_include_packages_false(tmp_path: Path) -> None:
    (tmp_path / "agents_schema.yml").write_text(
        "version: 1\nproject: finance\ninclude_packages: false\n", encoding="utf-8"
    )
    manifest = load_manifest(MANIFESTS / "finance.json")
    config = load_config(tmp_path, manifest_project_name=manifest.project_name)
    plan = build_plan(config, manifest, load_skills(tmp_path, "agents/skills", "finance"), RUN)
    assert all(r["package_name"] == "finance" for r in plan.rows("DBT_MODEL"))
    (row,) = plan.rows("DBT_PROJECT")
    assert row["skill_count"] == 0
    assert row["owner"] is None


def test_model_json_columns() -> None:
    rows = {r["unique_id"]: r for r in build("finance").rows("DBT_MODEL")}
    rev = rows["model.finance.fct_revenue"]
    assert rev["tags"] == ["finance", "revenue"]
    assert rev["meta"] == {"owner": "revenue-team"}
    assert rows["model.finance.int_invoice_lines"]["relation_name"] is None


def test_plan_to_json_is_json_serializable() -> None:
    data: dict[str, Any] = plan_to_json(build("finance"))
    text = json.dumps(data)
    assert '"published_at": "2026-01-02T03:04:05+00:00"' in text
    assert set(data["tables"]) == set(TABLES)
    assert data["project"] == "finance"


def test_published_at_normalized_to_utc() -> None:
    from datetime import timedelta, timezone

    local = datetime(2026, 1, 2, 5, 4, 5, tzinfo=timezone(timedelta(hours=2)))
    run = RunMetadata(environment="prod", warehouse="duckdb", published_at=local)
    assert run.published_at == RUN.published_at
    assert run.published_at.utcoffset() == timedelta(0)
    (row,) = build("finance", run=run).rows("DBT_PROJECT")
    assert row["published_at"].isoformat() == "2026-01-02T03:04:05+00:00"
