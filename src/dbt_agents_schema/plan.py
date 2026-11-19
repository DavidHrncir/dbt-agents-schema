"""Build the publish plan: every row one publish writes (SPEC 5 and 6 step 3).

Pure function of config + manifest + skills + run metadata. No I/O beyond reading the
built-in analyst skill text shipped inside the package.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import cache
from importlib import resources
from typing import Any

from dbt_agents_schema import __version__
from dbt_agents_schema.config import Config
from dbt_agents_schema.manifest import Manifest, read_models
from dbt_agents_schema.skills import SkillSet

SPEC_VERSION = 1
PROVIDER = "dbt"
WAREHOUSES = ("snowflake", "bigquery", "databricks", "duckdb")
ANALYST_SKILL_KEY = "skill/dbt-agents-schema-analyst"
SHARED_ROOT_KEYS = ("overview", "project", "model", "column", "dependency", "skill_use")

TABLES = ("ROOT", "DBT_PROJECT", "DBT_MODEL", "DBT_COLUMN", "DBT_DEPENDENCY", "SKILL_USE")
PROJECT_TABLES = TABLES[1:]

TABLE_COLUMNS: Mapping[str, tuple[str, ...]] = {
    "ROOT": ("provider", "key", "content"),
    "DBT_PROJECT": (
        "project",
        "dbt_project_name",
        "owner",
        "description",
        "environment",
        "repo_url",
        "git_sha",
        "git_branch",
        "dbt_version",
        "manifest_generated_at",
        "publisher_version",
        "spec_version",
        "published_at",
        "model_count",
        "column_count",
        "dependency_count",
        "skill_count",
    ),
    "DBT_MODEL": (
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
    ),
    "DBT_COLUMN": ("project", "model_id", "column_name", "data_type", "description", "meta"),
    "DBT_DEPENDENCY": (
        "project",
        "upstream_id",
        "downstream_id",
        "upstream_type",
        "downstream_type",
        "upstream_project",
    ),
    "SKILL_USE": ("project", "provider", "skill_key", "use_kind", "object_ref"),
}

PRIMARY_KEYS: Mapping[str, tuple[str, ...]] = {
    "ROOT": ("provider", "key"),
    "DBT_PROJECT": ("project",),
    "DBT_MODEL": ("project", "unique_id"),
    "DBT_COLUMN": ("project", "model_id", "column_name"),
    "DBT_DEPENDENCY": ("project", "upstream_id", "downstream_id"),
    "SKILL_USE": ("project", "skill_key", "use_kind", "object_ref"),
}

Row = Mapping[str, Any]

_SHARED_ROOT_CONTENT: Mapping[str, str] = {
    "overview": (
        "# dbt agents schema\n\n"
        "This schema holds metadata about one or more dbt projects, published by "
        "dbt-agents-schema. Every row in the DBT_* and SKILL_USE tables carries a "
        "`project` column naming the dbt project that owns it; filter on it to look at "
        "one project. Tables: ROOT (this catalog of keys and markdown content), "
        "DBT_PROJECT (one row per published project), DBT_MODEL, DBT_COLUMN, "
        "DBT_DEPENDENCY (model lineage) and SKILL_USE (which schemas and tables each "
        "skill uses). Project overviews are under ROOT keys `project/<project>`, skills "
        "under `skill/<project>/<path>`. Start with "
        "`SELECT * FROM DBT_PROJECT ORDER BY project`.\n"
    ),
    "project": (
        "# DBT_PROJECT\n\n"
        "One row per dbt project published into this schema, keyed by `project`. "
        "Columns: project, dbt_project_name, owner, description, environment, repo_url, "
        "git_sha, git_branch, dbt_version, manifest_generated_at, publisher_version, "
        "spec_version, published_at, model_count, column_count, dependency_count, "
        "skill_count.\n"
    ),
    "model": (
        "# DBT_MODEL\n\n"
        "One row per published dbt model, keyed by (project, unique_id). Columns: "
        "project, unique_id, name, package_name, database_name, schema_name, alias, "
        "relation_name (null for ephemeral models), materialization, access, "
        "description, file_path, tags (JSON array), meta (JSON object). Query a model "
        "through relation_name, or database_name.schema_name.alias.\n"
    ),
    "column": (
        "# DBT_COLUMN\n\n"
        "One row per documented column of a published model, keyed by (project, "
        "model_id, column_name). model_id joins to DBT_MODEL.unique_id within the same "
        "project. Columns: project, model_id, column_name, data_type, description, meta "
        "(JSON object).\n"
    ),
    "dependency": (
        "# DBT_DEPENDENCY\n\n"
        "One row per lineage edge into a published model, keyed by (project, "
        "upstream_id, downstream_id). upstream_type is the node type (model, source, "
        "seed, snapshot, ...); downstream_type is always model. upstream_project is the "
        "publishing project when the upstream node is in its manifest, else null. Join "
        "upstream_id or downstream_id to DBT_MODEL.unique_id; the same model may be "
        "published by several projects.\n"
    ),
    "skill_use": (
        "# SKILL_USE\n\n"
        "Which schemas and tables each skill declares in its `uses` frontmatter, keyed "
        "by (project, skill_key, use_kind, object_ref). provider is always dbt; "
        "skill_key is the ROOT key of the skill; use_kind is `schema` or `table`.\n"
    ),
}


@dataclass(frozen=True)
class RunMetadata:
    """Facts about this publish that do not come from config, manifest or skills."""

    environment: str
    warehouse: str
    published_at: datetime
    repo_url: str | None = None
    git_sha: str | None = None
    git_branch: str | None = None
    publisher_version: str = field(default=__version__)

    def __post_init__(self) -> None:
        if self.published_at.tzinfo is None:
            raise ValueError("RunMetadata.published_at must be timezone-aware (UTC)")
        object.__setattr__(self, "published_at", self.published_at.astimezone(UTC))
        if self.warehouse not in WAREHOUSES:
            raise ValueError(
                f"unsupported warehouse {self.warehouse!r}; expected one of: "
                f"{', '.join(WAREHOUSES)}"
            )


@dataclass(frozen=True)
class Plan:
    """Every row one publish writes, per table, sorted by primary key."""

    project: str
    warehouse: str
    tables: Mapping[str, tuple[Row, ...]]

    def rows(self, table: str) -> tuple[Row, ...]:
        return self.tables[table]

    def counts(self) -> dict[str, int]:
        return {table: len(self.tables[table]) for table in TABLES}


def analyst_skill(warehouse: str) -> str:
    """Built-in analyst skill body for `warehouse` (SPEC 9)."""
    return _analyst_skill_text(warehouse)


@cache
def _analyst_skill_text(warehouse: str) -> str:
    return (
        resources.files("dbt_agents_schema")
        .joinpath(f"skills/{warehouse}.md")
        .read_text(encoding="utf-8")
    )


def build_plan(
    config: Config,
    manifest: Manifest,
    skills: SkillSet,
    run: RunMetadata,
    *,
    root_package: str | None = None,
) -> Plan:
    """`root_package` overrides `metadata.project_name` as the root package (pass the
    `dbt_project.yml` name when the manifest has none; see `CheckResult.root_package`)."""
    project = config.project
    content = read_models(
        manifest,
        project=project,
        root_package=root_package,
        include_packages=config.include_packages,
        exclude_packages=config.exclude_packages,
    )
    models = [
        _row("DBT_MODEL", **_fields(m), tags=list(m.tags), meta=dict(m.meta))
        for m in content.models
    ]
    columns = [_row("DBT_COLUMN", **_fields(c), meta=dict(c.meta)) for c in content.columns]
    deps = [_row("DBT_DEPENDENCY", **_fields(d)) for d in content.dependencies]

    uses: dict[tuple[str, str, str], Row] = {}
    for skill in skills.skills:
        for kind, refs in (("schema", skill.uses_schemas), ("table", skill.uses_tables)):
            for ref in refs:
                uses[(skill.key, kind, ref)] = _row(
                    "SKILL_USE",
                    project=project,
                    provider=PROVIDER,
                    skill_key=skill.key,
                    use_kind=kind,
                    object_ref=ref,
                )

    project_row = _row(
        "DBT_PROJECT",
        project=project,
        dbt_project_name=manifest.project_name,
        owner=config.owner,
        description=config.description,
        environment=run.environment,
        repo_url=run.repo_url,
        git_sha=run.git_sha,
        git_branch=run.git_branch,
        dbt_version=manifest.dbt_version,
        manifest_generated_at=manifest.generated_at,
        publisher_version=run.publisher_version,
        spec_version=SPEC_VERSION,
        published_at=run.published_at,
        model_count=len(models),
        column_count=len(columns),
        dependency_count=len(deps),
        skill_count=len(skills.skills),
    )

    root: list[Row] = [
        _row("ROOT", provider=PROVIDER, key=key, content=text)
        for key, text in _SHARED_ROOT_CONTENT.items()
    ]
    root.append(
        _row("ROOT", provider=PROVIDER, key=ANALYST_SKILL_KEY, content=analyst_skill(run.warehouse))
    )
    root.append(
        _row("ROOT", provider=PROVIDER, key=f"project/{project}", content=_overview(project_row))
    )
    root.extend(
        _row("ROOT", provider=PROVIDER, key=s.key, content=s.content) for s in skills.skills
    )

    tables: dict[str, list[Row]] = {
        "ROOT": root,
        "DBT_PROJECT": [project_row],
        "DBT_MODEL": models,
        "DBT_COLUMN": columns,
        "DBT_DEPENDENCY": deps,
        "SKILL_USE": list(uses.values()),
    }
    return Plan(
        project=project,
        warehouse=run.warehouse,
        tables={t: tuple(sorted(tables[t], key=_pk_sorter(t))) for t in TABLES},
    )


def plan_to_json(plan: Plan) -> dict[str, Any]:
    """JSON-ready view of a plan (timestamps as ISO 8601). Used for golden files and
    `--dry-run` output."""
    return {
        "project": plan.project,
        "warehouse": plan.warehouse,
        "tables": {
            table: [{k: _jsonable(v) for k, v in row.items()} for row in plan.rows(table)]
            for table in TABLES
        },
    }


# --- internals ---------------------------------------------------------------------


def _fields(record: Any) -> dict[str, Any]:
    return {k: v for k, v in vars(record).items() if k not in ("tags", "meta")}


def _row(table: str, **values: Any) -> Row:
    columns = TABLE_COLUMNS[table]
    unknown = set(values) - set(columns)
    missing = set(columns) - set(values)
    if unknown or missing:
        raise ValueError(f"{table}: unknown columns {sorted(unknown)}, missing {sorted(missing)}")
    return {c: values[c] for c in columns}


def _pk_sorter(table: str) -> Any:
    pk = PRIMARY_KEYS[table]
    return lambda row: tuple(row[c] for c in pk)


def _jsonable(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _overview(row: Row) -> str:
    def show(value: Any) -> str:
        if value is None or value == "":
            return "unknown"
        return value.isoformat() if isinstance(value, datetime) else str(value)

    project = row["project"]
    return (
        f"# Project `{project}`\n\n"
        f"- project: {project}\n"
        f"- dbt project name: {show(row['dbt_project_name'])}\n"
        f"- owner: {show(row['owner'])}\n"
        f"- description: {show(row['description'])}\n"
        f"- environment: {show(row['environment'])}\n"
        f"- repository: {show(row['repo_url'])}\n"
        f"- git SHA: {show(row['git_sha'])}\n"
        f"- git branch: {show(row['git_branch'])}\n"
        f"- dbt version: {show(row['dbt_version'])}\n"
        f"- published at: {show(row['published_at'])}\n"
        f"- models: {row['model_count']}\n"
        f"- columns: {row['column_count']}\n"
        f"- dependencies: {row['dependency_count']}\n"
        f"- skills: {row['skill_count']}\n\n"
        "Rows in DBT_MODEL, DBT_COLUMN, DBT_DEPENDENCY and SKILL_USE carry `project`. "
        "To see only this project's models:\n\n"
        "```sql\n"
        "SELECT name, schema_name, alias, description\n"
        "FROM DBT_MODEL\n"
        f"WHERE project = '{project}'\n"
        "ORDER BY name;\n"
        "```\n"
    )
