"""GitLab CI template and example caller (SPEC 10.2, T23)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

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
        "package_version",
        "python_version",
        "dbt_adapter",
        "dbt_version",
        "mode",
        "environment",
    }
    assert inputs["project_dir"]["default"] == "."
    assert "default" not in inputs["package_version"], "package_version is required"
    assert "default" not in inputs["dbt_adapter"], "dbt_adapter is required"
    assert inputs["python_version"]["default"] == "3.12"
    assert inputs["dbt_version"]["default"] == ">=1.8,<2"
    assert inputs["environment"]["default"] == "prod"
    assert {"check", "publish"} <= set(inputs["mode"]["options"])
    assert inputs["dbt_adapter"]["options"] == [
        "dbt-snowflake",
        "dbt-bigquery",
        "dbt-databricks",
        "dbt-duckdb",
    ]


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
    assert "dbt-agents-schema[${EXTRA}]==$[[ inputs.package_version ]]" in text
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
    assert include["remote"].endswith("/v0.1.0/templates/gitlab/dbt-agents-schema.yml")
    assert include["inputs"]["package_version"] == "0.1.0"
    assert include["inputs"]["dbt_adapter"].startswith("dbt-")


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
