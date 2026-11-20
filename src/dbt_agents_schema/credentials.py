"""Credential resolution (SPEC 4). Pure: reads text and mappings, never connects.

Error messages name files, keys and environment variables, never values: any value may
be a secret.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from dbt_agents_schema.plan import WAREHOUSES

ENV_VAR = "WAREHOUSE_CREDENTIALS"

# Adapters mapped from a dbt profile (SPEC 4.2).
PROFILE_ADAPTERS: tuple[str, ...] = ("snowflake", "bigquery", "databricks", "duckdb")


class CredentialError(Exception):
    """Credentials are missing or invalid (exit code 2)."""


@dataclass(frozen=True)
class Connection:
    """Resolved connection settings for one warehouse.

    `settings` holds normalized keys (SPEC 4.1 names) and may contain secrets; it is
    excluded from `repr`. `target_schema`/`target_database` feed config templates.
    """

    warehouse: str
    source: str
    settings: Mapping[str, Any] = field(repr=False)
    target_schema: str | None = None
    target_database: str | None = None


# --- credentials: env -----------------------------------------------------------------

# canonical key -> accepted aliases (canonical first)
_ALIASES: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "snowflake": {
        "account": ("account",),
        "user": ("user",),
        "warehouse": ("warehouse",),
        "database": ("database",),
        "role": ("role",),
        "password": ("password",),
        "private_key_pem": ("private_key_pem",),
        "private_key_passphrase": ("private_key_passphrase",),
        "private_key_path": ("private_key_path",),
    },
    "bigquery": {
        "project_id": ("project_id", "projectId", "project"),
        "location": ("location",),
        "credentials_json": ("credentials_json", "credentialsJson"),
        "auth_method": ("auth_method",),
    },
    "databricks": {
        "host": ("host", "server_host_name", "serverHostName"),
        "http_path": ("http_path", "httpPath"),
        "catalog": ("catalog",),
        "token": ("token", "access_token", "personal_access_token", "personalAccessToken"),
    },
    "duckdb": {"path": ("path",)},
}

_REQUIRED: Mapping[str, tuple[str, ...]] = {
    "snowflake": ("account", "user", "warehouse", "database"),
    "bigquery": ("project_id",),
    "databricks": ("host", "http_path", "catalog", "token"),
    "duckdb": ("path",),
}

_TYPE_ALIASES = {"big_query": "bigquery"}


def from_env(environ: Mapping[str, str]) -> Connection:
    """Parse `WAREHOUSE_CREDENTIALS` (YAML or JSON mapping), SPEC 4.1."""
    text = environ.get(ENV_VAR)
    if text is None or not text.strip():
        raise CredentialError(
            f"{ENV_VAR} is not set; set it to a YAML or JSON mapping with `type` and the "
            "connection keys for your warehouse (see the implementation guide), or use "
            "`credentials: dbt_profile` for this environment"
        )
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        # The YAML error message quotes the offending text, which may hold a secret.
        raise CredentialError(f"{ENV_VAR} is not valid YAML or JSON; fix its syntax") from None
    if not isinstance(data, dict):
        raise CredentialError(f"{ENV_VAR} must be a mapping of connection keys")
    raw_type = data.get("type")
    warehouse = _TYPE_ALIASES.get(str(raw_type), str(raw_type))
    if warehouse not in WAREHOUSES:
        raise CredentialError(
            f"{ENV_VAR}: unsupported `type` {raw_type!r}; supported: {', '.join(WAREHOUSES)}"
        )
    settings = _normalize(warehouse, data)
    _validate(warehouse, settings)
    return Connection(warehouse=warehouse, source=ENV_VAR, settings=settings)


def _normalize(warehouse: str, data: Mapping[str, Any]) -> dict[str, Any]:
    settings: dict[str, Any] = {}
    for canonical, aliases in _ALIASES[warehouse].items():
        for alias in aliases:
            value = data.get(alias)
            if value is not None and value != "":
                settings[canonical] = value
                break
    return settings


def _validate(warehouse: str, settings: Mapping[str, Any]) -> None:
    missing = [k for k in _REQUIRED[warehouse] if k not in settings]
    if missing:
        raise CredentialError(
            f"{ENV_VAR} ({warehouse}): missing required keys: {', '.join(missing)}"
        )
    if warehouse == "snowflake" and not any(
        k in settings for k in ("password", "private_key_pem", "private_key_path")
    ):
        raise CredentialError(
            f"{ENV_VAR} (snowflake): set one of password, private_key_pem or private_key_path"
        )
    if warehouse == "bigquery":
        if "credentials_json" not in settings and settings.get("auth_method") != "adc":
            raise CredentialError(
                f"{ENV_VAR} (bigquery): set credentials_json (a service-account mapping) "
                "or auth_method: adc"
            )
        if "credentials_json" in settings and not isinstance(settings["credentials_json"], dict):
            raise CredentialError(
                f"{ENV_VAR} (bigquery): credentials_json must be a mapping (the service "
                "account key file contents)"
            )


# --- credentials: dbt_profile ---------------------------------------------------------

_JINJA = re.compile(r"\{\{(.*?)\}\}|\{%.*?%\}|\{#.*?#\}", re.DOTALL)
_ENV_VAR_CALL = re.compile(
    r"""^\s*env_var\(\s*(['"])([^'"]+)\1\s*(?:,\s*(['"])(.*?)\3\s*)?\)\s*$""", re.DOTALL
)


def render_env_vars(text: str, environ: Mapping[str, str], key: str) -> str:
    """Render `{{ env_var('NAME') }}` and `{{ env_var('NAME', 'default') }}` in `text`.

    Any other Jinja is an error naming `key`.
    """

    def substitute(match: re.Match[str]) -> str:
        inner = match.group(1)
        call = _ENV_VAR_CALL.match(inner) if inner is not None else None
        if call is None:
            raise CredentialError(
                f"profiles.yml key {key}: only {{{{ env_var('NAME') }}}} and "
                f"{{{{ env_var('NAME', 'default') }}}} are supported; replace the other "
                "Jinja with a literal value"
            )
        name, default = call.group(2), call.group(4)
        value = environ.get(name)
        if value is not None:
            return value
        if default is not None:
            return default
        raise CredentialError(
            f"environment variable {name} (profiles.yml key {key}) is not set and has no "
            "default; set it or add a default"
        )

    return _JINJA.sub(substitute, text)


def _render_tree(value: Any, environ: Mapping[str, str], key: str) -> Any:
    if isinstance(value, str):
        return render_env_vars(value, environ, key)
    if isinstance(value, dict):
        return {k: _render_tree(v, environ, f"{key}.{k}") for k, v in value.items()}
    if isinstance(value, list):
        return [_render_tree(v, environ, f"{key}[{i}]") for i, v in enumerate(value)]
    return value


def profiles_path(project_dir: Path, profiles_dir: Path | None, environ: Mapping[str, str]) -> Path:
    """`--profiles-dir`, else `DBT_PROFILES_DIR`, else the project dir if it has one,
    else `~/.dbt/` (SPEC 4.2)."""
    if profiles_dir is not None:
        return profiles_dir / "profiles.yml"
    if environ.get("DBT_PROFILES_DIR"):
        return Path(environ["DBT_PROFILES_DIR"]) / "profiles.yml"
    if (project_dir / "profiles.yml").is_file():
        return project_dir / "profiles.yml"
    return Path.home() / ".dbt" / "profiles.yml"


def from_dbt_profile(
    project_dir: Path,
    *,
    profiles_dir: Path | None,
    target: str | None,
    environ: Mapping[str, str] | None = None,
) -> Connection:
    """Read the project's dbt profile output (SPEC 4.2)."""
    env = os.environ if environ is None else environ
    profile_name = _profile_name(project_dir)
    path = profiles_path(project_dir, profiles_dir, env)
    if not path.is_file():
        raise CredentialError(
            f"no profiles.yml at {path}; pass --profiles-dir or set DBT_PROFILES_DIR"
        )
    try:
        profiles = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise CredentialError(f"{path} is not readable YAML; fix its syntax") from None
    profile = profiles.get(profile_name) if isinstance(profiles, dict) else None
    if not isinstance(profile, dict):
        raise CredentialError(
            f"{path} has no profile {profile_name!r} (the `profile` in "
            f"{project_dir / 'dbt_project.yml'}); add it or fix the name"
        )
    outputs = profile.get("outputs")
    if not isinstance(outputs, dict) or not outputs:
        raise CredentialError(f"{path}: profile {profile_name!r} has no `outputs`")
    target_name = target
    if target_name is None:
        default = profile.get("target")
        target_name = (
            render_env_vars(default, env, f"{profile_name}.target")
            if isinstance(default, str)
            else None
        )
    if target_name is None:
        raise CredentialError(f"{path}: profile {profile_name!r} has no `target`; pass --target")
    output = outputs.get(target_name)
    if not isinstance(output, dict):
        raise CredentialError(
            f"{path}: profile {profile_name!r} has no output {target_name!r}; available: "
            f"{', '.join(str(k) for k in outputs)}. Pass one of them to --target"
        )
    rendered = _render_tree(output, env, f"outputs.{target_name}")
    adapter = rendered.get("type")
    if adapter not in PROFILE_ADAPTERS:
        raise CredentialError(
            f"{path}: output {target_name!r} uses adapter {adapter!r}; supported with "
            f"`credentials: dbt_profile`: {', '.join(PROFILE_ADAPTERS)}"
        )
    source = f"{path} (profile {profile_name}, target {target_name})"
    where = _ProfileKeys(path=path, target=target_name)
    if adapter == "snowflake":
        return _map_snowflake(rendered, source, where)
    if adapter == "bigquery":
        return _map_bigquery(rendered, source, where)
    if adapter == "databricks":
        return _map_databricks(rendered, source, where)
    return _map_duckdb(rendered, source)


@dataclass(frozen=True)
class _ProfileKeys:
    """Names profiles.yml keys in errors: `<path>: outputs.<target>.<key>`."""

    path: Path
    target: str

    def key(self, name: str) -> str:
        return f"outputs.{self.target}.{name}"

    def error(self, message: str) -> CredentialError:
        return CredentialError(f"{self.path}: {message}")

    def require(self, output: Mapping[str, Any], *names: str) -> None:
        missing = [self.key(n) for n in names if _blank(output.get(n))]
        if missing:
            raise self.error(f"missing required keys: {', '.join(missing)}")


def _blank(value: Any) -> bool:
    return value is None or value == ""


def _copy(output: Mapping[str, Any], names: tuple[str, ...]) -> dict[str, Any]:
    return {n: output[n] for n in names if not _blank(output.get(n))}


def _opt_str(value: Any) -> str | None:
    return None if _blank(value) else str(value)


def _map_snowflake(output: Mapping[str, Any], source: str, where: _ProfileKeys) -> Connection:
    where.require(output, "account", "user")
    settings = _copy(output, ("account", "user", "role", "warehouse", "database"))
    authenticator = output.get("authenticator")
    if not _blank(output.get("password")):
        settings["password"] = output["password"]
    elif not _blank(output.get("private_key_path")):
        settings["private_key_path"] = output["private_key_path"]
    elif not _blank(output.get("private_key")):
        settings["private_key_pem"] = _snowflake_pem(
            str(output["private_key"]), encrypted=not _blank(output.get("private_key_passphrase"))
        )
    elif not _blank(authenticator):
        if str(authenticator).lower() != "externalbrowser":
            raise where.error(
                f"{where.key('authenticator')}: only `externalbrowser` is supported; use "
                "password, private_key_path or private_key otherwise"
            )
        settings["authenticator"] = "externalbrowser"
    else:
        raise where.error(
            f"snowflake output {where.target!r} has no authentication; set one of "
            "password, private_key_path (+ private_key_passphrase), private_key, or "
            "authenticator: externalbrowser"
        )
    if "password" not in settings and not _blank(output.get("private_key_passphrase")):
        settings["private_key_passphrase"] = output["private_key_passphrase"]
    return Connection(
        warehouse="snowflake",
        source=source,
        settings=settings,
        target_schema=_opt_str(output.get("schema")),
        target_database=_opt_str(output.get("database")),
    )


def _snowflake_pem(value: str, *, encrypted: bool) -> str:
    """dbt-snowflake's `private_key` is PEM text or base64-encoded PKCS#8 DER."""
    if "-----BEGIN" in value:
        return value
    body = "".join(value.split())
    label = "ENCRYPTED PRIVATE KEY" if encrypted else "PRIVATE KEY"
    lines = [body[i : i + 64] for i in range(0, len(body), 64)]
    return f"-----BEGIN {label}-----\n" + "\n".join(lines) + f"\n-----END {label}-----\n"


_BIGQUERY_METHODS = ("oauth", "service-account", "service-account-json")


def _map_bigquery(output: Mapping[str, Any], source: str, where: _ProfileKeys) -> Connection:
    where.require(output, "project", "method")
    method = str(output["method"])
    settings: dict[str, Any] = {"project_id": output["project"]}
    if not _blank(output.get("location")):
        settings["location"] = output["location"]
    if method == "oauth":
        settings["auth_method"] = "adc"
    elif method == "service-account":
        where.require(output, "keyfile")
        settings["keyfile"] = output["keyfile"]
    elif method == "service-account-json":
        keyfile_json = output.get("keyfile_json")
        if not isinstance(keyfile_json, dict):
            raise where.error(
                f"{where.key('keyfile_json')} must be a mapping (the service-account key "
                "file contents)"
            )
        settings["credentials_json"] = keyfile_json
    else:
        raise where.error(
            f"{where.key('method')} {method!r} is not supported; use one of: "
            f"{', '.join(_BIGQUERY_METHODS)}"
        )
    dataset = output.get("dataset")
    if _blank(dataset):
        dataset = output.get("schema")
    return Connection(
        warehouse="bigquery",
        source=source,
        settings=settings,
        target_schema=_opt_str(dataset),
        target_database=str(output["project"]),
    )


def _map_databricks(output: Mapping[str, Any], source: str, where: _ProfileKeys) -> Connection:
    where.require(output, "host", "http_path", "token")
    settings = _copy(output, ("host", "http_path", "catalog", "token"))
    return Connection(
        warehouse="databricks",
        source=source,
        settings=settings,
        target_schema=_opt_str(output.get("schema")),
        target_database=_opt_str(output.get("catalog")),
    )


def _map_duckdb(output: Mapping[str, Any], source: str) -> Connection:
    path = str(output.get("path") or ":memory:")
    schema = output.get("schema") or "main"
    # dbt-duckdb names the catalog after the file stem (`memory` for `:memory:`).
    database = output.get("database") or _duckdb_catalog(path)
    return Connection(
        warehouse="duckdb",
        source=source,
        settings={"path": path},
        target_schema=str(schema),
        target_database=str(database) if database else None,
    )


def _duckdb_catalog(path: str) -> str:
    if path == ":memory:" or path.startswith(":memory:"):
        return "memory"
    base = path.partition("?")[0]
    if base.startswith("md:") or base.startswith("motherduck:"):
        return base.split(":", 1)[1] or "my_db"
    return Path(base).stem


def _profile_name(project_dir: Path) -> str:
    path = project_dir / "dbt_project.yml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        raise CredentialError(
            f"cannot read {path}; `credentials: dbt_profile` needs the dbt project's `profile`"
        ) from None
    name = data.get("profile") if isinstance(data, dict) else None
    if not isinstance(name, str) or not name:
        raise CredentialError(f"{path} has no `profile`; add one or use `credentials: env`")
    return name
