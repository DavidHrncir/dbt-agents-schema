"""Manifest target rules (SPEC 7, 8; T32): `manifest_target_mismatch`, `manifest_empty`,
`column_meta_missing`, `check --env`, and `publish --env` applying them before connecting."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from fakes import MANIFESTS, PROJECTS

from dbt_agents_schema import cli
from dbt_agents_schema.check import Finding, check_project
from dbt_agents_schema.cli import main

FINANCE_MODELS = 8  # published models in tests/fixtures/manifests/finance.json


def project(
    tmp_path: Path, parse_target: str | None = None, rules: str = "", database: str = ""
) -> Path:
    """A copy of the finance fixture project with `parse_target` on `prod`."""
    root = tmp_path / "finance"
    shutil.copytree(PROJECTS / "finance", root)
    pt = f"    parse_target:\n{parse_target}" if parse_target is not None else ""
    (root / "agents_schema.yml").write_text(
        "version: 1\nproject: finance\nenvironments:\n  prod:\n    credentials: env\n"
        f"    schema: AGENTS\n{pt}  dev:\n    credentials: dbt_profile\n"
        '    schema: "{{ target_schema }}_AGENTS"\n'
        + (f"check:\n  rules:\n{rules}" if rules else "")
    )
    return root


def target(type_: str, schema: str, database: str | None = None) -> str:
    db = f"      database: {database}\n" if database is not None else ""
    return f"      type: {type_}\n      schema: {schema}\n{db}"


def run(root: Path, env: str | None = "prod", manifest: Path | None = None) -> list[Finding]:
    result = check_project(root, manifest_path=manifest or MANIFESTS / "finance.json", env=env)
    return list(result.findings)


def rule(findings: list[Finding], name: str) -> list[Finding]:
    return [f for f in findings if f.rule == name]


# --- manifest_target_mismatch ------------------------------------------------------


def test_matching_target_no_finding(tmp_path: Path) -> None:
    root = project(tmp_path, target("snowflake", "finance", "memory"))
    assert rule(run(root), "manifest_target_mismatch") == []


def test_case_differences_no_finding(tmp_path: Path) -> None:
    root = project(tmp_path, target("snowflake", "FINANCE", "MEMORY"))
    assert rule(run(root), "manifest_target_mismatch") == []


def test_wrong_database_one_finding_per_model(tmp_path: Path) -> None:
    root = project(tmp_path, target("snowflake", "finance", "ANALYTICS"))
    found = rule(run(root), "manifest_target_mismatch")
    assert len(found) == FINANCE_MODELS
    assert all(f.severity == "error" for f in found)
    by_node = {f.node: f for f in found}
    msg = by_node["model.finance.fct_revenue"].message
    assert "fct_revenue" in msg
    assert "memory.finance_marts" in msg
    assert "ANALYTICS" in msg and "finance" in msg


def test_wrong_schema_prefix(tmp_path: Path) -> None:
    root = project(tmp_path, target("snowflake", "finance_marts", "memory"))
    found = rule(run(root), "manifest_target_mismatch")
    # finance_staging and the package models in `finance` do not start with finance_marts
    assert {f.node for f in found} == {
        "model.finance.int_invoice_lines",
        "model.finance.stg_invoices",
        "model.finance.stg_payments",
        "model.shared_pkg.dim_calendar",
        "model.shared_pkg.dim_currency",
    }
    assert all("finance_marts" in f.message for f in found)


def test_unrelated_schema_flags_every_model(tmp_path: Path) -> None:
    root = project(tmp_path, target("bigquery", "marketing", "memory"))
    assert len(rule(run(root), "manifest_target_mismatch")) == FINANCE_MODELS


def test_duckdb_unset_database_skips_database_comparison(tmp_path: Path) -> None:
    root = project(tmp_path, target("duckdb", "finance"))
    assert rule(run(root), "manifest_target_mismatch") == []


def test_duckdb_unset_database_still_checks_schema(tmp_path: Path) -> None:
    root = project(tmp_path, target("duckdb", "marketing"))
    assert len(rule(run(root), "manifest_target_mismatch")) == FINANCE_MODELS


def test_without_env_never_reports_mismatch(tmp_path: Path) -> None:
    root = project(tmp_path, target("snowflake", "marketing", "ANALYTICS"))
    assert rule(run(root, env=None), "manifest_target_mismatch") == []


def test_env_without_parse_target_no_finding(tmp_path: Path) -> None:
    root = project(tmp_path, target("snowflake", "marketing", "ANALYTICS"))
    assert rule(run(root, env="dev"), "manifest_target_mismatch") == []


def test_mismatch_can_be_downgraded(tmp_path: Path) -> None:
    root = project(
        tmp_path,
        target("snowflake", "marketing", "memory"),
        rules="    manifest_target_mismatch: warning\n",
    )
    found = rule(run(root), "manifest_target_mismatch")
    assert found and all(f.severity == "warning" for f in found)


# --- manifest_empty ----------------------------------------------------------------


def manifest_without_models(tmp_path: Path) -> Path:
    data: dict[str, Any] = json.loads((MANIFESTS / "finance.json").read_text())
    data["nodes"] = {k: v for k, v in data["nodes"].items() if v["resource_type"] != "model"}
    data["parent_map"] = {k: v for k, v in data.get("parent_map", {}).items() if k in data["nodes"]}
    data["child_map"] = {k: v for k, v in data.get("child_map", {}).items() if k in data["nodes"]}
    path = tmp_path / "empty.json"
    path.write_text(json.dumps(data))
    return path


def test_manifest_with_no_models_is_error(tmp_path: Path) -> None:
    root = project(tmp_path)
    found = rule(run(root, env=None, manifest=manifest_without_models(tmp_path)), "manifest_empty")
    assert len(found) == 1
    assert found[0].severity == "error"
    assert "no published models" in found[0].message


def test_manifest_with_models_not_empty(tmp_path: Path) -> None:
    assert rule(run(project(tmp_path), env=None), "manifest_empty") == []


# --- column_meta_missing -----------------------------------------------------------


def manifest_without_column_meta(tmp_path: Path) -> Path:
    data: dict[str, Any] = json.loads((MANIFESTS / "finance.json").read_text())
    for node in data["nodes"].values():
        for column in node.get("columns", {}).values():
            column["meta"] = {}
            if isinstance(column.get("config"), dict):
                column["config"]["meta"] = {}
    path = tmp_path / "nometa.json"
    path.write_text(json.dumps(data))
    return path


def test_column_meta_missing_off_by_default(tmp_path: Path) -> None:
    root = project(tmp_path)
    found = run(root, env=None, manifest=manifest_without_column_meta(tmp_path))
    assert rule(found, "column_meta_missing") == []


def test_column_meta_missing_at_error(tmp_path: Path) -> None:
    root = project(tmp_path, rules="    column_meta_missing: error\n")
    found = rule(
        run(root, env=None, manifest=manifest_without_column_meta(tmp_path)), "column_meta_missing"
    )
    assert len(found) == 1
    assert found[0].severity == "error"
    assert "meta" in found[0].message and "Fusion" in found[0].message


def test_column_meta_present_no_finding_at_error(tmp_path: Path) -> None:
    # fct_revenue has a column with meta in the fixture manifest
    root = project(tmp_path, rules="    column_meta_missing: error\n")
    assert rule(run(root, env=None), "column_meta_missing") == []


# --- CLI ---------------------------------------------------------------------------


def test_check_cli_env_reports_mismatch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path, target("snowflake", "marketing", "memory"))
    args = ["check", "--project-dir", str(root), "--manifest", str(MANIFESTS / "finance.json")]
    assert main([*args, "--env", "prod"]) == 1
    assert "manifest_target_mismatch" in capsys.readouterr().out
    assert main(args) == 0
    assert "manifest_target_mismatch" not in capsys.readouterr().out


def test_check_cli_unknown_env_exit_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = project(tmp_path)
    code = main(
        [
            "check",
            "--project-dir",
            str(root),
            "--manifest",
            str(MANIFESTS / "finance.json"),
            "--env",
            "staging",
        ]
    )
    assert code == 2
    assert "staging" in capsys.readouterr().err


def test_publish_mismatch_exit_1_without_connecting(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path, target("snowflake", "finance", "ANALYTICS"))
    db = tmp_path / "w.duckdb"
    monkeypatch.setenv("WAREHOUSE_CREDENTIALS", json.dumps({"type": "duckdb", "path": str(db)}))
    monkeypatch.setattr(cli, "open_writer", lambda *a, **k: pytest.fail("must not connect"))
    code = main(
        [
            "publish",
            "--env",
            "prod",
            "--project-dir",
            str(root),
            "--manifest",
            str(MANIFESTS / "finance.json"),
        ]
    )
    assert code == 1
    assert "manifest_target_mismatch" in capsys.readouterr().out
    assert not db.exists()


def test_publish_mismatch_exit_1_even_without_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("WAREHOUSE_CREDENTIALS", raising=False)
    root = project(tmp_path, target("snowflake", "finance", "ANALYTICS"))
    code = main(
        [
            "publish",
            "--env",
            "prod",
            "--project-dir",
            str(root),
            "--manifest",
            str(MANIFESTS / "finance.json"),
        ]
    )
    assert code == 1


def test_publish_matching_target_succeeds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = project(tmp_path, target("duckdb", "finance"))
    db = tmp_path / "w.duckdb"
    monkeypatch.setenv("WAREHOUSE_CREDENTIALS", json.dumps({"type": "duckdb", "path": str(db)}))
    code = main(
        [
            "publish",
            "--env",
            "prod",
            "--project-dir",
            str(root),
            "--manifest",
            str(MANIFESTS / "finance.json"),
        ]
    )
    assert code == 0


def test_check_cli_invalid_config_reports_findings_not_unknown_env(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = project(tmp_path)
    (root / "agents_schema.yml").write_text(
        "version: 1\nenvironments:\n  staging:\n    credentials: env\n    schema: A\n    bogus: 1\n"
    )
    code = main(
        [
            "check",
            "--project-dir",
            str(root),
            "--manifest",
            str(MANIFESTS / "finance.json"),
            "--env",
            "staging",
        ]
    )
    captured = capsys.readouterr()
    assert code == 1
    assert "config_invalid" in captured.out
    assert "is not defined" not in captured.err
