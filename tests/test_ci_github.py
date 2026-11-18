"""GitHub reusable workflow and example caller (SPEC 10.1, T22)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/dbt-agents-schema.yml"
CALLER = ROOT / "examples/github/agents-schema.yml"


def load(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text())
    assert isinstance(data, dict)
    # PyYAML reads the bare key `on` as True.
    if True in data:
        data["on"] = data.pop(True)
    return data


def test_reusable_workflow_inputs_and_secrets() -> None:
    call = load(WORKFLOW)["on"]["workflow_call"]
    inputs = call["inputs"]
    assert set(inputs) == {
        "project-dir",
        "package-version",
        "python-version",
        "dbt-adapter",
        "dbt-version",
        "mode",
        "environment",
    }
    assert inputs["project-dir"]["default"] == "."
    assert inputs["package-version"]["required"] is True
    assert inputs["python-version"]["default"] == "3.12"
    assert inputs["dbt-adapter"]["required"] is True
    assert inputs["dbt-version"]["default"] == ">=1.8,<2"
    assert inputs["environment"]["default"] == "prod"
    secrets = call["secrets"]
    assert set(secrets) == {"WAREHOUSE_CREDENTIALS", "DBT_PROFILES_YML"}
    assert secrets["DBT_PROFILES_YML"]["required"] is False


def test_reusable_workflow_steps() -> None:
    text = WORKFLOW.read_text()
    for needle in (
        "dbt-agents-schema[${EXTRA}]==${PACKAGE_VERSION}",
        "write-parse-profile",
        "dbt deps",
        "dbt parse",
        "check --project-dir",
        "--format github",
        "publish --project-dir",
    ):
        assert needle in text, needle
    # Inputs reach shell only through env, never interpolated into `run:` scripts.
    for job in load(WORKFLOW)["jobs"].values():
        for step in job["steps"]:
            assert "${{ inputs." not in step.get("run", ""), step.get("name")
            assert "${{ secrets." not in step.get("run", ""), step.get("name")


def test_example_caller() -> None:
    caller = load(CALLER)
    assert set(caller["on"]) == {"pull_request", "push"}
    assert caller["on"]["push"]["branches"] == ["main"]
    jobs = caller["jobs"]
    check, publish = jobs["check"], jobs["publish"]
    for job in (check, publish):
        assert job["uses"].endswith(".github/workflows/dbt-agents-schema.yml@v0.1.0")
        assert job["with"]["package-version"] == "0.1.0"
    assert check["with"]["mode"] == "check"
    assert check["if"] == "github.event_name == 'pull_request'"
    assert publish["with"]["mode"] == "publish"
    assert publish["if"] == "github.event_name == 'push'"
    assert publish["secrets"]["WAREHOUSE_CREDENTIALS"] == "${{ secrets.WAREHOUSE_CREDENTIALS }}"
    concurrency = publish["concurrency"]
    assert "github.repository" in concurrency["group"]
    assert concurrency["cancel-in-progress"] is False


def test_secrets_reach_only_the_steps_that_need_them() -> None:
    (job,) = load(WORKFLOW)["jobs"].values()
    assert "secrets." not in str(job.get("env", {}))
    holders = {
        step.get("name") or step.get("uses"): sorted(
            k for k, v in step.get("env", {}).items() if "secrets." in str(v)
        )
        for step in job["steps"]
        if "secrets." in str(step.get("env", {}))
    }
    assert holders == {
        "Validate inputs": ["HAS_WAREHOUSE_CREDENTIALS"],
        "Write profiles.yml for dbt parse": ["DBT_PROFILES_YML", "WAREHOUSE_CREDENTIALS"],
        "Publish": ["WAREHOUSE_CREDENTIALS"],
    }
    validate = next(s for s in job["steps"] if s.get("name") == "Validate inputs")
    assert validate["env"]["HAS_WAREHOUSE_CREDENTIALS"].endswith("!= '' }}"), "presence only"
