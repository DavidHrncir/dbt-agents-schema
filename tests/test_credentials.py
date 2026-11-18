"""Credential resolution (SPEC 4; T14). DuckDB profile mapping only; T19 adds the rest."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dbt_agents_schema.credentials import (
    CredentialError,
    from_dbt_profile,
    from_env,
    render_env_vars,
)

SNOWFLAKE = {
    "type": "snowflake",
    "account": "acme",
    "user": "svc",
    "warehouse": "wh",
    "database": "ANALYTICS",
    "password": "hunter2-secret",
}


# --- credentials: env --------------------------------------------------------------------


def test_env_missing_variable() -> None:
    with pytest.raises(CredentialError, match="WAREHOUSE_CREDENTIALS"):
        from_env({})


@pytest.mark.parametrize(
    "text", ["type: duckdb\npath: x.duckdb\n", '{"type":"duckdb","path":"x.duckdb"}']
)
def test_env_yaml_or_json(text: str) -> None:
    conn = from_env({"WAREHOUSE_CREDENTIALS": text})
    assert conn.warehouse == "duckdb"
    assert conn.settings["path"] == "x.duckdb"
    assert conn.target_schema is None


def test_env_not_a_mapping() -> None:
    with pytest.raises(CredentialError, match="mapping"):
        from_env({"WAREHOUSE_CREDENTIALS": "[1, 2]"})


def test_env_invalid_yaml_does_not_echo_text() -> None:
    with pytest.raises(CredentialError) as info:
        from_env({"WAREHOUSE_CREDENTIALS": "password: hunter2-secret\n  bad: [indent"})
    assert "hunter2-secret" not in str(info.value)


def test_env_unknown_type_lists_supported() -> None:
    with pytest.raises(CredentialError, match="snowflake, bigquery, databricks, duckdb"):
        from_env({"WAREHOUSE_CREDENTIALS": json.dumps({"type": "oracle"})})


def test_env_snowflake() -> None:
    conn = from_env({"WAREHOUSE_CREDENTIALS": json.dumps(SNOWFLAKE)})
    assert conn.warehouse == "snowflake"
    assert conn.settings["account"] == "acme"
    assert "hunter2-secret" not in repr(conn)


def test_env_snowflake_missing_keys_named_without_values() -> None:
    creds = {k: v for k, v in SNOWFLAKE.items() if k not in ("user", "warehouse")}
    with pytest.raises(CredentialError) as info:
        from_env({"WAREHOUSE_CREDENTIALS": json.dumps(creds)})
    message = str(info.value)
    assert "user" in message and "warehouse" in message
    assert "hunter2-secret" not in message


def test_env_snowflake_needs_one_auth() -> None:
    creds = {k: v for k, v in SNOWFLAKE.items() if k != "password"}
    with pytest.raises(CredentialError, match="private_key_pem"):
        from_env({"WAREHOUSE_CREDENTIALS": json.dumps(creds)})


@pytest.mark.parametrize("type_", ["bigquery", "big_query"])
@pytest.mark.parametrize("project_key", ["project_id", "projectId", "project"])
def test_env_bigquery_aliases(type_: str, project_key: str) -> None:
    conn = from_env(
        {
            "WAREHOUSE_CREDENTIALS": json.dumps(
                {"type": type_, project_key: "p1", "credentialsJson": {"type": "service_account"}}
            )
        }
    )
    assert conn.warehouse == "bigquery"
    assert conn.settings["project_id"] == "p1"
    assert conn.settings["credentials_json"] == {"type": "service_account"}


def test_env_bigquery_adc() -> None:
    conn = from_env(
        {
            "WAREHOUSE_CREDENTIALS": json.dumps(
                {"type": "bigquery", "project_id": "p", "auth_method": "adc"}
            )
        }
    )
    assert conn.settings["auth_method"] == "adc"


def test_env_bigquery_needs_auth() -> None:
    with pytest.raises(CredentialError, match="credentials_json"):
        from_env({"WAREHOUSE_CREDENTIALS": json.dumps({"type": "bigquery", "project_id": "p"})})


@pytest.mark.parametrize(
    ("host", "path", "token"),
    [
        ("host", "http_path", "token"),
        ("server_host_name", "httpPath", "access_token"),
        ("serverHostName", "http_path", "personal_access_token"),
        ("host", "http_path", "personalAccessToken"),
    ],
)
def test_env_databricks_aliases(host: str, path: str, token: str) -> None:
    conn = from_env(
        {
            "WAREHOUSE_CREDENTIALS": json.dumps(
                {"type": "databricks", host: "h", path: "/sql", "catalog": "main", token: "dapi-x"}
            )
        }
    )
    assert dict(conn.settings) == {
        "host": "h",
        "http_path": "/sql",
        "catalog": "main",
        "token": "dapi-x",
    }
    assert "dapi-x" not in repr(conn)


def test_env_duckdb_needs_path() -> None:
    with pytest.raises(CredentialError, match="path"):
        from_env({"WAREHOUSE_CREDENTIALS": json.dumps({"type": "duckdb"})})


# --- env_var rendering --------------------------------------------------------------------


def test_render_env_var_with_and_without_default() -> None:
    env = {"A": "1"}
    assert render_env_vars("{{ env_var('A') }}", env, "k") == "1"
    assert render_env_vars('{{ env_var("B", "dflt") }}', env, "k") == "dflt"
    assert render_env_vars("x-{{env_var('A')}}-y", env, "k") == "x-1-y"
    assert render_env_vars("plain", env, "k") == "plain"


def test_render_env_var_missing() -> None:
    with pytest.raises(CredentialError, match=r"MISSING.*outputs\.dev\.password"):
        render_env_vars("{{ env_var('MISSING') }}", {}, "outputs.dev.password")


@pytest.mark.parametrize("text", ["{{ target.name }}", "{% if x %}a{% endif %}", "{{ var('x') }}"])
def test_render_unsupported_jinja(text: str) -> None:
    with pytest.raises(CredentialError, match=r"outputs\.dev\.schema"):
        render_env_vars(text, {}, "outputs.dev.schema")


# --- credentials: dbt_profile -------------------------------------------------------------


def write_project(tmp_path: Path, profiles: str, *, profile: str = "finance") -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "dbt_project.yml").write_text(f"name: finance\nprofile: {profile}\n")
    (tmp_path / "profiles").mkdir()
    (tmp_path / "profiles" / "profiles.yml").write_text(profiles)
    return project


DUCK_PROFILE = """
finance:
  target: dev
  outputs:
    dev:
      type: duckdb
      path: "{{ env_var('DB_PATH', 'default.duckdb') }}"
      schema: finance
    ci:
      type: duckdb
      path: ci.duckdb
"""


def test_profile_duckdb_default_target(tmp_path: Path) -> None:
    project = write_project(tmp_path, DUCK_PROFILE)
    conn = from_dbt_profile(project, profiles_dir=tmp_path / "profiles", target=None, environ={})
    assert conn.warehouse == "duckdb"
    assert conn.settings["path"] == "default.duckdb"
    assert conn.target_schema == "finance"


def test_profile_explicit_target_and_schema_default(tmp_path: Path) -> None:
    project = write_project(tmp_path, DUCK_PROFILE)
    conn = from_dbt_profile(project, profiles_dir=tmp_path / "profiles", target="ci", environ={})
    assert conn.settings["path"] == "ci.duckdb"
    assert conn.target_schema == "main"


def test_profile_env_var_rendered(tmp_path: Path) -> None:
    project = write_project(tmp_path, DUCK_PROFILE)
    conn = from_dbt_profile(
        project, profiles_dir=tmp_path / "profiles", target=None, environ={"DB_PATH": "x.duckdb"}
    )
    assert conn.settings["path"] == "x.duckdb"


def test_profiles_dir_from_env_then_project_dir(tmp_path: Path) -> None:
    project = write_project(tmp_path, DUCK_PROFILE)
    conn = from_dbt_profile(
        project,
        profiles_dir=None,
        target=None,
        environ={"DBT_PROFILES_DIR": str(tmp_path / "profiles")},
    )
    assert conn.target_schema == "finance"
    (project / "profiles.yml").write_text(DUCK_PROFILE.replace("schema: finance", "schema: local"))
    conn = from_dbt_profile(project, profiles_dir=None, target=None, environ={})
    assert conn.target_schema == "local"


def test_profiles_default_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    project = write_project(tmp_path, DUCK_PROFILE)
    home = tmp_path / "home"
    (home / ".dbt").mkdir(parents=True)
    (home / ".dbt" / "profiles.yml").write_text(
        DUCK_PROFILE.replace("schema: finance", "schema: h")
    )
    monkeypatch.setattr(Path, "home", lambda: home)
    conn = from_dbt_profile(project, profiles_dir=None, target=None, environ={})
    assert conn.target_schema == "h"


def test_profile_file_missing(tmp_path: Path) -> None:
    project = write_project(tmp_path, DUCK_PROFILE)
    with pytest.raises(CredentialError, match=r"profiles.yml"):
        from_dbt_profile(project, profiles_dir=tmp_path / "nowhere", target=None, environ={})


def test_profile_name_missing(tmp_path: Path) -> None:
    project = write_project(tmp_path, DUCK_PROFILE, profile="other")
    with pytest.raises(CredentialError, match="'other'"):
        from_dbt_profile(project, profiles_dir=tmp_path / "profiles", target=None, environ={})


def test_profile_target_missing(tmp_path: Path) -> None:
    project = write_project(tmp_path, DUCK_PROFILE)
    with pytest.raises(CredentialError, match=r"'prod'.*dev, ci|'prod'.*ci, dev"):
        from_dbt_profile(project, profiles_dir=tmp_path / "profiles", target="prod", environ={})


def test_profile_unsupported_adapter(tmp_path: Path) -> None:
    project = write_project(
        tmp_path, "finance:\n  target: dev\n  outputs:\n    dev:\n      type: postgres\n"
    )
    with pytest.raises(CredentialError, match=r"postgres.*duckdb"):
        from_dbt_profile(project, profiles_dir=tmp_path / "profiles", target=None, environ={})


def test_profile_unsupported_jinja_names_key(tmp_path: Path) -> None:
    project = write_project(
        tmp_path, DUCK_PROFILE.replace("schema: finance", 'schema: "{{ target.name }}"')
    )
    with pytest.raises(CredentialError, match=r"outputs\.dev\.schema"):
        from_dbt_profile(project, profiles_dir=tmp_path / "profiles", target=None, environ={})
