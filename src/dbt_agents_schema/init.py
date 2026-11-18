"""`init`: scaffold the config, an example skill and a CI caller (SPEC 7).

Writes `agents_schema.yml`, `agents/skills/example.md` and, for `--ci github|gitlab`,
the CI caller file; prints a `.pre-commit-config.yaml` snippet. Refuses to write
anything when any target exists, unless `force`. Pure file generation: no dbt, no
warehouse.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import cache
from importlib import resources
from pathlib import Path
from typing import Literal

from dbt_agents_schema import __version__
from dbt_agents_schema.config import (
    CONFIG_FILENAME,
    dbt_project_name,
    normalize_project_name,
    validate_project_key,
)

CI = Literal["github", "gitlab", "none"]
CALLER_PATHS: dict[str, str] = {
    "github": ".github/workflows/agents-schema.yml",
    "gitlab": ".gitlab-ci.yml",
}
EXAMPLE_SKILL = "agents/skills/example.md"
DEFAULT_ADAPTER = "dbt-snowflake"
SUPPORTED_ADAPTERS = ("dbt-snowflake", "dbt-bigquery", "dbt-databricks", "dbt-duckdb")
REPO_URL = "https://github.com/DavidHrncir/dbt-agents-schema"
DBT_CORE_SPEC = "dbt-core>=1.8,<2"


class InitError(Exception):
    """`init` cannot write its files (exit code 2)."""


@dataclass(frozen=True)
class InitResult:
    written: tuple[Path, ...]
    pre_commit_snippet: str
    notes: tuple[str, ...]


@cache
def _template(name: str) -> str:
    return (
        resources.files("dbt_agents_schema")
        .joinpath(f"init_templates/{name}")
        .read_text(encoding="utf-8")
    )


def render_caller(ci: str, *, version: str, adapter: str) -> str:
    """The CI caller file for `ci` pinned to `version`, installing `adapter`."""
    return (
        _template(f"{ci}.yml")
        .replace("@PACKAGE_VERSION@", version)
        .replace("@DBT_ADAPTER@", adapter)
    )


def pre_commit_snippet(*, version: str, adapter: str) -> str:
    return (
        "repos:\n"
        f"  - repo: {REPO_URL}\n"
        f"    rev: v{version}\n"
        "    hooks:\n"
        "      - id: dbt-agents-schema-check\n"
        f'        additional_dependencies: ["{DBT_CORE_SPEC}", "{adapter}"]\n'
    )


def init_project(project_dir: Path, *, ci: CI = "github", force: bool = False) -> InitResult:
    """Write the scaffold into `project_dir`. Raises InitError before writing anything."""
    notes: list[str] = []
    adapter = _adapter(project_dir, notes)
    files: dict[str, str] = {
        CONFIG_FILENAME: _config_text(project_dir, notes),
        EXAMPLE_SKILL: _template("example_skill.md"),
    }
    if ci != "none":
        files[CALLER_PATHS[ci]] = render_caller(ci, version=__version__, adapter=adapter)

    existing = [name for name in files if (project_dir / name).exists()]
    if existing and not force:
        raise InitError(
            f"refusing to overwrite {', '.join(str(project_dir / n) for n in existing)}; "
            "nothing was written. Pass --force to replace them"
        )

    written: list[Path] = []
    for name, text in files.items():
        path = project_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        written.append(path)
    return InitResult(
        written=tuple(written),
        pre_commit_snippet=pre_commit_snippet(version=__version__, adapter=adapter),
        notes=tuple(notes),
    )


def _config_text(project_dir: Path, notes: list[str]) -> str:
    name = dbt_project_name(project_dir)
    key = normalize_project_name(name) if name else None
    if key is not None and validate_project_key(key) is None:
        line = f"project: {key}"
    else:
        line = (
            "# project: my_project        # required: lowercase letter first, then up to 62\n"
            "#                             # lowercase letters, digits or underscores"
        )
        notes.append(
            f"set `project` in {CONFIG_FILENAME}: the dbt project name "
            f"{name!r} is not a valid project key"
            if name
            else f"set `project` in {CONFIG_FILENAME}: no dbt project name was found"
        )
    return _template("agents_schema.yml").replace("@PROJECT_LINE@", line)


def _adapter(project_dir: Path, notes: list[str]) -> str:
    """The dbt adapter package from `target/manifest.json`, else the default."""
    manifest = project_dir / "target" / "manifest.json"
    adapter_type = None
    try:
        metadata = json.loads(manifest.read_text(encoding="utf-8")).get("metadata", {})
        adapter_type = metadata.get("adapter_type") if isinstance(metadata, dict) else None
    except (OSError, ValueError, AttributeError):
        pass
    package = f"dbt-{adapter_type}" if isinstance(adapter_type, str) else None
    if package in SUPPORTED_ADAPTERS:
        return package
    notes.append(
        f"set the dbt adapter in the CI file (written as {DEFAULT_ADAPTER}); run "
        "`dbt parse` before `init` to detect it from the manifest"
    )
    return DEFAULT_ADAPTER
