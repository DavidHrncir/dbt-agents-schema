"""GitHub reusable workflow and example caller (SPEC 10, 10.1; T22, T34)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/dbt-agents-schema.yml"
CALLER = ROOT / "examples/github/agents-schema.yml"

# A caller written for v0.1.0 (the example shipped with that release).
V01_CALLER = """\
name: agents-schema
on:
  pull_request:
  push:
    branches: [main]
permissions:
  contents: read
jobs:
  check:
    if: github.event_name == 'pull_request'
    uses: DavidHrncir/dbt-agents-schema/.github/workflows/dbt-agents-schema.yml@v0.2.0
    with:
      mode: check
      package-version: "0.2.0"
      dbt-adapter: dbt-snowflake
    secrets:
      WAREHOUSE_CREDENTIALS: ${{ secrets.WAREHOUSE_CREDENTIALS }}
      DBT_PROFILES_YML: ${{ secrets.DBT_PROFILES_YML }}
  publish:
    if: github.event_name == 'push'
    concurrency:
      group: dbt-agents-schema-${{ github.repository }}
      cancel-in-progress: false
    uses: DavidHrncir/dbt-agents-schema/.github/workflows/dbt-agents-schema.yml@v0.2.0
    with:
      mode: publish
      package-version: "0.2.0"
      dbt-adapter: dbt-snowflake
      environment: prod
    secrets:
      WAREHOUSE_CREDENTIALS: ${{ secrets.WAREHOUSE_CREDENTIALS }}
      DBT_PROFILES_YML: ${{ secrets.DBT_PROFILES_YML }}
"""


def load(path: Path | str) -> dict[Any, Any]:
    text = path.read_text() if isinstance(path, Path) else path
    data = yaml.safe_load(text)
    assert isinstance(data, dict)
    # PyYAML reads the bare key `on` as True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def workflow_call() -> dict[str, Any]:
    call: dict[str, Any] = load(WORKFLOW)["on"]["workflow_call"]
    return call


def steps() -> list[dict[str, Any]]:
    (job,) = load(WORKFLOW)["jobs"].values()
    result: list[dict[str, Any]] = job["steps"]
    return result


def step(name: str) -> dict[str, Any]:
    return next(s for s in steps() if s.get("name") == name)


def validate_github(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "check_jsonschema",
            "--builtin-schema",
            "vendor.github-workflows",
            str(path),
        ],
        capture_output=True,
        text=True,
    )


# --- inputs ------------------------------------------------------------------------


def test_reusable_workflow_inputs_and_defaults() -> None:
    inputs = workflow_call()["inputs"]
    assert set(inputs) == {
        "project-dir",
        "python-version",
        "mode",
        "environment",
        "warehouse",
        "dbt-engine",
        "dbt-fusion-version",
        "dbt-version",
        "dbt-adapter",
        "package-version",
        "package-spec",
        "parse-env",
    }
    assert inputs["project-dir"]["default"] == "."
    assert inputs["python-version"]["default"] == "3.12"
    assert inputs["mode"]["required"] is True
    assert inputs["environment"]["default"] == "prod"
    assert inputs["dbt-engine"]["default"] == "fusion"
    assert inputs["dbt-fusion-version"]["default"]
    assert inputs["dbt-version"]["default"] == ">=1.8,<2"
    # Conditionally required inputs are validated by the first step, not by GitHub.
    for name in ("warehouse", "dbt-adapter", "package-version", "package-spec", "parse-env"):
        assert inputs[name].get("required", False) is False, name
        assert inputs[name]["default"] == "", name
    for spec in inputs.values():
        assert spec["type"] == "string"


def test_fusion_default_recorded_in_changelog() -> None:
    version = workflow_call()["inputs"]["dbt-fusion-version"]["default"]
    assert f"`{version}`" in (ROOT / "CHANGELOG.md").read_text()


def test_reusable_workflow_secrets() -> None:
    secrets = workflow_call()["secrets"]
    assert set(secrets) == {"WAREHOUSE_CREDENTIALS", "DBT_PROFILES_YML", "PACKAGE_REPO_TOKEN"}
    assert all(s["required"] is False for s in secrets.values())


# --- steps -------------------------------------------------------------------------


def test_step_order() -> None:
    names = [s.get("name") or s.get("uses", "").split("@")[0] for s in steps()]
    assert names == [
        "Validate inputs",
        "actions/checkout",
        "actions/setup-python",
        "Install dbt and dbt-agents-schema",
        "Write profiles.yml for dbt parse",
        "dbt deps",
        "dbt parse",
        "Check",
        "Publish (dry run)",
        "Publish",
        "Remove profiles.yml",
    ]


def test_step_commands() -> None:
    assert step("dbt parse")["run"].strip() == "dbt parse --no-partial-parse"
    assert step("dbt deps")["run"].strip() == "dbt deps"
    profile = step("Write profiles.yml for dbt parse")["run"]
    assert 'write-parse-profile --project-dir "$PROJECT_DIR"' in profile
    assert '--env "$AGENTS_ENVIRONMENT"' in profile
    assert "DBT_PROFILES_YML" in profile
    check = step("Check")
    assert check["if"] == "inputs.mode == 'check'"
    assert '--env "$AGENTS_ENVIRONMENT" --format github' in check["run"]
    dry, publish = step("Publish (dry run)"), step("Publish")
    assert dry["if"] == publish["if"] == "inputs.mode == 'publish'"
    assert dry["run"].rstrip().endswith("--dry-run")
    assert '--env "$AGENTS_ENVIRONMENT"' in publish["run"]
    assert "--dry-run" not in publish["run"]
    assert step("Remove profiles.yml")["if"] == "always()"


def test_install_step_engines() -> None:
    run = step("Install dbt and dbt-agents-schema")["run"]
    assert 'pip install "$AGENTS_PACKAGE_REQUIREMENT"' in run
    assert 'pip install "dbt-core${DBT_VERSION}" "$AGENTS_DBT_ADAPTER"' in run
    assert "public.cdn.getdbt.com/fs/install/install.sh" in run
    assert '--version "$DBT_FUSION_VERSION"' in run
    assert "insteadOf" in run and "--unset-all" in run


def test_inputs_never_interpolated_into_scripts() -> None:
    for s in steps():
        assert "${{ inputs." not in s.get("run", ""), s.get("name")
        assert "${{ secrets." not in s.get("run", ""), s.get("name")


def test_checkout_does_not_persist_credentials() -> None:
    checkout = next(s for s in steps() if s.get("uses", "").startswith("actions/checkout"))
    assert checkout["with"]["persist-credentials"] is False


def test_secrets_reach_only_the_steps_that_need_them() -> None:
    (job,) = load(WORKFLOW)["jobs"].values()
    assert "secrets." not in str(job.get("env", {}))
    holders = {
        s.get("name") or s.get("uses"): sorted(
            k for k, v in s.get("env", {}).items() if "secrets." in str(v)
        )
        for s in steps()
        if "secrets." in str(s.get("env", {}))
    }
    assert holders == {
        "Validate inputs": ["HAS_WAREHOUSE_CREDENTIALS"],
        "Install dbt and dbt-agents-schema": ["PACKAGE_REPO_TOKEN"],
        "Write profiles.yml for dbt parse": ["DBT_PROFILES_YML"],
        "Publish (dry run)": ["WAREHOUSE_CREDENTIALS"],
        "Publish": ["WAREHOUSE_CREDENTIALS"],
    }
    validate = step("Validate inputs")
    assert validate["env"]["HAS_WAREHOUSE_CREDENTIALS"].endswith("!= '' }}"), "presence only"
    # The values themselves: WAREHOUSE_CREDENTIALS only in the publish steps,
    # PACKAGE_REPO_TOKEN only in the install step.
    for s in steps():
        env = s.get("env", {})
        if "secrets.WAREHOUSE_CREDENTIALS }}" in str(env):
            assert s["name"] in ("Publish (dry run)", "Publish")
        if "secrets.PACKAGE_REPO_TOKEN" in str(env):
            assert s["name"] == "Install dbt and dbt-agents-schema"


# --- the Validate inputs script, run with bash -------------------------------------


def run_validate(tmp_path: Path, **inputs: str) -> tuple[subprocess.CompletedProcess[str], str]:
    github_env = tmp_path / "github_env"
    github_env.write_text("")
    env = {
        "PATH": os.environ["PATH"],
        "GITHUB_ENV": str(github_env),
        "RUNNER_TEMP": str(tmp_path),
        "MODE": "check",
        "HAS_WAREHOUSE_CREDENTIALS": "false",
        "WAREHOUSE": "",
        "DBT_ENGINE": "fusion",
        "DBT_ADAPTER": "",
        "PACKAGE_VERSION": "0.2.0",
        "PACKAGE_SPEC": "",
        "PARSE_ENV": "",
        **inputs,
    }
    bash = shutil.which("bash")
    assert bash is not None
    proc = subprocess.run(
        [bash, "-e", "-c", step("Validate inputs")["run"]],
        env=env,
        capture_output=True,
        text=True,
    )
    return proc, github_env.read_text()


def exported(github_env: str) -> dict[str, str]:
    """Parse GITHUB_ENV lines: NAME=value and NAME<<DELIM ... DELIM."""
    out: dict[str, str] = {}
    lines = github_env.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if "<<" in line and ("=" not in line or line.index("<<") < line.index("=")):
            name, delim = line.split("<<", 1)
            end = lines.index(delim, i + 1)
            out[name] = "\n".join(lines[i + 1 : end])
            i = end + 1
        else:
            name, value = line.split("=", 1)
            out[name] = value
            i += 1
    return out


def test_validate_derives_requirement(tmp_path: Path) -> None:
    proc, env = run_validate(tmp_path, WAREHOUSE="bigquery")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    values = exported(env)
    assert values["AGENTS_WAREHOUSE"] == "bigquery"
    assert values["AGENTS_DBT_ADAPTER"] == "dbt-bigquery"
    assert values["AGENTS_PACKAGE_REQUIREMENT"] == "dbt-agents-schema[bigquery]==0.2.0"
    assert values["DBT_PROFILES_DIR"].startswith(str(tmp_path))


def test_validate_v01_inputs_derive_warehouse(tmp_path: Path) -> None:
    proc, env = run_validate(tmp_path, DBT_ADAPTER="dbt-databricks", PACKAGE_VERSION="0.1.0")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    values = exported(env)
    assert values["AGENTS_WAREHOUSE"] == "databricks"
    assert values["AGENTS_DBT_ADAPTER"] == "dbt-databricks"
    assert values["AGENTS_PACKAGE_REQUIREMENT"] == "dbt-agents-schema[databricks]==0.1.0"


@pytest.mark.parametrize(
    ("spec", "requirement"),
    [
        (
            "git+https://github.com/acme/dbt-agents-schema@0123abc",
            "dbt-agents-schema[duckdb] @ git+https://github.com/acme/dbt-agents-schema@0123abc",
        ),
        (
            "dbt-agents-schema[duckdb] @ git+https://github.com/acme/x@abc",
            "dbt-agents-schema[duckdb] @ git+https://github.com/acme/x@abc",
        ),
    ],
)
def test_validate_package_spec_replaces_version(
    tmp_path: Path, spec: str, requirement: str
) -> None:
    proc, env = run_validate(tmp_path, WAREHOUSE="duckdb", PACKAGE_SPEC=spec, PACKAGE_VERSION="")
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert exported(env)["AGENTS_PACKAGE_REQUIREMENT"] == requirement


@pytest.mark.parametrize(
    ("inputs", "needle"),
    [
        ({"MODE": "deploy", "WAREHOUSE": "duckdb"}, "input mode"),
        ({}, "set input warehouse"),
        ({"WAREHOUSE": "postgres"}, "input warehouse must be"),
        ({"DBT_ADAPTER": "dbt-postgres"}, "input warehouse must be"),
        ({"WAREHOUSE": "duckdb", "DBT_ENGINE": "v2"}, "input dbt-engine"),
        ({"WAREHOUSE": "duckdb", "PACKAGE_VERSION": ""}, "package-version"),
        ({"WAREHOUSE": "duckdb", "MODE": "publish"}, "WAREHOUSE_CREDENTIALS"),
    ],
)
def test_validate_rejects_bad_inputs(tmp_path: Path, inputs: dict[str, str], needle: str) -> None:
    proc, _ = run_validate(tmp_path, **inputs)
    assert proc.returncode == 2
    assert "::error::" in proc.stdout and needle in proc.stdout


def test_parse_env_exports_lines(tmp_path: Path) -> None:
    parse_env = "# comment\n\nDBT_ENV_NAME=prod\n  SOURCE_DB=raw=1\nEMPTY=\n_UNDERSCORE=x y z\n"
    proc, env = run_validate(tmp_path, WAREHOUSE="duckdb", PARSE_ENV=parse_env)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    values = exported(env)
    assert values["DBT_ENV_NAME"] == "prod"
    assert values["SOURCE_DB"] == "raw=1"
    assert values["EMPTY"] == ""
    assert values["_UNDERSCORE"] == "x y z"
    assert "# comment" not in env


@pytest.mark.parametrize(
    "key",
    [
        "WAREHOUSE_CREDENTIALS",
        "DBT_PROFILES_YML",
        "DBT_PROFILES_DIR",
        "PACKAGE_REPO_TOKEN",
        "GITHUB_TOKEN",
        "GITHUB_ENV",
        "RUNNER_TEMP",
    ],
)
def test_parse_env_rejects_reserved_keys(tmp_path: Path, key: str) -> None:
    proc, _env = run_validate(tmp_path, WAREHOUSE="duckdb", PARSE_ENV=f"OK=1\n{key}=x\n")
    assert proc.returncode == 2
    assert key in proc.stdout and "reserved" in proc.stdout


@pytest.mark.parametrize("line", ["lower=x", "1ABC=x", "A-B=x", "NO_EQUALS", "A B=x"])
def test_parse_env_rejects_malformed_lines(tmp_path: Path, line: str) -> None:
    proc, _ = run_validate(tmp_path, WAREHOUSE="duckdb", PARSE_ENV=line)
    assert proc.returncode == 2
    assert "parse-env" in proc.stdout


# --- callers -----------------------------------------------------------------------


def test_example_caller() -> None:
    caller = load(CALLER)
    assert set(caller["on"]) == {"workflow_dispatch", "pull_request", "push"}
    assert caller["on"]["push"]["branches"] == ["main"]
    expected_paths = [
        "models/**",
        "macros/**",
        "agents/skills/**",
        "dbt_project.yml",
        "packages.yml",
        "package-lock.yml",
        "agents_schema.yml",
        ".github/workflows/agents-schema.yml",
    ]
    assert caller["on"]["pull_request"]["paths"] == expected_paths
    assert caller["on"]["push"]["paths"] == expected_paths
    (job,) = caller["jobs"].values()
    assert job["uses"].endswith(".github/workflows/dbt-agents-schema.yml@v0.2.0")
    w = job["with"]
    assert w["mode"] == "${{ github.event_name == 'pull_request' && 'check' || 'publish' }}"
    assert (w["warehouse"], w["package-version"], w["environment"]) == (
        "snowflake",
        "0.2.0",
        "prod",
    )
    assert job["secrets"] == {"WAREHOUSE_CREDENTIALS": "${{ secrets.WAREHOUSE_CREDENTIALS }}"}
    concurrency = caller["concurrency"]
    assert "github.ref" in concurrency["group"]
    assert "'agents-schema-publish'" in concurrency["group"]
    # Checks of the same PR cancel each other; publishes queue and are never cancelled.
    assert concurrency["cancel-in-progress"] == "${{ github.event_name == 'pull_request' }}"


def test_example_caller_inputs_exist_in_workflow() -> None:
    inputs = set(workflow_call()["inputs"])
    (job,) = load(CALLER)["jobs"].values()
    assert set(job["with"]) <= inputs
    assert set(job["secrets"]) <= set(workflow_call()["secrets"])


def test_v01_caller_still_validates(tmp_path: Path) -> None:
    path = tmp_path / "agents-schema.yml"
    path.write_text(V01_CALLER)
    result = validate_github(path)
    assert result.returncode == 0, result.stdout + result.stderr
    inputs = set(workflow_call()["inputs"])
    secrets = set(workflow_call()["secrets"])
    for job in load(V01_CALLER)["jobs"].values():
        assert set(job["with"]) <= inputs
        assert set(job["secrets"]) <= secrets


@pytest.mark.parametrize("path", [WORKFLOW, CALLER])
def test_files_pass_github_schema(path: Path) -> None:
    result = validate_github(path)
    assert result.returncode == 0, result.stdout + result.stderr
