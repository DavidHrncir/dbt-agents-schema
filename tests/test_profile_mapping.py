"""dbt profile → connection mapping for Snowflake, BigQuery, Databricks (T19, SPEC 4.2)."""

from __future__ import annotations

import base64
import textwrap
from pathlib import Path
from typing import Any

import pytest

from dbt_agents_schema.credentials import Connection, CredentialError, from_dbt_profile

SECRET = "hunter2-secret"


def project_with(tmp_path: Path, output: str, *, target: str = "dev") -> Path:
    project = tmp_path / "proj"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: finance\nprofile: finance\n")
    body = textwrap.indent(textwrap.dedent(output).strip(), " " * 6)
    (project / "profiles.yml").write_text(
        f"finance:\n  target: {target}\n  outputs:\n    {target}:\n{body}\n"
    )
    return project


def resolve(tmp_path: Path, output: str, environ: dict[str, str] | None = None) -> Connection:
    project = project_with(tmp_path, output)
    return from_dbt_profile(project, profiles_dir=None, target=None, environ=environ or {})


def fails(tmp_path: Path, output: str, environ: dict[str, str] | None = None) -> str:
    with pytest.raises(CredentialError) as info:
        resolve(tmp_path, output, environ)
    message = str(info.value)
    assert SECRET not in message
    return message


SF = """
type: snowflake
account: acme-xy123
user: dev_user
role: TRANSFORMER
warehouse: WH
database: ANALYTICS
schema: dbt_dev
"""


# --- snowflake -------------------------------------------------------------------------------


def test_snowflake_password(tmp_path: Path) -> None:
    conn = resolve(tmp_path, SF + f"password: {SECRET}\n")
    assert conn.warehouse == "snowflake"
    assert dict(conn.settings) == {
        "account": "acme-xy123",
        "user": "dev_user",
        "role": "TRANSFORMER",
        "warehouse": "WH",
        "database": "ANALYTICS",
        "password": SECRET,
    }
    assert conn.target_schema == "dbt_dev"
    assert conn.target_database == "ANALYTICS"
    assert SECRET not in repr(conn)


def test_snowflake_private_key_path_and_passphrase(tmp_path: Path) -> None:
    conn = resolve(tmp_path, SF + "private_key_path: /keys/rsa.p8\nprivate_key_passphrase: pp\n")
    assert conn.settings["private_key_path"] == "/keys/rsa.p8"
    assert conn.settings["private_key_passphrase"] == "pp"
    assert "password" not in conn.settings


PEM = "-----BEGIN PRIVATE KEY-----\nMIIBVQIBADANBg\n-----END PRIVATE KEY-----\n"


def test_snowflake_private_key_pem(tmp_path: Path) -> None:
    conn = resolve(tmp_path, SF + "private_key: |\n" + textwrap.indent(PEM, "  "))
    assert conn.settings["private_key_pem"] == PEM


@pytest.mark.parametrize(
    ("passphrase", "label"), [(None, "PRIVATE KEY"), ("pp", "ENCRYPTED PRIVATE KEY")]
)
def test_snowflake_private_key_base64_der(
    tmp_path: Path, passphrase: str | None, label: str
) -> None:
    der_b64 = base64.b64encode(b"\x30\x82" + b"k" * 100).decode()
    extra = f"private_key_passphrase: {passphrase}\n" if passphrase else ""
    conn = resolve(tmp_path, SF + f"private_key: {der_b64}\n" + extra)
    pem = conn.settings["private_key_pem"]
    assert pem.startswith(f"-----BEGIN {label}-----\n")
    assert pem.rstrip().endswith(f"-----END {label}-----")
    assert "".join(pem.splitlines()[1:-1]) == der_b64


def test_snowflake_externalbrowser(tmp_path: Path) -> None:
    conn = resolve(tmp_path, SF + "authenticator: externalbrowser\n")
    assert conn.settings["authenticator"] == "externalbrowser"


def test_snowflake_unsupported_authenticator(tmp_path: Path) -> None:
    message = fails(tmp_path, SF + "authenticator: oauth\n")
    assert "outputs.dev.authenticator" in message and "externalbrowser" in message


def test_snowflake_needs_auth(tmp_path: Path) -> None:
    message = fails(tmp_path, SF)
    for key in ("password", "private_key_path", "private_key", "authenticator"):
        assert key in message


def test_snowflake_missing_required_key(tmp_path: Path) -> None:
    message = fails(tmp_path, SF.replace("account: acme-xy123\n", "") + f"password: {SECRET}\n")
    assert "outputs.dev.account" in message
    assert "profiles.yml" in message


def test_snowflake_writer_accepts_externalbrowser(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from fake_dbapi import FakeConnection, FakeDBError

    from dbt_agents_schema.config import ResolvedTarget
    from dbt_agents_schema.writers.factory import open_writer

    seen: dict[str, Any] = {}

    class FakeSF:
        Error = FakeDBError

        @staticmethod
        def connect(**kwargs: Any) -> FakeConnection:
            seen.update(kwargs)
            return FakeConnection()

    monkeypatch.setattr("dbt_agents_schema.writers.snowflake._connector", lambda: FakeSF)
    conn = resolve(tmp_path, SF + "authenticator: externalbrowser\n")
    open_writer(conn, ResolvedTarget(environment="dev", database=None, schema="DBT_DEV_AGENTS"))
    assert seen["authenticator"] == "externalbrowser"
    assert "password" not in seen and "private_key" not in seen


# --- bigquery --------------------------------------------------------------------------------


BQ = """
type: bigquery
project: my-gcp-proj
dataset: dbt_dev
location: EU
"""


def test_bigquery_oauth_is_adc(tmp_path: Path) -> None:
    conn = resolve(tmp_path, BQ + "method: oauth\n")
    assert conn.warehouse == "bigquery"
    assert dict(conn.settings) == {
        "project_id": "my-gcp-proj",
        "location": "EU",
        "auth_method": "adc",
    }
    assert conn.target_schema == "dbt_dev"
    assert conn.target_database == "my-gcp-proj"


def test_bigquery_service_account_keyfile(tmp_path: Path) -> None:
    conn = resolve(tmp_path, BQ + "method: service-account\nkeyfile: /keys/sa.json\n")
    assert conn.settings["keyfile"] == "/keys/sa.json"
    assert "auth_method" not in conn.settings


def test_bigquery_service_account_json(tmp_path: Path) -> None:
    conn = resolve(
        tmp_path,
        BQ
        + "method: service-account-json\nkeyfile_json:\n"
        + "  type: service_account\n  client_email: a@b.iam.gserviceaccount.com\n"
        + f"  private_key: {SECRET}\n",
    )
    assert conn.settings["credentials_json"]["client_email"] == "a@b.iam.gserviceaccount.com"


def test_bigquery_schema_fallback(tmp_path: Path) -> None:
    conn = resolve(tmp_path, BQ.replace("dataset:", "schema:") + "method: oauth\n")
    assert conn.target_schema == "dbt_dev"


def test_bigquery_service_account_needs_keyfile(tmp_path: Path) -> None:
    assert "outputs.dev.keyfile" in fails(tmp_path, BQ + "method: service-account\n")


def test_bigquery_service_account_json_needs_mapping(tmp_path: Path) -> None:
    message = fails(tmp_path, BQ + f"method: service-account-json\nkeyfile_json: {SECRET}\n")
    assert "outputs.dev.keyfile_json" in message


def test_bigquery_unsupported_method(tmp_path: Path) -> None:
    message = fails(tmp_path, BQ + "method: oauth-secrets\n")
    assert "outputs.dev.method" in message
    assert "oauth, service-account, service-account-json" in message


def test_bigquery_missing_project(tmp_path: Path) -> None:
    assert "outputs.dev.project" in fails(
        tmp_path, BQ.replace("project: my-gcp-proj\n", "") + "method: oauth\n"
    )


# --- databricks ------------------------------------------------------------------------------


DBX = """
type: databricks
host: dbc-123.cloud.databricks.com
http_path: /sql/1.0/warehouses/abc
catalog: main
schema: dbt_dev
"""


def test_databricks(tmp_path: Path) -> None:
    conn = resolve(tmp_path, DBX + f"token: {SECRET}\n")
    assert conn.warehouse == "databricks"
    assert dict(conn.settings) == {
        "host": "dbc-123.cloud.databricks.com",
        "http_path": "/sql/1.0/warehouses/abc",
        "catalog": "main",
        "token": SECRET,
    }
    assert conn.target_schema == "dbt_dev"
    assert conn.target_database == "main"


def test_databricks_without_catalog(tmp_path: Path) -> None:
    conn = resolve(tmp_path, DBX.replace("catalog: main\n", "") + f"token: {SECRET}\n")
    assert "catalog" not in conn.settings
    assert conn.target_database is None


def test_databricks_missing_token(tmp_path: Path) -> None:
    assert "outputs.dev.token" in fails(tmp_path, DBX)


# --- env_var, Jinja, adapters ----------------------------------------------------------------


def test_env_var_without_default(tmp_path: Path) -> None:
    conn = resolve(
        tmp_path,
        SF + "password: \"{{ env_var('SF_PASSWORD') }}\"\n",
        environ={"SF_PASSWORD": SECRET},
    )
    assert conn.settings["password"] == SECRET


def test_env_var_with_default(tmp_path: Path) -> None:
    output = (
        DBX.replace("catalog: main", "catalog: \"{{ env_var('DBX_CATALOG', 'dev_cat') }}\"")
        + "token: x\n"
    )
    conn = resolve(tmp_path, output)
    assert conn.settings["catalog"] == "dev_cat"
    assert conn.target_database == "dev_cat"


def test_env_var_overrides_default(tmp_path: Path) -> None:
    output = (
        DBX.replace("catalog: main", "catalog: \"{{ env_var('DBX_CATALOG', 'dev_cat') }}\"")
        + "token: x\n"
    )
    conn = resolve(tmp_path, output, environ={"DBX_CATALOG": "prod_cat"})
    assert conn.settings["catalog"] == "prod_cat"


def test_env_var_missing_names_variable_and_key(tmp_path: Path) -> None:
    message = fails(tmp_path, SF + "password: \"{{ env_var('SF_PASSWORD') }}\"\n")
    assert "SF_PASSWORD" in message
    assert "outputs.dev.password" in message


def test_unsupported_jinja_names_key(tmp_path: Path) -> None:
    message = fails(tmp_path, BQ + "method: \"{{ 'oauth' | upper }}\"\n")
    assert "outputs.dev.method" in message


def test_unsupported_adapter_lists_supported(tmp_path: Path) -> None:
    message = fails(tmp_path, "type: redshift\nhost: h\n")
    assert "redshift" in message
    for adapter in ("snowflake", "bigquery", "databricks", "duckdb"):
        assert adapter in message
