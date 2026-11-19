"""`write-parse-profile`: a minimal profiles.yml from WAREHOUSE_CREDENTIALS (SPEC 7, T22)."""

from __future__ import annotations

import json
import stat
from pathlib import Path
from typing import Any

import pytest
import yaml

from dbt_agents_schema.cli import main
from dbt_agents_schema.credentials import CredentialError
from dbt_agents_schema.parse_profile import (
    PLACEHOLDER,
    build_parse_profile,
    write_parse_profile,
)

SECRET = "hunter2-secret"


@pytest.fixture
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    p.mkdir()
    (p / "dbt_project.yml").write_text("name: finance\nprofile: finance_profile\n")
    return p


def creds(**doc: Any) -> dict[str, str]:
    return {"WAREHOUSE_CREDENTIALS": json.dumps(doc)}


def output_of(profile: dict[str, Any]) -> dict[str, Any]:
    assert list(profile) == ["finance_profile"], "profile name comes from dbt_project.yml"
    body = profile["finance_profile"]
    assert body["target"] in body["outputs"]
    out: dict[str, Any] = body["outputs"][body["target"]]
    return out


def test_snowflake(project: Path) -> None:
    env = creds(
        type="snowflake",
        account="ab12345.eu-west-1",
        user="SVC_DBT",
        warehouse="TRANSFORMING",
        database="ANALYTICS",
        role="TRANSFORMER",
        schema="MARTS",
        password=SECRET,
    )
    out = output_of(build_parse_profile(project, env))
    assert out == {
        "type": "snowflake",
        "account": "ab12345.eu-west-1",
        "user": "SVC_DBT",
        "warehouse": "TRANSFORMING",
        "database": "ANALYTICS",
        "role": "TRANSFORMER",
        "schema": "MARTS",
    }


def test_snowflake_key_pair_is_not_written(project: Path) -> None:
    env = creds(
        type="snowflake",
        account="a",
        user="u",
        warehouse="w",
        database="d",
        schema="s",
        private_key_pem=f"-----BEGIN PRIVATE KEY-----\n{SECRET}\n-----END PRIVATE KEY-----",
        private_key_passphrase=SECRET,
    )
    out = output_of(build_parse_profile(project, env))
    assert SECRET not in json.dumps(out)
    assert "role" not in out


def test_bigquery_service_account(project: Path) -> None:
    env = creds(
        type="big_query",
        projectId="acme-analytics",
        location="EU",
        dataset="marts",
        credentials_json={"type": "service_account", "private_key": SECRET},
    )
    out = output_of(build_parse_profile(project, env))
    assert out == {
        "type": "bigquery",
        "method": "oauth",
        "project": "acme-analytics",
        "dataset": "marts",
        "location": "EU",
    }


def test_bigquery_adc_schema_alias(project: Path) -> None:
    env = creds(type="bigquery", project_id="p", auth_method="adc", schema="marts")
    out = output_of(build_parse_profile(project, env))
    assert out["dataset"] == "marts"
    assert "location" not in out


def test_databricks(project: Path) -> None:
    env = creds(
        type="databricks",
        server_host_name="https://dbc-123.cloud.databricks.com/",
        httpPath="/sql/1.0/warehouses/abc",
        catalog="main",
        schema="marts",
        personal_access_token=SECRET,
    )
    out = output_of(build_parse_profile(project, env))
    assert SECRET not in json.dumps(out)
    assert out["type"] == "databricks"
    assert out["host"] == "dbc-123.cloud.databricks.com"
    assert out["http_path"] == "/sql/1.0/warehouses/abc"
    assert out["catalog"] == "main"
    assert out["schema"] == "marts"
    assert out["token"]  # dbt-databricks requires a token key; parse never connects


def test_duckdb(project: Path) -> None:
    env = creds(type="duckdb", path="/tmp/warehouse.duckdb", schema="marts")
    out = output_of(build_parse_profile(project, env))
    assert out == {"type": "duckdb", "path": "/tmp/warehouse.duckdb", "schema": "marts"}


def test_duckdb_schema_defaults_to_main(project: Path) -> None:
    out = output_of(build_parse_profile(project, creds(type="duckdb", path=":memory:")))
    assert out["schema"] == "main"


@pytest.mark.parametrize(
    "doc",
    [
        {
            "type": "snowflake",
            "account": "a",
            "user": "u",
            "warehouse": "w",
            "database": "d",
            "password": SECRET,
        },
        {"type": "bigquery", "project_id": "p", "auth_method": "adc"},
        {"type": "databricks", "host": "h", "http_path": "p", "catalog": "c", "token": SECRET},
    ],
)
def test_missing_schema_names_key_and_fix(project: Path, doc: dict[str, Any]) -> None:
    with pytest.raises(CredentialError) as info:
        build_parse_profile(project, creds(**doc))
    message = str(info.value)
    assert "WAREHOUSE_CREDENTIALS" in message and "schema" in message
    assert "DBT_PROFILES_YML" in message
    assert SECRET not in message


def test_invalid_credentials_are_rejected(project: Path) -> None:
    with pytest.raises(CredentialError, match="missing required keys"):
        build_parse_profile(project, creds(type="snowflake", account="a", schema="s"))


def test_missing_profile_in_dbt_project(tmp_path: Path) -> None:
    (tmp_path / "dbt_project.yml").write_text("name: x\n")
    with pytest.raises(CredentialError, match="profile"):
        build_parse_profile(tmp_path, creds(type="duckdb", path=":memory:"))


def test_write_creates_private_file_and_refuses_overwrite(project: Path, tmp_path: Path) -> None:
    out_dir = tmp_path / "profiles"
    path = write_parse_profile(project, out_dir, creds(type="duckdb", path=":memory:"))
    assert path == out_dir / "profiles.yml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = yaml.safe_load(path.read_text())
    assert loaded["finance_profile"]["outputs"][loaded["finance_profile"]["target"]]["type"] == (
        "duckdb"
    )
    with pytest.raises(CredentialError, match="already exists"):
        write_parse_profile(project, out_dir, creds(type="duckdb", path=":memory:"))


def test_cli(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv(
        "WAREHOUSE_CREDENTIALS",
        json.dumps(
            {
                "type": "databricks",
                "host": "h",
                "http_path": "p",
                "catalog": "c",
                "schema": "s",
                "token": SECRET,
            }
        ),
    )
    out_dir = tmp_path / "ci-profiles"
    code = main(["write-parse-profile", "--project-dir", str(project), "--out", str(out_dir)])
    captured = capsys.readouterr()
    assert code == 0
    assert str(out_dir / "profiles.yml") in captured.out
    text = (out_dir / "profiles.yml").read_text()
    assert SECRET not in text + captured.out + captured.err


def test_cli_error_exit_2(
    project: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv("WAREHOUSE_CREDENTIALS", raising=False)
    code = main(["write-parse-profile", "--project-dir", str(project), "--out", str(tmp_path)])
    assert code == 2
    assert "WAREHOUSE_CREDENTIALS" in capsys.readouterr().err


# --- placeholder profile from parse_target (T31) -------------------------------------


PARSE_TARGETS = {
    "snowflake": "type: snowflake\n      database: ANALYTICS\n      schema: FINANCE\n",
    "bigquery": (
        "type: bigquery\n      name: ci\n      database: acme-prod\n      schema: finance\n"
        "      location: EU\n"
    ),
    "databricks": "type: databricks\n      database: main\n      schema: finance\n",
    "duckdb": "type: duckdb\n      schema: main\n",
}


def with_parse_target(project: Path, warehouse: str) -> None:
    (project / "agents_schema.yml").write_text(
        "version: 1\nenvironments:\n  prod:\n    credentials: env\n    schema: AGENTS\n"
        "    parse_target:\n      " + PARSE_TARGETS[warehouse] + "  dev:\n"
        "    credentials: dbt_profile\n    schema: DEV_AGENTS\n"
    )


def run_wpp(project: Path, out_dir: Path, *extra: str) -> int:
    return main(
        ["write-parse-profile", "--project-dir", str(project), "--out", str(out_dir), *extra]
    )


def written(out_dir: Path) -> tuple[str, dict[str, Any]]:
    path = out_dir / "profiles.yml"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = yaml.safe_load(path.read_text())
    assert list(loaded) == ["finance_profile"]
    body = loaded["finance_profile"]
    assert list(body["outputs"]) == [body["target"]]
    return body["target"], body["outputs"][body["target"]]


@pytest.fixture
def no_creds(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WAREHOUSE_CREDENTIALS", raising=False)


@pytest.mark.usefixtures("no_creds")
def test_parse_target_snowflake(project: Path, tmp_path: Path) -> None:
    with_parse_target(project, "snowflake")
    assert run_wpp(project, tmp_path / "out", "--env", "prod") == 0
    target, out = written(tmp_path / "out")
    assert target == "prod"
    assert out["type"] == "snowflake"
    assert (out["database"], out["schema"]) == ("ANALYTICS", "FINANCE")
    assert out["password"] == PLACEHOLDER
    assert out["account"] == PLACEHOLDER and out["user"] == PLACEHOLDER


@pytest.mark.usefixtures("no_creds")
def test_parse_target_bigquery(project: Path, tmp_path: Path) -> None:
    with_parse_target(project, "bigquery")
    assert run_wpp(project, tmp_path / "out", "--env", "prod") == 0
    target, out = written(tmp_path / "out")
    assert target == "ci"
    assert out["type"] == "bigquery"
    assert out["method"] == "service-account-json"
    assert (out["project"], out["dataset"], out["location"]) == ("acme-prod", "finance", "EU")
    key = out["keyfile_json"]
    assert key["type"] == "service_account"
    assert key["project_id"] == "acme-prod"
    assert PLACEHOLDER in key["private_key"]


@pytest.mark.usefixtures("no_creds")
def test_parse_target_databricks(project: Path, tmp_path: Path) -> None:
    with_parse_target(project, "databricks")
    assert run_wpp(project, tmp_path / "out", "--env", "prod") == 0
    target, out = written(tmp_path / "out")
    assert target == "prod"
    assert out["type"] == "databricks"
    assert (out["catalog"], out["schema"]) == ("main", "finance")
    assert out["token"] == PLACEHOLDER
    assert out["host"] and out["http_path"]


@pytest.mark.usefixtures("no_creds")
def test_parse_target_duckdb(project: Path, tmp_path: Path) -> None:
    with_parse_target(project, "duckdb")
    assert run_wpp(project, tmp_path / "out", "--env", "prod") == 0
    target, out = written(tmp_path / "out")
    assert target == "prod"
    assert out == {"type": "duckdb", "path": ":memory:", "schema": "main"}


@pytest.mark.parametrize("warehouse", sorted(PARSE_TARGETS))
def test_parse_target_never_reads_warehouse_credentials(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, warehouse: str
) -> None:
    class Tripwire(dict[str, str]):
        def __getitem__(self, key: str) -> str:
            assert key != "WAREHOUSE_CREDENTIALS", "WAREHOUSE_CREDENTIALS was read"
            return super().__getitem__(key)

        def get(self, key: str, default: Any = None) -> Any:
            assert key != "WAREHOUSE_CREDENTIALS", "WAREHOUSE_CREDENTIALS was read"
            return super().get(key, default)

    with_parse_target(project, warehouse)
    env = Tripwire(WAREHOUSE_CREDENTIALS=json.dumps({"type": "duckdb", "path": SECRET}))
    monkeypatch.setattr("os.environ", env)
    assert run_wpp(project, tmp_path / "out", "--env", "prod") == 0
    assert SECRET not in (tmp_path / "out" / "profiles.yml").read_text()


def test_parse_target_ignores_set_credentials(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_parse_target(project, "snowflake")
    monkeypatch.setenv("WAREHOUSE_CREDENTIALS", "not even yaml: [")
    assert run_wpp(project, tmp_path / "out", "--env", "prod") == 0
    _, out = written(tmp_path / "out")
    assert out["database"] == "ANALYTICS"


def test_env_without_parse_target_uses_credentials(
    project: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with_parse_target(project, "snowflake")
    monkeypatch.setenv(
        "WAREHOUSE_CREDENTIALS", json.dumps({"type": "duckdb", "path": "x.db", "schema": "s"})
    )
    assert run_wpp(project, tmp_path / "out", "--env", "dev") == 0
    target, out = written(tmp_path / "out")
    assert target == "agents_schema_parse"
    assert out == {"type": "duckdb", "path": "x.db", "schema": "s"}


@pytest.mark.usefixtures("no_creds")
def test_unknown_env_is_exit_2(
    project: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    with_parse_target(project, "snowflake")
    assert run_wpp(project, tmp_path / "out", "--env", "staging") == 2
    err = capsys.readouterr().err
    assert "staging" in err and "prod" in err


@pytest.mark.usefixtures("no_creds")
def test_parse_target_refuses_overwrite(project: Path, tmp_path: Path) -> None:
    with_parse_target(project, "duckdb")
    assert run_wpp(project, tmp_path / "out", "--env", "prod") == 0
    assert run_wpp(project, tmp_path / "out", "--env", "prod") == 2
