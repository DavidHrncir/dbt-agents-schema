"""`init` and the pre-commit hook (SPEC 7, 10.3, T24)."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from fakes import MANIFESTS, PROJECTS

from dbt_agents_schema import __version__
from dbt_agents_schema.check import check_project
from dbt_agents_schema.cli import main
from dbt_agents_schema.config import load_config
from dbt_agents_schema.init import CALLER_PATHS, render_caller

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def project(tmp_path: Path) -> Path:
    """The marketing fixture without its agents_schema.yml and skills, plus a manifest."""
    dest = tmp_path / "marketing"
    shutil.copytree(PROJECTS / "marketing", dest)
    (dest / "agents_schema.yml").unlink()
    shutil.rmtree(dest / "agents")
    (dest / "target").mkdir()
    shutil.copy(MANIFESTS / "marketing.json", dest / "target" / "manifest.json")
    return dest


def run_init(project: Path, *args: str) -> int:
    return main(["init", "--project-dir", str(project), *args])


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


def validate_gitlab(path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(ROOT / "scripts/validate_gitlab_ci.py"), str(path)],
        capture_output=True,
        text=True,
    )


@pytest.mark.parametrize("ci", ["github", "gitlab"])
def test_init_output_passes_check_and_ci_validation(
    project: Path, ci: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run_init(project, "--ci", ci) == 0
    out = capsys.readouterr().out

    config = load_config(project)
    assert config.found and config.project == "marketing"
    assert (project / "agents/skills/example.md").is_file()
    result = check_project(project)
    assert not result.has_errors, [f.message for f in result.findings if f.severity == "error"]
    assert main(["check", "--project-dir", str(project)]) == 0

    caller = project / CALLER_PATHS[ci]
    assert caller.is_file()
    validated = (validate_github if ci == "github" else validate_gitlab)(caller)
    assert validated.returncode == 0, validated.stdout + validated.stderr

    # The adapter comes from the manifest; the version is this package's.
    text = caller.read_text()
    assert "dbt-duckdb" in text and __version__ in text

    # The pre-commit snippet is printed, not written.
    assert not (project / ".pre-commit-config.yaml").exists()
    snippet = out[out.index("repos:") :]
    hooks = yaml.safe_load(snippet)["repos"][0]
    assert hooks["rev"] == f"v{__version__}"
    (hook,) = hooks["hooks"]
    assert hook["id"] == "dbt-agents-schema-check"
    # `check --parse` runs dbt inside pre-commit's isolated environment.
    assert hook["additional_dependencies"] == ["dbt-core>=1.8,<2", "dbt-duckdb"]


def test_init_ci_none_writes_no_caller(project: Path) -> None:
    assert run_init(project, "--ci", "none") == 0
    assert (project / "agents_schema.yml").is_file()
    for path in CALLER_PATHS.values():
        assert not (project / path).exists()


def test_init_default_ci_is_github(project: Path) -> None:
    assert run_init(project) == 0
    assert (project / CALLER_PATHS["github"]).is_file()


def test_init_refuses_to_overwrite_without_force(
    project: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (project / "agents_schema.yml").write_text("version: 1\nowner: keep-me\n")
    assert run_init(project, "--ci", "gitlab") == 2
    err = capsys.readouterr().err
    assert "agents_schema.yml" in err and "--force" in err
    # Nothing at all was written: not the config, not the skill, not the caller.
    assert (project / "agents_schema.yml").read_text() == "version: 1\nowner: keep-me\n"
    assert not (project / "agents").exists()
    assert not (project / CALLER_PATHS["gitlab"]).exists()


def test_init_refuses_when_only_the_caller_exists(project: Path) -> None:
    caller = project / CALLER_PATHS["github"]
    caller.parent.mkdir(parents=True)
    caller.write_text("name: mine\n")
    assert run_init(project, "--ci", "github") == 2
    assert caller.read_text() == "name: mine\n"
    assert not (project / "agents_schema.yml").exists()


def test_init_force_overwrites(project: Path) -> None:
    (project / "agents_schema.yml").write_text("version: 1\nowner: old\n")
    assert run_init(project, "--ci", "github", "--force") == 0
    assert load_config(project).owner != "old"


def test_init_without_manifest_uses_dbt_project_name(tmp_path: Path) -> None:
    project = tmp_path / "p"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: My-Proj\nprofile: x\n")
    assert run_init(project, "--ci", "github") == 0
    assert load_config(project).project == "my_proj"
    assert "dbt-snowflake" in (project / CALLER_PATHS["github"]).read_text()


def test_init_invalid_dbt_name_leaves_project_unset(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    project = tmp_path / "p"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: " + "x" * 70 + "\nprofile: x\n")
    assert run_init(project, "--ci", "none") == 0
    text = (project / "agents_schema.yml").read_text()
    assert "\nproject:" not in text
    assert "project" in capsys.readouterr().err


def test_examples_match_init_templates() -> None:
    for ci, example, version in (
        ("github", ROOT / "examples/github/agents-schema.yml", "0.2.0"),
        ("gitlab", ROOT / "examples/gitlab/.gitlab-ci.yml", "0.1.0"),
    ):
        assert render_caller(ci, version=version, adapter="dbt-snowflake") == example.read_text()


def test_pre_commit_hook_definition() -> None:
    hooks = yaml.safe_load((ROOT / ".pre-commit-hooks.yaml").read_text())
    (hook,) = hooks
    assert hook["id"] == "dbt-agents-schema-check"
    assert hook["language"] == "python"
    assert hook["entry"] == "dbt-agents-schema check --parse"
    assert hook["pass_filenames"] is False
    assert hook["files"] == r"^(models/|agents/|agents_schema\.yml|dbt_project\.yml)"
    assert hook["name"]


def test_package_ships_init_templates() -> None:
    from importlib import resources

    base = resources.files("dbt_agents_schema").joinpath("init_templates")
    names = {p.name for p in base.iterdir()}
    assert {"agents_schema.yml", "example_skill.md", "github.yml", "gitlab.yml"} <= names
