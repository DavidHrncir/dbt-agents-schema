"""`write-parse-profile`: a minimal dbt profile for `dbt parse` in CI (SPEC 7).

The profile is built from `WAREHOUSE_CREDENTIALS` (SPEC 4.1) so CI needs no separate
profiles secret. `dbt parse` never connects, so no secret is written: passwords, keys
and service-account JSON are left out, BigQuery uses `method: oauth`, and the Databricks
`token` (a required key in dbt-databricks) is a fixed placeholder.

The output's `schema` decides the schema dbt puts into each model's `relation_name`, so
it must be the project's real target schema. It is read from an optional `schema` key
(BigQuery also `dataset`) in `WAREHOUSE_CREDENTIALS`; the publisher itself ignores it.

With `--env` naming an environment that has `parse_target` (SPEC 3, 7), the profile is
built from `parse_target` alone: connection fields are obvious placeholders and
`WAREHOUSE_CREDENTIALS` is never read.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from dbt_agents_schema.config import ParseTarget
from dbt_agents_schema.credentials import ENV_VAR, CredentialError, _profile_name, from_env

PROFILES_FILENAME = "profiles.yml"
TARGET = "agents_schema_parse"
DATABRICKS_PARSE_TOKEN = "dbt-parse-only-not-a-token"
PLACEHOLDER = "dbt-parse-placeholder"
_PLACEHOLDER_KEY = f"-----BEGIN PRIVATE KEY-----\n{PLACEHOLDER}\n-----END PRIVATE KEY-----\n"


def build_placeholder_profile(project_dir: Path, parse_target: ParseTarget) -> dict[str, Any]:
    """The profiles.yml mapping for `project_dir` from `parse_target` (no credentials)."""
    output: dict[str, Any]
    if parse_target.type == "snowflake":
        output = {
            "type": "snowflake",
            "account": PLACEHOLDER,
            "user": PLACEHOLDER,
            "password": PLACEHOLDER,
            "warehouse": PLACEHOLDER,
            "database": parse_target.database,
            "schema": parse_target.schema,
        }
    elif parse_target.type == "bigquery":
        output = {
            "type": "bigquery",
            "method": "service-account-json",
            "project": parse_target.database,
            "dataset": parse_target.schema,
            "keyfile_json": {
                "type": "service_account",
                "project_id": parse_target.database,
                "private_key_id": PLACEHOLDER,
                "private_key": _PLACEHOLDER_KEY,
                "client_email": f"{PLACEHOLDER}@{parse_target.database}.iam.gserviceaccount.com",
                "client_id": "0",
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            },
        }
        if parse_target.location is not None:
            output["location"] = parse_target.location
    elif parse_target.type == "databricks":
        output = {
            "type": "databricks",
            "host": f"{PLACEHOLDER}.cloud.databricks.com",
            "http_path": f"/sql/1.0/warehouses/{PLACEHOLDER}",
            "catalog": parse_target.database,
            "schema": parse_target.schema,
            "token": PLACEHOLDER,
        }
    else:  # duckdb: the catalog is named after the path's stem (in memory: `memory`)
        path = f"{parse_target.database}.duckdb" if parse_target.database else ":memory:"
        output = {"type": "duckdb", "path": path, "schema": parse_target.schema}
    name = parse_target.name
    return {_profile_name(project_dir): {"target": name, "outputs": {name: output}}}


def build_parse_profile(project_dir: Path, environ: Mapping[str, str]) -> dict[str, Any]:
    """The profiles.yml mapping for `project_dir`, from `WAREHOUSE_CREDENTIALS`."""
    connection = from_env(environ)  # validates the document; errors never echo values
    raw = yaml.safe_load(environ[ENV_VAR])
    settings = connection.settings
    warehouse = connection.warehouse
    schema = _schema(warehouse, raw)

    output: dict[str, Any]
    if warehouse == "snowflake":
        output = {"type": "snowflake"}
        for key in ("account", "user", "warehouse", "database", "role"):
            if key in settings:
                output[key] = settings[key]
        output["schema"] = schema
    elif warehouse == "bigquery":
        output = {
            "type": "bigquery",
            "method": "oauth",
            "project": settings["project_id"],
            "dataset": schema,
        }
        if "location" in settings:
            output["location"] = settings["location"]
    elif warehouse == "databricks":
        output = {
            "type": "databricks",
            "host": _bare_host(str(settings["host"])),
            "http_path": settings["http_path"],
            "catalog": settings["catalog"],
            "schema": schema,
            "token": DATABRICKS_PARSE_TOKEN,
        }
    else:  # duckdb
        output = {"type": "duckdb", "path": settings["path"], "schema": schema}

    return {_profile_name(project_dir): {"target": TARGET, "outputs": {TARGET: output}}}


def write_parse_profile(
    project_dir: Path,
    out_dir: Path,
    environ: Mapping[str, str],
    *,
    parse_target: ParseTarget | None = None,
) -> Path:
    """Write `<out_dir>/profiles.yml` (mode 0600). Never overwrites an existing file.

    With `parse_target`, the profile holds placeholders and `environ` is not read.
    """
    profile = (
        build_placeholder_profile(project_dir, parse_target)
        if parse_target is not None
        else build_parse_profile(project_dir, environ)
    )
    path = out_dir / PROFILES_FILENAME
    if path.exists():
        raise CredentialError(
            f"{path} already exists; write-parse-profile never overwrites a profiles file. "
            "Pass an empty directory to --out"
        )
    out_dir.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(profile, sort_keys=False, default_flow_style=False)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _schema(warehouse: str, raw: Mapping[str, Any]) -> str:
    keys = ("dataset", "schema") if warehouse == "bigquery" else ("schema",)
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value:
            return value
    if warehouse == "duckdb":
        return "main"
    names = " or ".join(f"`{k}`" for k in keys)
    raise CredentialError(
        f"{ENV_VAR} ({warehouse}): write-parse-profile needs {names}, the dbt target "
        "schema your models build into (dbt writes it into every model's relation name). "
        f"Add it to {ENV_VAR}, or provide the DBT_PROFILES_YML secret instead"
    )


def _bare_host(host: str) -> str:
    for scheme in ("https://", "http://"):
        if host.startswith(scheme):
            host = host[len(scheme) :]
    return host.rstrip("/")
