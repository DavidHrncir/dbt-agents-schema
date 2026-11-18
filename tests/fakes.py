"""Test doubles shared across test modules."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from dbt_agents_schema.config import load_config
from dbt_agents_schema.manifest import load_manifest
from dbt_agents_schema.plan import PROJECT_TABLES, TABLES, Plan, RunMetadata, build_plan
from dbt_agents_schema.skills import load_skills
from dbt_agents_schema.writers import (
    EnsureResult,
    RootScope,
    SwapResult,
    UnpublishResult,
)

HERE = Path(__file__).parent
PROJECTS = HERE / "fixtures" / "projects"
MANIFESTS = HERE / "fixtures" / "manifests"


def fixture_plan(
    name: str,
    *,
    project_dir: str | None = None,
    warehouse: str = "duckdb",
    repo_url: str | None = "https://github.com/example/analytics",
) -> Plan:
    """Build the plan for a fixture project (manifest `name`, project dir default `name`)."""
    manifest = load_manifest(MANIFESTS / f"{name}.json")
    root = PROJECTS / (project_dir or name)
    config = load_config(root, manifest_project_name=manifest.project_name)
    skills = load_skills(root, config.skills_dir, config.project)
    run = RunMetadata(
        environment="prod",
        warehouse=warehouse,
        published_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
        repo_url=repo_url,
        git_sha="0" * 40,
        git_branch="main",
    )
    return build_plan(config, manifest, skills, run)


class FakeWriter:
    """In-memory writer: follows the Writer protocol's semantics without a database."""

    def __init__(self) -> None:
        self.tables: dict[str, list[dict[str, Any]]] = {t: [] for t in TABLES}
        self.calls: list[str] = []
        self.closed = False

    def ensure_structure(self) -> EnsureResult:
        self.calls.append("ensure_structure")
        return EnsureResult(columns_added={})

    def read_project_row(self, project: str) -> dict[str, Any] | None:
        self.calls.append("read_project_row")
        for row in self.tables["DBT_PROJECT"]:
            if row["project"] == project:
                return dict(row)
        return None

    def max_spec_version(self) -> int | None:
        self.calls.append("max_spec_version")
        versions = [
            r["spec_version"] for r in self.tables["DBT_PROJECT"] if r["spec_version"] is not None
        ]
        return max(versions, default=None)

    def swap(self, plan: Plan) -> SwapResult:
        self.calls.append("swap")
        legacy: dict[str, int] = {}
        for table in PROJECT_TABLES:
            rows = self.tables[table]
            legacy[table] = sum(1 for r in rows if r["project"] is None)
            self.tables[table] = [
                r for r in rows if r["project"] is not None and r["project"] != plan.project
            ]
        scope = RootScope.for_publish(plan.project)
        self.tables["ROOT"] = [
            r for r in self.tables["ROOT"] if not (r["provider"] == "dbt" and scope.owns(r["key"]))
        ]
        for table in TABLES:
            self.tables[table].extend(dict(r) for r in plan.rows(table))
        return SwapResult(rows_written=plan.counts(), legacy_rows_removed=legacy)

    def unpublish(self, project: str) -> UnpublishResult:
        self.calls.append("unpublish")
        deleted: dict[str, int] = {}
        for table in PROJECT_TABLES:
            before = len(self.tables[table])
            self.tables[table] = [r for r in self.tables[table] if r["project"] != project]
            deleted[table] = before - len(self.tables[table])
        scope = RootScope.for_unpublish(project)
        before = len(self.tables["ROOT"])
        self.tables["ROOT"] = [
            r for r in self.tables["ROOT"] if not (r["provider"] == "dbt" and scope.owns(r["key"]))
        ]
        deleted["ROOT"] = before - len(self.tables["ROOT"])
        return UnpublishResult(rows_deleted=deleted)

    def close(self) -> None:
        self.calls.append("close")
        self.closed = True


# Tables as the dbt Labs `agents-schema` tool creates them: no `project` columns and none
# of the columns marked "new" in SPEC 5, and no DBT_PROJECT table.
DBT_LABS_TABLES: dict[str, tuple[str, ...]] = {
    "ROOT": ("provider", "key", "content"),
    "DBT_MODEL": (
        "unique_id",
        "name",
        "database_name",
        "schema_name",
        "materialization",
        "description",
        "file_path",
        "tags",
        "meta",
    ),
    "DBT_COLUMN": ("model_id", "column_name", "data_type", "description", "meta"),
    "DBT_DEPENDENCY": ("upstream_id", "downstream_id", "upstream_type", "downstream_type"),
    "SKILL_USE": ("provider", "skill_key", "use_kind", "object_ref"),
}


def create_dbt_labs_tables(con: Any, schema: str) -> dict[str, int]:
    """Create dbt Labs-shaped tables in a DuckDB connection with two legacy rows each
    (ROOT gets a legacy `skill/legacy` row plus the shared `overview` row). Returns the
    legacy row count per project-scoped table."""
    con.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema}"')
    counts: dict[str, int] = {}
    for table, columns in DBT_LABS_TABLES.items():
        types = ", ".join(
            f'"{c}" {"JSON" if c in ("tags", "meta") else "VARCHAR"}' for c in columns
        )
        con.execute(f'CREATE TABLE "{schema}"."{table}" ({types})')
        placeholders = ", ".join("?" for _ in columns)
        for i in range(2):
            values: list[Any] = []
            for c in columns:
                if c in ("tags", "meta"):
                    values.append("[]" if c == "tags" else "{}")
                elif table == "ROOT" and c == "provider":
                    values.append("dbt")
                elif table == "ROOT" and c == "key":
                    values.append(("overview", "skill/legacy")[i])
                else:
                    values.append(f"legacy_{c}_{i}")
            con.execute(f'INSERT INTO "{schema}"."{table}" VALUES ({placeholders})', values)
        if table != "ROOT":
            counts[table] = 2
    return counts
