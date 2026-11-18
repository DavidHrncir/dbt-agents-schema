"""Writer protocol, dialect helpers and the in-memory fake (SPEC 5, 6; T11)."""

from __future__ import annotations

import importlib

import pytest
from fakes import FakeWriter, fixture_plan

from dbt_agents_schema.dialects import DIALECTS, LOGICAL_TYPES, get_dialect
from dbt_agents_schema.plan import PROJECT_TABLES, SHARED_ROOT_KEYS, TABLE_COLUMNS, TABLES
from dbt_agents_schema.writers import (
    MissingExtraError,
    RootScope,
    SwapResult,
    UnpublishResult,
    Writer,
    import_extra,
)


def run_publish(writer: Writer, project: str) -> SwapResult:
    """Typed use of the protocol: this is how publish drives any writer."""
    writer.ensure_structure()
    writer.max_spec_version()
    writer.read_project_row(project)
    return writer.swap(fixture_plan(project))


# --- protocol ---------------------------------------------------------------------------


def test_fake_satisfies_protocol() -> None:
    assert isinstance(FakeWriter(), Writer)


def test_protocol_methods_documented() -> None:
    for name in (
        "ensure_structure",
        "read_project_row",
        "max_spec_version",
        "swap",
        "unpublish",
        "close",
    ):
        method = getattr(Writer, name)
        assert method.__doc__, f"Writer.{name} needs a docstring"


def test_fake_publish_two_projects_and_unpublish() -> None:
    writer = FakeWriter()
    fin = run_publish(writer, "finance")
    mkt = run_publish(writer, "marketing")
    assert fin.rows_written["DBT_MODEL"] > 0
    assert mkt.rows_written["DBT_MODEL"] > 0
    assert writer.read_project_row("finance") is not None
    assert writer.max_spec_version() == 1

    result = writer.unpublish("marketing")
    assert isinstance(result, UnpublishResult)
    assert result.rows_deleted["DBT_PROJECT"] == 1
    assert writer.read_project_row("marketing") is None
    assert writer.read_project_row("finance") is not None
    for table in PROJECT_TABLES:
        assert all(r["project"] == "finance" for r in writer.tables[table])
    keys = {r["key"] for r in writer.tables["ROOT"]}
    assert "project/finance" in keys
    assert "project/marketing" not in keys
    assert not any(k.startswith("skill/marketing/") for k in keys)
    assert set(SHARED_ROOT_KEYS) <= keys
    writer.close()
    assert writer.closed


def test_fake_swap_replaces_only_own_rows_and_counts_legacy() -> None:
    writer = FakeWriter()
    writer.ensure_structure()
    writer.tables["DBT_MODEL"].append(
        dict.fromkeys(TABLE_COLUMNS["DBT_MODEL"], None) | {"unique_id": "model.legacy.x"}
    )
    first = run_publish(writer, "marketing")
    assert first.legacy_rows_removed["DBT_MODEL"] == 1
    assert first.total_legacy_removed == 1
    second = run_publish(writer, "finance")
    assert second.total_legacy_removed == 0
    assert {r["project"] for r in writer.tables["DBT_MODEL"]} == {"finance", "marketing"}


def test_swap_result_totals() -> None:
    result = SwapResult(
        rows_written=dict.fromkeys(TABLES, 2), legacy_rows_removed=dict.fromkeys(TABLES, 1)
    )
    assert result.total_written == 12
    assert result.total_legacy_removed == 6


# --- ROOT ownership ---------------------------------------------------------------------


def test_root_scope_publish_owns_shared_and_project_keys() -> None:
    scope = RootScope.for_publish("fin_a")
    assert scope.owns("overview")
    assert scope.owns("skill/dbt-agents-schema-analyst")
    assert scope.owns("project/fin_a")
    assert scope.owns("skill/fin_a/revenue")
    assert scope.owns("skill/fin_a/nested/x")
    assert not scope.owns("project/fin_ab")
    assert not scope.owns("skill/fin_ab/x")
    assert not scope.owns("skill/finXa/x")  # `_` is literal, never a wildcard
    assert not scope.owns("project/marketing")
    assert not scope.owns("custom_key")


def test_root_scope_unpublish_leaves_shared_keys() -> None:
    scope = RootScope.for_unpublish("finance")
    assert scope.shared_keys == ()
    assert not scope.owns("overview")
    assert scope.owns("project/finance")
    assert scope.owns("skill/finance/a")


def test_root_scope_like_pattern_escapes_wildcards() -> None:
    scope = RootScope.for_publish("fin_a")
    assert scope.skill_like_pattern("\\") == "skill/fin\\_a/%"


# --- dialects ---------------------------------------------------------------------------


def test_one_dialect_module_per_warehouse() -> None:
    assert set(DIALECTS) == {"snowflake", "bigquery", "databricks", "duckdb"}
    for name in DIALECTS:
        module = importlib.import_module(f"dbt_agents_schema.dialects.{name}")
        assert get_dialect(name) is module.DIALECT


def test_unknown_dialect_names_choices() -> None:
    with pytest.raises(ValueError, match="duckdb"):
        get_dialect("oracle")


@pytest.mark.parametrize(
    ("dialect", "name", "quoted"),
    [
        ("duckdb", "ROOT", '"ROOT"'),
        ("duckdb", 'we"ird', '"we""ird"'),
        ("snowflake", "AGENTS", '"AGENTS"'),
        ("snowflake", 'a"b', '"a""b"'),
        ("bigquery", "my-project", "`my-project`"),
        ("bigquery", "a`b", "`a\\`b`"),
        ("bigquery", "a\\b", "`a\\\\b`"),
        ("databricks", "main", "`main`"),
        ("databricks", "a`b", "`a``b`"),
    ],
)
def test_quote_identifier(dialect: str, name: str, quoted: str) -> None:
    assert get_dialect(dialect).quote(name) == quoted


def test_quote_rejects_empty_and_nul() -> None:
    for name in DIALECTS:
        d = get_dialect(name)
        with pytest.raises(ValueError):
            d.quote("")
        with pytest.raises(ValueError):
            d.quote("a\x00b")


def test_qualified_name_skips_missing_parts() -> None:
    d = get_dialect("duckdb")
    assert d.qualified("db", "AGENTS", "ROOT") == '"db"."AGENTS"."ROOT"'
    assert d.qualified(None, "AGENTS", "ROOT") == '"AGENTS"."ROOT"'


@pytest.mark.parametrize(
    ("dialect", "expected"),
    [
        ("snowflake", ("VARCHAR", "BOOLEAN", "NUMBER(38,0)", "TIMESTAMP_TZ", "VARIANT")),
        ("bigquery", ("STRING", "BOOL", "INT64", "TIMESTAMP", "JSON")),
        ("databricks", ("STRING", "BOOLEAN", "BIGINT", "TIMESTAMP", "STRING")),
        ("duckdb", ("VARCHAR", "BOOLEAN", "BIGINT", "TIMESTAMPTZ", "JSON")),
    ],
)
def test_type_map_matches_spec(dialect: str, expected: tuple[str, ...]) -> None:
    d = get_dialect(dialect)
    logical = ("varchar", "boolean", "integer", "timestamp", "json")
    assert tuple(d.types[t] for t in logical) == expected
    assert d.types["text"] == d.types["varchar"]


def test_every_column_has_a_logical_type() -> None:
    for table, columns in TABLE_COLUMNS.items():
        assert tuple(LOGICAL_TYPES[table]) == columns
    assert LOGICAL_TYPES["DBT_PROJECT"]["spec_version"] == "integer"
    assert LOGICAL_TYPES["DBT_PROJECT"]["published_at"] == "timestamp"
    assert LOGICAL_TYPES["DBT_MODEL"]["tags"] == "json"
    assert LOGICAL_TYPES["DBT_COLUMN"]["meta"] == "json"


def test_column_ddl_types() -> None:
    d = get_dialect("snowflake")
    assert d.column_type("DBT_MODEL", "meta") == "VARIANT"
    assert d.column_type("DBT_PROJECT", "model_count") == "NUMBER(38,0)"


# --- optional extras --------------------------------------------------------------------


def test_import_extra_names_the_extra() -> None:
    with pytest.raises(MissingExtraError) as info:
        import_extra("definitely_not_installed_xyz", "snowflake")
    assert 'pip install "dbt-agents-schema[snowflake]"' in str(info.value)


def test_import_extra_returns_module() -> None:
    assert import_extra("json", "duckdb").__name__ == "json"
