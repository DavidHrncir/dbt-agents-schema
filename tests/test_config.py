"""Config loader tests (SPEC section 3, T04)."""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest

from dbt_agents_schema.config import (
    DEFAULT_RULES,
    ConfigError,
    load_config,
    normalize_project_name,
    resolve_target,
    validate_project_key,
)

FIXTURES = Path(__file__).parent / "fixtures" / "projects"


def write(tmp_path: Path, text: str, name: str = "agents_schema.yml") -> Path:
    path = tmp_path / name
    path.write_text(text, encoding="utf-8")
    return path


# --- schema shipped in the package -------------------------------------------------


def test_schema_ships_inside_package() -> None:
    text = (
        resources.files("dbt_agents_schema")
        .joinpath("schemas/agents_schema.schema.json")
        .read_text(encoding="utf-8")
    )
    schema = json.loads(text)
    assert schema["additionalProperties"] is False
    assert schema["required"] == ["version"]


# --- defaults ----------------------------------------------------------------------


def test_defaults_with_no_file(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, manifest_project_name="My-Project")
    assert cfg.found is False
    assert cfg.path == tmp_path / "agents_schema.yml"
    assert cfg.version == 1
    assert cfg.project == "my_project"
    assert cfg.project_source == "manifest"
    assert cfg.owner is None
    assert cfg.description is None
    assert cfg.include_packages is True
    assert cfg.exclude_packages == ()
    assert cfg.skills_dir == "agents/skills"
    assert sorted(cfg.environments) == ["dev", "prod"]
    prod, dev = cfg.environments["prod"], cfg.environments["dev"]
    assert (prod.credentials, prod.schema, prod.database) == ("env", "AGENTS", None)
    assert (dev.credentials, dev.schema, dev.database) == (
        "dbt_profile",
        "{{ target_schema }}_AGENTS",
        None,
    )
    assert cfg.check.description_scope == "public"
    assert cfg.check.external_schemas == ()
    assert dict(cfg.check.rules) == {
        "config_missing": "warning",
        "missing_model_description": "warning",
        "missing_column_description": "warning",
        "sampled_values_in_description": "error",
        "skill_without_uses": "warning",
        "skill_unresolved_uses": "error",
    }
    assert dict(cfg.check.rules) == dict(DEFAULT_RULES)


def test_explicit_missing_config_path_means_defaults(tmp_path: Path) -> None:
    missing = tmp_path / "nope.yml"
    cfg = load_config(tmp_path, config_path=missing, manifest_project_name="finance")
    assert cfg.found is False
    assert cfg.path == missing


def test_minimal_file_takes_defaults(tmp_path: Path) -> None:
    write(tmp_path, "version: 1\n")
    cfg = load_config(tmp_path, manifest_project_name="finance")
    assert cfg.found is True
    assert sorted(cfg.environments) == ["dev", "prod"]
    assert dict(cfg.check.rules) == dict(DEFAULT_RULES)


def test_partial_rules_merge_with_defaults(tmp_path: Path) -> None:
    write(tmp_path, "version: 1\ncheck:\n  rules:\n    skill_without_uses: off\n")
    cfg = load_config(tmp_path, manifest_project_name="finance")
    assert cfg.check.rules["skill_without_uses"] == "off"
    assert cfg.check.rules["skill_unresolved_uses"] == "error"


def test_yaml_off_is_a_severity_not_false(tmp_path: Path) -> None:
    # YAML 1.1 reads bare `off` as False; the loader must still accept it as "off".
    write(tmp_path, "version: 1\ncheck:\n  rules:\n    config_missing: off\n")
    cfg = load_config(tmp_path, manifest_project_name="finance")
    assert cfg.check.rules["config_missing"] == "off"


def test_environments_replace_defaults(tmp_path: Path) -> None:
    write(
        tmp_path,
        "version: 1\nenvironments:\n  staging:\n    credentials: env\n"
        "    database: RAW\n    schema: AGENTS_STG\n",
    )
    cfg = load_config(tmp_path, manifest_project_name="finance")
    assert list(cfg.environments) == ["staging"]
    assert cfg.environments["staging"].database == "RAW"


def test_full_fixture_config_loads() -> None:
    cfg = load_config(FIXTURES / "finance", manifest_project_name="finance")
    assert cfg.project == "finance"
    assert cfg.project_source == "config"
    assert cfg.owner == "finance-data@example.com"
    assert cfg.description == "Finance marts"


# --- default project resolution ----------------------------------------------------


def test_project_from_config_wins(tmp_path: Path) -> None:
    write(tmp_path, "version: 1\nproject: ledger\n")
    cfg = load_config(tmp_path, manifest_project_name="finance")
    assert (cfg.project, cfg.project_source) == ("ledger", "config")


def test_project_defaults_to_manifest_name_normalized(tmp_path: Path) -> None:
    assert normalize_project_name("Acme-Finance") == "acme_finance"
    cfg = load_config(tmp_path, manifest_project_name="Acme-Finance")
    assert (cfg.project, cfg.project_source) == ("acme_finance", "manifest")


def test_project_falls_back_to_dbt_project_yml(tmp_path: Path) -> None:
    write(tmp_path, "name: from_dbt_project\nversion: '1'\n", name="dbt_project.yml")
    cfg = load_config(tmp_path, manifest_project_name=None)
    assert (cfg.project, cfg.project_source) == ("from_dbt_project", "dbt_project")


def test_project_unresolvable_is_config_error(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path, manifest_project_name=None)
    assert "project" in str(exc.value)
    assert "agents_schema.yml" in str(exc.value)


def test_bad_fixture_default_project_fails_pattern() -> None:
    cfg = load_config(FIXTURES / "bad", manifest_project_name=None)
    assert cfg.project_source == "dbt_project"
    assert validate_project_key(cfg.project) is not None


@pytest.mark.parametrize("key", ["finance", "a", "a" + "b" * 62, "fin_2"])
def test_project_key_valid(key: str) -> None:
    assert validate_project_key(key) is None


@pytest.mark.parametrize("key", ["", "Finance", "2fin", "_fin", "fin-x", "a" + "b" * 63])
def test_project_key_invalid(key: str) -> None:
    message = validate_project_key(key)
    assert message is not None
    assert "^[a-z][a-z0-9_]{0,62}$" in message


# --- validation rules --------------------------------------------------------------


def errors_for(tmp_path: Path, text: str) -> ConfigError:
    path = write(tmp_path, text)
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path, manifest_project_name="finance")
    assert exc.value.file == path
    return exc.value


def paths(err: ConfigError) -> list[str]:
    return [issue.path for issue in err.issues]


def test_yaml_parse_error_reports_line(tmp_path: Path) -> None:
    err = errors_for(tmp_path, "version: 1\nenvironments:\n  prod: [credentials: env\n")
    assert err.issues[0].line is not None
    assert err.issues[0].line >= 3


def test_bad_fixture_unparseable() -> None:
    path = FIXTURES / "bad" / "agents_schema.unparseable.yml"
    with pytest.raises(ConfigError) as exc:
        load_config(FIXTURES / "bad", config_path=path, manifest_project_name="bad")
    assert exc.value.file == path


def test_bad_fixture_invalid_reports_json_paths() -> None:
    path = FIXTURES / "bad" / "agents_schema.invalid.yml"
    with pytest.raises(ConfigError) as exc:
        load_config(FIXTURES / "bad", config_path=path, manifest_project_name="bad")
    assert "$" in paths(exc.value)  # unknown key `colour` at the top level
    assert "$.check.rules.missing_model_description" in paths(exc.value)
    assert "colour" in str(exc.value)


def test_empty_file_is_invalid(tmp_path: Path) -> None:
    err = errors_for(tmp_path, "")
    assert "version" in str(err)


def test_version_required(tmp_path: Path) -> None:
    err = errors_for(tmp_path, "project: finance\n")
    assert "version" in str(err)


def test_version_only_1(tmp_path: Path) -> None:
    err = errors_for(tmp_path, "version: 2\n")
    assert "$.version" in paths(err)


def test_unknown_top_level_key(tmp_path: Path) -> None:
    err = errors_for(tmp_path, "version: 1\nfoo: bar\n")
    assert "foo" in str(err)


def test_unknown_nested_keys(tmp_path: Path) -> None:
    err = errors_for(
        tmp_path,
        "version: 1\nenvironments:\n  prod:\n    credentials: env\n    schema: A\n"
        "    warehouse: X\ncheck:\n  bogus: 1\n",
    )
    assert "$.environments.prod" in paths(err)
    assert "$.check" in paths(err)


def test_environment_name_pattern(tmp_path: Path) -> None:
    err = errors_for(
        tmp_path, "version: 1\nenvironments:\n  Prod:\n    credentials: env\n    schema: A\n"
    )
    assert "Prod" in str(err)


def test_environment_name_too_long(tmp_path: Path) -> None:
    name = "e" * 32
    err = errors_for(
        tmp_path, f"version: 1\nenvironments:\n  {name}:\n    credentials: env\n    schema: A\n"
    )
    assert name in str(err)


def test_environment_name_max_length_ok(tmp_path: Path) -> None:
    name = "e" * 31
    write(tmp_path, f"version: 1\nenvironments:\n  {name}:\n    credentials: env\n    schema: A\n")
    assert name in load_config(tmp_path, manifest_project_name="finance").environments


def test_environment_schema_required(tmp_path: Path) -> None:
    err = errors_for(tmp_path, "version: 1\nenvironments:\n  prod:\n    credentials: env\n")
    assert "schema" in str(err)


def test_environment_credentials_required(tmp_path: Path) -> None:
    err = errors_for(tmp_path, "version: 1\nenvironments:\n  prod:\n    schema: A\n")
    assert "credentials" in str(err)


def test_credentials_enum(tmp_path: Path) -> None:
    err = errors_for(
        tmp_path, "version: 1\nenvironments:\n  prod:\n    credentials: vault\n    schema: A\n"
    )
    assert "$.environments.prod.credentials" in paths(err)


def test_description_scope_enum(tmp_path: Path) -> None:
    err = errors_for(tmp_path, "version: 1\ncheck:\n  description_scope: some\n")
    assert "$.check.description_scope" in paths(err)


def test_rule_severity_enum(tmp_path: Path) -> None:
    err = errors_for(tmp_path, "version: 1\ncheck:\n  rules:\n    skill_without_uses: loud\n")
    assert "$.check.rules.skill_without_uses" in paths(err)


def test_hard_error_rules_cannot_be_configured(tmp_path: Path) -> None:
    err = errors_for(tmp_path, "version: 1\ncheck:\n  rules:\n    config_invalid: off\n")
    assert "config_invalid" in str(err)


def test_types_checked(tmp_path: Path) -> None:
    err = errors_for(
        tmp_path,
        "version: 1\ninclude_packages: maybe\nexclude_packages: pkg\nskills_dir: 3\n"
        "check:\n  external_schemas: raw\n",
    )
    assert set(paths(err)) >= {
        "$.include_packages",
        "$.exclude_packages",
        "$.skills_dir",
        "$.check.external_schemas",
    }


def test_project_must_be_string(tmp_path: Path) -> None:
    err = errors_for(tmp_path, "version: 1\nproject: 12\n")
    assert "$.project" in paths(err)


def test_error_message_names_file_and_fix(tmp_path: Path) -> None:
    err = errors_for(tmp_path, "version: 1\nfoo: bar\n")
    text = str(err)
    assert str(tmp_path / "agents_schema.yml") in text
    assert "$" in text


# --- templates ---------------------------------------------------------------------


def env_config(tmp_path: Path, credentials: str, schema: str, database: str | None = None) -> Path:
    db_line = f'    database: "{database}"\n' if database is not None else ""
    return write(
        tmp_path,
        f"version: 1\nproject: finance\nenvironments:\n  dev:\n    credentials: {credentials}\n"
        f'    schema: "{schema}"\n{db_line}',
    )


def test_template_target_schema(tmp_path: Path) -> None:
    env_config(tmp_path, "dbt_profile", "{{ target_schema }}_AGENTS")
    cfg = load_config(tmp_path)
    target = resolve_target(cfg, "dev", warehouse="duckdb", target_schema="dbt_alice")
    assert target.schema == "dbt_alice_AGENTS"
    assert target.database is None


def test_template_target_database(tmp_path: Path) -> None:
    env_config(tmp_path, "dbt_profile", "AGENTS", database="{{target_database}}_X")
    cfg = load_config(tmp_path)
    target = resolve_target(cfg, "dev", warehouse="duckdb", target_database="analytics")
    assert target.database == "analytics_X"


def test_template_project(tmp_path: Path) -> None:
    env_config(tmp_path, "env", "AGENTS_{{ project }}")
    target = resolve_target(load_config(tmp_path), "dev", warehouse="bigquery")
    assert target.schema == "AGENTS_finance"


def test_template_env(tmp_path: Path) -> None:
    env_config(tmp_path, "env", "AGENTS_{{  env  }}")
    target = resolve_target(load_config(tmp_path), "dev", warehouse="databricks")
    assert target.schema == "AGENTS_dev"


def test_template_multiple_variables(tmp_path: Path) -> None:
    env_config(tmp_path, "dbt_profile", "{{target_schema}}_{{ project }}_{{ env }}")
    target = resolve_target(load_config(tmp_path), "dev", warehouse="duckdb", target_schema="s")
    assert target.schema == "s_finance_dev"


@pytest.mark.parametrize(
    "template",
    [
        "{{ schema }}_AGENTS",
        "{{ env_var('X') }}",
        "{{ target_schema | upper }}",
        "{{ }}",
        "{{ target_schema",
        "AGENTS }}",
        "{% if x %}A{% endif %}",
    ],
)
def test_unknown_template_variables_are_config_errors(tmp_path: Path, template: str) -> None:
    env_config(tmp_path, "dbt_profile", template)
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path)
    assert "$.environments.dev.schema" in paths(exc.value)
    assert "target_schema" in str(exc.value)  # lists the supported variables


def test_unknown_template_in_database(tmp_path: Path) -> None:
    env_config(tmp_path, "dbt_profile", "AGENTS", database="{{ catalog }}")
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path)
    assert "$.environments.dev.database" in paths(exc.value)


@pytest.mark.parametrize("var", ["target_schema", "target_database"])
@pytest.mark.parametrize("field", ["schema", "database"])
def test_target_vars_with_env_credentials_are_errors(tmp_path: Path, var: str, field: str) -> None:
    if field == "schema":
        env_config(tmp_path, "env", f"{{{{ {var} }}}}_AGENTS")
    else:
        env_config(tmp_path, "env", "AGENTS", database=f"{{{{ {var} }}}}")
    with pytest.raises(ConfigError) as exc:
        load_config(tmp_path)
    assert f"$.environments.dev.{field}" in paths(exc.value)
    assert "dbt_profile" in str(exc.value)


def test_default_dev_env_renders(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, manifest_project_name="finance")
    target = resolve_target(cfg, "dev", warehouse="duckdb", target_schema="finance")
    assert target.schema == "finance_AGENTS"


def test_missing_target_value_is_config_error(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, manifest_project_name="finance")
    with pytest.raises(ConfigError) as exc:
        resolve_target(cfg, "dev", warehouse="duckdb", target_schema=None)
    assert "target_schema" in str(exc.value)


def test_unknown_environment_lists_available(tmp_path: Path) -> None:
    cfg = load_config(tmp_path, manifest_project_name="finance")
    with pytest.raises(ConfigError) as exc:
        resolve_target(cfg, "qa", warehouse="duckdb")
    assert "qa" in str(exc.value)
    assert "dev" in str(exc.value)
    assert "prod" in str(exc.value)


def test_snowflake_uppercases_rendered_values(tmp_path: Path) -> None:
    env_config(tmp_path, "dbt_profile", "{{ target_schema }}_agents", database="analytics")
    cfg = load_config(tmp_path)
    target = resolve_target(cfg, "dev", warehouse="snowflake", target_schema="dbt_alice")
    assert target.schema == "DBT_ALICE_AGENTS"
    assert target.database == "ANALYTICS"


@pytest.mark.parametrize("warehouse", ["bigquery", "databricks", "duckdb"])
def test_other_warehouses_keep_case(tmp_path: Path, warehouse: str) -> None:
    env_config(tmp_path, "dbt_profile", "{{ target_schema }}_agents", database="Analytics")
    cfg = load_config(tmp_path)
    target = resolve_target(cfg, "dev", warehouse=warehouse, target_schema="Dbt_Alice")
    assert target.schema == "Dbt_Alice_agents"
    assert target.database == "Analytics"
