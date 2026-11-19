"""Load and validate `agents_schema.yml` (SPEC section 3).

Pure logic: reads files and returns typed values; never touches a warehouse.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal

import jsonschema
import yaml

CONFIG_FILENAME = "agents_schema.yml"
PROJECT_PATTERN = r"^[a-z][a-z0-9_]{0,62}$"
TEMPLATE_VARIABLES = ("target_schema", "target_database", "project", "env")
PROFILE_ONLY_VARIABLES = frozenset({"target_schema", "target_database"})

Credentials = Literal["env", "dbt_profile"]
WarehouseType = Literal["snowflake", "bigquery", "databricks", "duckdb"]
Severity = Literal["error", "warning", "off"]
DescriptionScope = Literal["public", "all"]
ProjectSource = Literal["config", "manifest", "dbt_project"]

DEFAULT_RULES: Mapping[str, Severity] = MappingProxyType(
    {
        "config_missing": "warning",
        "missing_model_description": "warning",
        "missing_column_description": "warning",
        "sampled_values_in_description": "error",
        "measured_values_in_context": "warning",
        "manifest_target_mismatch": "error",
        "manifest_empty": "error",
        "column_meta_missing": "off",
        "skill_without_uses": "warning",
        "skill_unresolved_uses": "error",
    }
)

_DEFAULT_ENVIRONMENTS: dict[str, dict[str, str]] = {
    "prod": {"credentials": "env", "schema": "AGENTS"},
    "dev": {"credentials": "dbt_profile", "schema": "{{ target_schema }}_AGENTS"},
}

_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_STRAY_JINJA = re.compile(r"\{\{|\}\}|\{%|%\}|\{#|#\}")


@dataclass(frozen=True)
class ConfigIssue:
    """One problem in a config file. `path` is a JSON path such as `$.check.rules`."""

    path: str
    message: str
    line: int | None = None


class ConfigError(Exception):
    """The config (or a value derived from it) is invalid. Maps to exit code 2."""

    def __init__(self, file: Path | None, issues: list[ConfigIssue]) -> None:
        self.file = file
        self.issues = issues
        super().__init__(self._render())

    def _render(self) -> str:
        where = str(self.file) if self.file is not None else "agents_schema config"
        lines = [f"{where}: invalid config; fix the keys below"]
        for issue in self.issues:
            loc = f"line {issue.line}, " if issue.line is not None else ""
            lines.append(f"  {loc}at {issue.path}: {issue.message}")
        return "\n".join(lines)


@dataclass(frozen=True)
class ParseTarget:
    """Production shape for `dbt parse` in CI (`environments.<name>.parse_target`)."""

    type: WarehouseType
    name: str
    schema: str
    database: str | None = None
    location: str | None = None


@dataclass(frozen=True)
class EnvironmentConfig:
    name: str
    credentials: Credentials
    schema: str
    database: str | None = None
    parse_target: ParseTarget | None = None


@dataclass(frozen=True)
class MeasuredValuesConfig:
    """Settings for the `measured_values_in_context` rule (`check.measured_values`)."""

    min_number: int = 10
    allow: tuple[str, ...] = ()
    exempt: tuple[str, ...] = ()
    count_nouns: tuple[str, ...] = ()


@dataclass(frozen=True)
class CheckConfig:
    description_scope: DescriptionScope = "public"
    external_schemas: tuple[str, ...] = ()
    measured_values: MeasuredValuesConfig = field(default_factory=MeasuredValuesConfig)
    rules: Mapping[str, Severity] = field(default_factory=lambda: DEFAULT_RULES)


@dataclass(frozen=True)
class Config:
    path: Path
    found: bool
    version: int
    project: str
    project_source: ProjectSource
    owner: str | None
    description: str | None
    include_packages: bool
    exclude_packages: tuple[str, ...]
    skills_dir: str
    environments: Mapping[str, EnvironmentConfig]
    check: CheckConfig


@dataclass(frozen=True)
class ResolvedTarget:
    """Rendered database and schema for one environment. `database=None` means "use the
    default from the credentials"."""

    environment: str
    database: str | None
    schema: str


class _StrictBoolLoader(yaml.SafeLoader):
    """SafeLoader that reads only `true`/`false` as booleans, so `off` stays a string."""


_StrictBoolLoader.yaml_implicit_resolvers = {
    key: [(tag, regexp) for tag, regexp in resolvers if tag != "tag:yaml.org,2002:bool"]
    for key, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_StrictBoolLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool",
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


@cache
def config_schema() -> dict[str, Any]:
    """The JSON Schema shipped at `dbt_agents_schema/schemas/agents_schema.schema.json`."""
    text = (
        resources.files("dbt_agents_schema")
        .joinpath("schemas/agents_schema.schema.json")
        .read_text(encoding="utf-8")
    )
    schema: dict[str, Any] = json.loads(text)
    return schema


def normalize_project_name(name: str) -> str:
    """Default project key from a dbt project name: lowercase, `-` becomes `_`."""
    return name.lower().replace("-", "_")


def validate_project_key(key: str) -> str | None:
    """Return an error message if `key` fails the project pattern, else None."""
    if re.fullmatch(PROJECT_PATTERN, key):
        return None
    return (
        f"project key {key!r} must match {PROJECT_PATTERN} (lowercase letter first, "
        "then up to 62 lowercase letters, digits or underscores); set `project` in "
        f"{CONFIG_FILENAME}"
    )


def load_config(
    project_dir: Path,
    config_path: Path | None = None,
    *,
    manifest_project_name: str | None = None,
) -> Config:
    """Load `agents_schema.yml`, validate it and apply defaults.

    A missing file yields defaults with `found=False` (check reports `config_missing`).
    Raises ConfigError for YAML errors, JSON Schema violations, bad templates, or when no
    project key can be resolved. The project key pattern is not enforced here; call
    `validate_project_key` (check reports `project_key_invalid`).
    """
    path = config_path if config_path is not None else project_dir / CONFIG_FILENAME
    found = path.is_file()
    data: dict[str, Any] = {"version": 1}
    if found:
        data = _parse_and_validate(path)
    return _build(path, found, data, project_dir, manifest_project_name)


def default_config(
    project_dir: Path,
    config_path: Path | None = None,
    *,
    manifest_project_name: str | None = None,
) -> Config:
    """Every field at its default, ignoring any file content (`found` reports whether the
    file exists). `check` uses it to keep going after `config_invalid`."""
    path = config_path if config_path is not None else project_dir / CONFIG_FILENAME
    return _build(path, path.is_file(), {"version": 1}, project_dir, manifest_project_name)


def _build(
    path: Path,
    found: bool,
    data: Mapping[str, Any],
    project_dir: Path,
    manifest_project_name: str | None,
) -> Config:
    environments = _environments(path, data.get("environments", _DEFAULT_ENVIRONMENTS))
    check_raw: dict[str, Any] = data.get("check", {})
    rules: dict[str, Severity] = {**DEFAULT_RULES, **check_raw.get("rules", {})}
    mv_raw: dict[str, Any] = check_raw.get("measured_values", {})
    check = CheckConfig(
        description_scope=check_raw.get("description_scope", "public"),
        external_schemas=tuple(check_raw.get("external_schemas", ())),
        measured_values=MeasuredValuesConfig(
            min_number=int(mv_raw.get("min_number", 10)),
            allow=tuple(mv_raw.get("allow", ())),
            exempt=tuple(mv_raw.get("exempt", ())),
            count_nouns=tuple(mv_raw.get("count_nouns", ())),
        ),
        rules=MappingProxyType(rules),
    )
    project, source = _resolve_project(path, project_dir, data, manifest_project_name)
    return Config(
        path=path,
        found=found,
        version=int(data["version"]),
        project=project,
        project_source=source,
        owner=data.get("owner"),
        description=data.get("description"),
        include_packages=bool(data.get("include_packages", True)),
        exclude_packages=tuple(data.get("exclude_packages", ())),
        skills_dir=str(data.get("skills_dir", "agents/skills")),
        environments=MappingProxyType(environments),
        check=check,
    )


def resolve_target(
    config: Config,
    env_name: str,
    *,
    warehouse: str,
    target_schema: str | None = None,
    target_database: str | None = None,
) -> ResolvedTarget:
    """Render an environment's `schema`/`database` templates.

    `target_schema`/`target_database` come from the dbt profile (`credentials:
    dbt_profile`). Rendered values are uppercased on Snowflake.
    """
    env = config.environments.get(env_name)
    if env is None:
        available = ", ".join(sorted(config.environments))
        raise ConfigError(
            config.path,
            [
                ConfigIssue(
                    "$.environments",
                    f"environment {env_name!r} is not defined; available: {available}. "
                    f"Pass one of them to --env or add it to {CONFIG_FILENAME}",
                )
            ],
        )
    values = {
        "target_schema": target_schema,
        "target_database": target_database,
        "project": config.project,
        "env": env_name,
    }
    schema = _render(config.path, f"$.environments.{env_name}.schema", env.schema, values)
    database = (
        _render(config.path, f"$.environments.{env_name}.database", env.database, values)
        if env.database is not None
        else None
    )
    if warehouse == "snowflake":
        schema = schema.upper()
        database = database.upper() if database is not None else None
    return ResolvedTarget(environment=env_name, database=database, schema=schema)


# --- internals ---------------------------------------------------------------------


def _parse_and_validate(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8")
    try:
        loaded = yaml.load(text, Loader=_StrictBoolLoader)  # SafeLoader subclass
        root_node = yaml.compose(text, Loader=_StrictBoolLoader)
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        problem = getattr(exc, "problem", None) or str(exc)
        raise ConfigError(
            path, [ConfigIssue("$", f"YAML parse error: {problem}; fix the YAML syntax", line)]
        ) from exc
    data: Any = {} if loaded is None else loaded
    validator = jsonschema.Draft202012Validator(config_schema())
    errors = sorted(validator.iter_errors(data), key=lambda e: (e.json_path, e.message))
    if errors:
        issues = [
            ConfigIssue(e.json_path, e.message, _line_for(root_node, list(e.absolute_path)))
            for e in errors
        ]
        raise ConfigError(path, issues)
    assert isinstance(data, dict)
    issues = _parse_target_issues(data) + _regex_issues(data)
    if issues:
        for i, issue in enumerate(issues):
            parts: list[str | int] = [
                int(p) if p.isdigit() else p for p in re.split(r"[.\[\]]+", issue.path[2:]) if p
            ]
            issues[i] = ConfigIssue(issue.path, issue.message, _line_for(root_node, parts))
        raise ConfigError(path, issues)
    return data


def _parse_target_issues(data: Mapping[str, Any]) -> list[ConfigIssue]:
    """`parse_target` values are plain strings: any template syntax is an error."""
    issues: list[ConfigIssue] = []
    for env_name, spec in data.get("environments", {}).items():
        for key, value in spec.get("parse_target", {}).items():
            if isinstance(value, str) and _STRAY_JINJA.search(value):
                issues.append(
                    ConfigIssue(
                        f"$.environments.{env_name}.parse_target.{key}",
                        f"{value!r} contains template syntax; parse_target takes plain "
                        "strings only, so write the literal value",
                    )
                )
    return issues


def _regex_issues(data: Mapping[str, Any]) -> list[ConfigIssue]:
    """Every `check.measured_values` pattern must compile as a Python regex."""
    issues: list[ConfigIssue] = []
    measured = data.get("check", {}).get("measured_values", {})
    for key in ("allow", "exempt", "count_nouns"):
        for index, pattern in enumerate(measured.get(key, ())):
            try:
                re.compile(pattern)
            except re.error as exc:
                issues.append(
                    ConfigIssue(
                        f"$.check.measured_values.{key}[{index}]",
                        f"{pattern!r} is not a valid Python regular expression ({exc}); "
                        "fix or remove this entry",
                    )
                )
    return issues


def _line_for(node: yaml.Node | None, parts: list[str | int]) -> int | None:
    """1-based line of the YAML node at `parts`, or of the deepest ancestor found."""
    if node is None:
        return None
    line = node.start_mark.line + 1
    for part in parts:
        child: yaml.Node | None = None
        if isinstance(node, yaml.MappingNode):
            for key, value in node.value:
                if isinstance(key, yaml.ScalarNode) and key.value == str(part):
                    child = value
                    break
        elif (
            isinstance(node, yaml.SequenceNode) and isinstance(part, int) and part < len(node.value)
        ):
            child = node.value[part]
        if child is None:
            break
        node = child
        line = node.start_mark.line + 1
    return line


def _environments(path: Path, raw: Mapping[str, Any]) -> dict[str, EnvironmentConfig]:
    issues: list[ConfigIssue] = []
    envs: dict[str, EnvironmentConfig] = {}
    for name, spec in raw.items():
        pt_raw: Mapping[str, Any] | None = spec.get("parse_target")
        parse_target = (
            ParseTarget(
                type=pt_raw["type"],
                name=pt_raw.get("name", name),
                schema=pt_raw["schema"],
                database=pt_raw.get("database"),
                location=pt_raw.get("location"),
            )
            if pt_raw is not None
            else None
        )
        env = EnvironmentConfig(
            name=name,
            credentials=spec["credentials"],
            schema=spec["schema"],
            database=spec.get("database"),
            parse_target=parse_target,
        )
        for key in ("schema", "database"):
            template = getattr(env, key)
            if template is not None:
                issues.extend(
                    _template_issues(f"$.environments.{name}.{key}", template, env.credentials)
                )
        envs[name] = env
    if issues:
        raise ConfigError(path, issues)
    return envs


def _template_issues(json_path: str, template: str, credentials: Credentials) -> list[ConfigIssue]:
    supported = ", ".join(TEMPLATE_VARIABLES)
    issues: list[ConfigIssue] = []
    for match in _PLACEHOLDER.finditer(template):
        name = match.group(1).strip()
        if name not in TEMPLATE_VARIABLES:
            issues.append(
                ConfigIssue(
                    json_path,
                    f"unsupported template {match.group(0)!r}; use only {{{{ name }}}} "
                    f"with one of: {supported}",
                )
            )
        elif name in PROFILE_ONLY_VARIABLES and credentials != "dbt_profile":
            issues.append(
                ConfigIssue(
                    json_path,
                    f"{{{{ {name} }}}} is only available with `credentials: dbt_profile`; "
                    f"this environment uses `credentials: {credentials}`",
                )
            )
    if _STRAY_JINJA.search(_PLACEHOLDER.sub("", template)):
        issues.append(
            ConfigIssue(
                json_path,
                f"unsupported template syntax in {template!r}; use only {{{{ name }}}} "
                f"with one of: {supported}",
            )
        )
    return issues


def _render(path: Path, json_path: str, template: str, values: Mapping[str, str | None]) -> str:
    def substitute(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        value = values.get(name)
        if value is None:
            raise ConfigError(
                path,
                [
                    ConfigIssue(
                        json_path,
                        f"{{{{ {name} }}}} has no value: the dbt profile target defines no "
                        f"{name.removeprefix('target_')}; set it in profiles.yml or drop "
                        "the variable from the template",
                    )
                ],
            )
        return value

    return _PLACEHOLDER.sub(substitute, template)


def _resolve_project(
    path: Path,
    project_dir: Path,
    data: Mapping[str, Any],
    manifest_project_name: str | None,
) -> tuple[str, ProjectSource]:
    if "project" in data:
        return str(data["project"]), "config"
    if manifest_project_name:
        return normalize_project_name(manifest_project_name), "manifest"
    dbt_name = dbt_project_name(project_dir)
    if dbt_name:
        return normalize_project_name(dbt_name), "dbt_project"
    raise ConfigError(
        path,
        [
            ConfigIssue(
                "$.project",
                "cannot resolve the project key: the manifest has no metadata.project_name "
                f"and {project_dir / 'dbt_project.yml'} has no `name`; set `project` in "
                f"{CONFIG_FILENAME}",
            )
        ],
    )


def dbt_project_name(project_dir: Path) -> str | None:
    """`name` from `<project_dir>/dbt_project.yml`, or None if absent or unreadable."""
    path = project_dir / "dbt_project.yml"
    if not path.is_file():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError:
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name if isinstance(name, str) and name else None
