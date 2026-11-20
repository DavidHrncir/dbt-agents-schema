"""GitLab CI template and example caller (SPEC 10.2, T23)."""

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
TEMPLATE = ROOT / "templates/gitlab/dbt-agents-schema.yml"
CALLER = ROOT / "examples/gitlab/.gitlab-ci.yml"
CHECK_JOB = "dbt-agents-schema-check"
PUBLISH_JOB = "dbt-agents-schema-publish"


def documents(path: Path) -> list[dict[str, Any]]:
    docs = list(yaml.safe_load_all(path.read_text()))
    assert all(isinstance(d, dict) for d in docs)
    return docs


def template() -> tuple[dict[str, Any], dict[str, Any]]:
    header, body = documents(TEMPLATE)
    return header, body


def test_header_declares_snake_case_inputs() -> None:
    header, _ = template()
    inputs = header["spec"]["inputs"]
    assert set(inputs) == {
        "project_dir",
        "python_version",
        "mode",
        "environment",
        "warehouse",
        "dbt_engine",
        "dbt_fusion_version",
        "dbt_version",
        "dbt_adapter",
        "package_version",
        "package_spec",
    }
    assert "parse_env" not in inputs, "included jobs already see the project's variables"
    assert inputs["project_dir"]["default"] == "."
    assert inputs["python_version"]["default"] == "3.12"
    assert inputs["dbt_version"]["default"] == ">=1.8,<2"
    assert inputs["environment"]["default"] == "prod"
    assert {"check", "publish"} <= set(inputs["mode"]["options"])
    assert inputs["dbt_engine"]["options"] == ["fusion", "core"]
    assert inputs["dbt_engine"]["default"] == "fusion"
    # Conditionally required inputs are validated by the job script.
    for name in ("warehouse", "dbt_adapter", "package_version", "package_spec"):
        assert inputs[name]["default"] == "", name


def test_fusion_default_matches_github_workflow() -> None:
    header, _ = template()
    github = yaml.safe_load((ROOT / ".github/workflows/dbt-agents-schema.yml").read_text())
    github_inputs = github[True]["workflow_call"]["inputs"]
    assert (
        header["spec"]["inputs"]["dbt_fusion_version"]["default"]
        == github_inputs["dbt-fusion-version"]["default"]
    )


def base_script() -> str:
    _, body = template()
    return "\n".join(body[".dbt-agents-schema-base"]["before_script"])


def test_engine_and_package_install() -> None:
    text = base_script()
    assert "public.cdn.getdbt.com/fs/install/install.sh" in text
    assert '--version "$DBT_FUSION_VERSION"' in text
    assert 'pip install "dbt-core${DBT_VERSION}" "$AGENTS_DBT_ADAPTER"' in text
    assert 'pip install "$AGENTS_PACKAGE_REQUIREMENT"' in text
    assert text.index("install.sh") < text.index("dbt deps")


def test_profile_parse_and_check_use_env() -> None:
    text = base_script()
    assert 'write-parse-profile --project-dir "$PROJECT_DIR" --env "$AGENTS_ENVIRONMENT"' in text
    assert "dbt parse --no-partial-parse" in text
    _, body = template()
    check = "\n".join(body[CHECK_JOB]["script"])
    assert '--env "$AGENTS_ENVIRONMENT"' in check


def test_publish_runs_dry_run_first() -> None:
    _, body = template()
    script = body[PUBLISH_JOB]["script"]
    dry = next(i for i, line in enumerate(script) if "--dry-run" in line)
    real = next(
        i
        for i, line in enumerate(script)
        if "publish --project-dir" in line and "--dry-run" not in line
    )
    assert dry < real
    for i in (dry, real):
        assert 'WAREHOUSE_CREDENTIALS="$_agents_schema_credentials"' in script[i]


def run_validate(tmp_path: Path, **values: str) -> subprocess.CompletedProcess[str]:
    """Run the template's input validation block with bash, inputs passed as the job
    variables GitLab would set."""
    header, body = template()
    base = body[".dbt-agents-schema-base"]
    defaults = {k: str(v.get("default", "")) for k, v in header["spec"]["inputs"].items()}
    env = {"PATH": os.environ["PATH"]}
    for key, value in base["variables"].items():
        for name, default in defaults.items():
            value = value.replace(f"$[[ inputs.{name} ]]", values.get(name, default))
        env[key] = value
    block = base["before_script"][0]
    assert "$[[ inputs." not in block, "inputs reach the script only through variables"
    bash = shutil.which("bash")
    assert bash is not None
    return subprocess.run(
        [
            bash,
            "-e",
            "-c",
            block + '\necho "REQ=$AGENTS_PACKAGE_REQUIREMENT ADAPTER=$AGENTS_DBT_ADAPTER"',
        ],
        env=env,
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize(
    ("values", "expected"),
    [
        (
            {"warehouse": "bigquery", "package_version": "0.2.0"},
            "REQ=dbt-agents-schema[bigquery]==0.2.0 ADAPTER=dbt-bigquery",
        ),
        (
            {"dbt_adapter": "dbt-duckdb", "package_version": "0.1.0"},
            "REQ=dbt-agents-schema[duckdb]==0.1.0 ADAPTER=dbt-duckdb",
        ),
        (
            {"warehouse": "snowflake", "package_spec": "git+https://gitlab.com/acme/das@abc"},
            "REQ=dbt-agents-schema[snowflake] @ git+https://gitlab.com/acme/das@abc "
            "ADAPTER=dbt-snowflake",
        ),
    ],
)
def test_input_validation_derives_requirement(
    tmp_path: Path, values: dict[str, str], expected: str
) -> None:
    proc = run_validate(tmp_path, **values)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert expected in proc.stdout


@pytest.mark.parametrize(
    ("values", "needle"),
    [
        ({"package_version": "0.2.0"}, "warehouse"),
        ({"warehouse": "postgres", "package_version": "0.2.0"}, "warehouse"),
        ({"warehouse": "duckdb"}, "package_version"),
    ],
)
def test_input_validation_rejects(tmp_path: Path, values: dict[str, str], needle: str) -> None:
    proc = run_validate(tmp_path, **values)
    assert proc.returncode == 2
    assert needle in proc.stderr


def test_job_names() -> None:
    _, body = template()
    jobs = {k for k in body if not k.startswith(".") and k not in {"variables", "default"}}
    assert jobs == {CHECK_JOB, PUBLISH_JOB}


def test_check_job_on_merge_requests_with_codequality() -> None:
    _, body = template()
    job = body[CHECK_JOB]
    conditions = [r["if"] for r in job["rules"] if "if" in r]
    assert any('$CI_PIPELINE_SOURCE == "merge_request_event"' in c for c in conditions)
    assert all("$CI_DEFAULT_BRANCH" not in c for c in conditions)
    reports = job["artifacts"]["reports"]
    assert reports["codequality"] == "gl-code-quality-report.json"
    assert job["artifacts"]["when"] == "always", "upload the report when check fails"
    script = "\n".join(job["script"])
    assert "--format gitlab" in script
    assert "--output gl-code-quality-report.json" in script or (
        '--output "$CI_PROJECT_DIR/gl-code-quality-report.json"' in script
    )
    assert "resource_group" not in job


def test_publish_job_on_default_branch_serialized() -> None:
    _, body = template()
    job = body[PUBLISH_JOB]
    conditions = [r["if"] for r in job["rules"] if "if" in r]
    assert any("$CI_COMMIT_BRANCH == $CI_DEFAULT_BRANCH" in c for c in conditions)
    assert all("merge_request_event" not in c for c in conditions)
    assert job["resource_group"] == "dbt-agents-schema-$CI_PROJECT_PATH"
    script = "\n".join(job["script"])
    assert "publish --project-dir" in script and "--env" in script


def test_jobs_install_pinned_package_and_parse() -> None:
    text = TEMPLATE.read_text()
    assert "dbt-agents-schema[${EXTRA}]==$PACKAGE_VERSION" in text
    assert 'PACKAGE_VERSION: "$[[ inputs.package_version ]]"' in text
    assert "dbt parse --no-partial-parse" in text
    for needle in ("dbt deps", "dbt parse", "write-parse-profile", "WAREHOUSE_CREDENTIALS"):
        assert needle in text, needle
    assert "component:" not in text, "must not depend on the GitLab CI/CD Catalog"


def test_every_input_is_used() -> None:
    header, _ = template()
    _, body_text = TEMPLATE.read_text().split("\n---\n", 1)
    for name in header["spec"]["inputs"]:
        assert f"$[[ inputs.{name} ]]" in body_text, name


def test_example_caller() -> None:
    (caller,) = documents(CALLER)
    (include,) = caller["include"]
    assert include["remote"].endswith("/v0.2.3/templates/gitlab/dbt-agents-schema.yml")
    assert include["inputs"]["package_version"] == "0.2.3"
    assert include["inputs"]["warehouse"] == "snowflake"
    header, _ = template()
    assert set(include["inputs"]) <= set(header["spec"]["inputs"])


def test_validator_checks_each_document(tmp_path: Path) -> None:
    script = ROOT / "scripts/validate_gitlab_ci.py"
    ok = subprocess.run([sys.executable, str(script), str(TEMPLATE), str(CALLER)])
    assert ok.returncode == 0
    broken = tmp_path / "broken.yml"
    broken.write_text(
        TEMPLATE.read_text().replace("resource_group:", "resource_groupp:", 1), encoding="utf-8"
    )
    bad = subprocess.run([sys.executable, str(script), str(broken)], capture_output=True)
    assert bad.returncode != 0, "an invalid job key in the body document must fail"


def test_secrets_are_unset_before_dbt_runs() -> None:
    _, body = template()
    before = "\n".join(body[".dbt-agents-schema-base"]["before_script"])
    assert '[ -f "$WAREHOUSE_CREDENTIALS" ]' in before, "file-type variables are read"
    unset = before.index("unset WAREHOUSE_CREDENTIALS DBT_PROFILES_YML")
    assert before.index("write-parse-profile") < unset < before.index("&& dbt deps &&")
    publish = "\n".join(body[PUBLISH_JOB]["script"])
    assert 'WAREHOUSE_CREDENTIALS="$_agents_schema_credentials" dbt-agents-schema publish' in (
        publish
    )
