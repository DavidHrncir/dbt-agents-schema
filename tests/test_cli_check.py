"""`check` command tests, in-process through `main(argv)` (SPEC 7, T09)."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from dbt_agents_schema import cli
from dbt_agents_schema.cli import main

HERE = Path(__file__).parent
PROJECTS = HERE / "fixtures" / "projects"
MANIFESTS = HERE / "fixtures" / "manifests"


def project(tmp_path: Path, name: str, manifest: str | None = None) -> Path:
    root = tmp_path / name
    shutil.copytree(PROJECTS / name, root)
    if manifest is not None:
        (root / "target").mkdir()
        shutil.copy(MANIFESTS / f"{manifest}.json", root / "target" / "manifest.json")
    return root


def test_clean_finance_exits_0_with_warnings(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, "finance", "finance")
    assert main(["check", "--project-dir", str(root)]) == 0
    out = capsys.readouterr().out
    assert "warning[skill_without_uses]" in out
    assert "0 errors, 1 warnings" in out


def test_bad_exits_1(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, "bad")
    code = main(["check", "--project-dir", str(root), "--manifest", str(MANIFESTS / "bad.json")])
    assert code == 1
    assert "error[skill_unresolved_uses]" in capsys.readouterr().out


def test_missing_manifest_exits_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, "finance")
    assert main(["check", "--project-dir", str(root)]) == 2
    captured = capsys.readouterr()
    assert "run `dbt parse` first" in captured.err
    assert str(root / "target" / "manifest.json") in captured.err


def test_config_flag(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, "bad")
    code = main(
        [
            "check",
            "--project-dir",
            str(root),
            "--manifest",
            str(MANIFESTS / "bad.json"),
            "--config",
            str(root / "agents_schema.invalid.yml"),
        ]
    )
    assert code == 1
    assert "config_invalid" in capsys.readouterr().out


def test_default_project_dir_is_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, "finance", "finance")
    monkeypatch.chdir(root)
    assert main(["check"]) == 0
    assert "agents/skills/revenue_overview.md" in capsys.readouterr().out


# --- --parse -----------------------------------------------------------------------


def test_parse_runs_dbt_parse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, "finance")
    calls: list[tuple[list[str], dict[str, Any]]] = []

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        (root / "target").mkdir(exist_ok=True)
        shutil.copy(MANIFESTS / "finance.json", root / "target" / "manifest.json")
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert main(["check", "--parse", "--project-dir", str(root)]) == 0
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[:2] == ["dbt", "parse"]
    assert kwargs["cwd"] == root
    assert kwargs.get("check") is not True


def test_parse_failure_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, "finance")

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(args, 1)

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert main(["check", "--parse", "--project-dir", str(root)]) == 2
    assert "dbt parse" in capsys.readouterr().err


def test_parse_without_dbt_installed_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, "finance")

    def fake_run(args: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        raise FileNotFoundError("dbt")

    monkeypatch.setattr(cli.subprocess, "run", fake_run)
    assert main(["check", "--parse", "--project-dir", str(root)]) == 2
    assert "dbt" in capsys.readouterr().err


def test_no_parse_never_calls_subprocess(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path, "finance", "finance")

    def boom(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("subprocess must not run without --parse")

    monkeypatch.setattr(cli.subprocess, "run", boom)
    assert main(["check", "--project-dir", str(root)]) == 0


# --- formats -----------------------------------------------------------------------


def test_format_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, "finance", "finance")
    assert main(["check", "--project-dir", str(root), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["summary"] == {"error": 0, "warning": 1, "info": 0}


def test_format_github(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, "bad")
    args = ["check", "--project-dir", str(root), "--manifest", str(MANIFESTS / "bad.json")]
    assert main([*args, "--format", "github"]) == 1
    out = capsys.readouterr().out
    assert "::error file=" in out
    assert "::warning file=" in out


def test_format_gitlab_writes_default_report_and_prints_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, "bad")
    monkeypatch.chdir(tmp_path)
    args = ["check", "--project-dir", str(root), "--manifest", str(MANIFESTS / "bad.json")]
    assert main([*args, "--format", "gitlab"]) == 1
    report = json.loads((tmp_path / "gl-code-quality-report.json").read_text())
    assert isinstance(report, list) and report
    assert {"description", "check_name", "fingerprint", "severity", "location"} <= set(report[0])
    assert "error[" in capsys.readouterr().out


def test_format_gitlab_output_path(tmp_path: Path) -> None:
    root = project(tmp_path, "finance", "finance")
    out = tmp_path / "reports" / "cq.json"
    assert (
        main(["check", "--project-dir", str(root), "--format", "gitlab", "--output", str(out)]) == 0
    )
    report = json.loads(out.read_text())
    assert [item["check_name"] for item in report] == ["skill_without_uses"]


def test_output_for_json(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, "finance", "finance")
    out = tmp_path / "check.json"
    assert (
        main(["check", "--project-dir", str(root), "--format", "json", "--output", str(out)]) == 0
    )
    assert json.loads(out.read_text())["summary"]["warning"] == 1


def test_gitlab_report_written_even_when_manifest_missing(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, "finance")
    out = tmp_path / "cq.json"
    assert (
        main(["check", "--project-dir", str(root), "--format", "gitlab", "--output", str(out)]) == 2
    )
    report = json.loads(out.read_text())
    assert "manifest_missing" in {item["check_name"] for item in report}


def test_invalid_format_is_usage_error(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["check", "--format", "xml"])
    assert exc.value.code == 2


def test_no_command_prints_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert main([]) == 0
    assert "check" in capsys.readouterr().out
