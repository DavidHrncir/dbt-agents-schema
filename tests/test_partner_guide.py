"""T27: the partner guide covers every CLI flag and config key, and its config reference
is generated from the JSON Schema without drift."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from dbt_agents_schema.cli import build_parser

ROOT = Path(__file__).resolve().parents[1]
GUIDE = ROOT / "docs" / "partner-guide.md"
README = ROOT / "README.md"
SCHEMA = ROOT / "src" / "dbt_agents_schema" / "schemas" / "agents_schema.schema.json"
GENERATOR = ROOT / "scripts" / "gen_config_reference.py"

FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")


def _guide() -> str:
    return GUIDE.read_text(encoding="utf-8")


def _subparsers() -> dict[str, argparse.ArgumentParser]:
    parser = build_parser()
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return dict(action.choices)
    raise AssertionError("no subcommands")


def _load_generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location("gen_config_reference", GENERATOR)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["gen_config_reference"] = module
    spec.loader.exec_module(module)
    return module


def _schema_key_paths() -> list[str]:
    schema: dict[str, Any] = json.loads(SCHEMA.read_text(encoding="utf-8"))
    defs = schema["$defs"]
    paths: list[str] = []

    def walk(node: dict[str, Any], prefix: str) -> None:
        if "$ref" in node:
            node = defs[node["$ref"].rsplit("/", 1)[1]]
        for key, child in node.get("properties", {}).items():
            path = f"{prefix}{key}"
            paths.append(path)
            walk(child, f"{path}.")
        extra = node.get("additionalProperties")
        if isinstance(extra, dict):
            walk(extra, f"{prefix}<name>.")

    walk(schema, "")
    return paths


def test_guide_exists_and_readme_points_to_it() -> None:
    assert GUIDE.is_file()
    assert "docs/partner-guide.md" in README.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "section",
    [
        "What it does",
        "Install",
        "Configuration reference",
        "GitHub setup",
        "GitLab setup",
        "Developer workflow",
        "dbt platform projects",
        "Migrating from the dbt Labs tool",
        "Warehouse permissions",
        "Troubleshooting",
    ],
)
def test_guide_has_section(section: str) -> None:
    headings = [line.lstrip("#").strip() for line in _guide().splitlines() if line.startswith("#")]
    assert any(h.startswith(section) for h in headings), f"missing section {section!r}"


def test_every_command_appears() -> None:
    guide = _guide()
    for name in _subparsers():
        assert f"dbt-agents-schema {name}" in guide, f"command {name} not in guide"
    assert "--version" in guide


def test_every_cli_flag_from_help_appears() -> None:
    guide = _guide()
    missing: list[str] = []
    for name, sub in _subparsers().items():
        flags = set(FLAG.findall(sub.format_help())) - {"--help"}
        assert flags, f"no flags parsed from `{name} --help`"
        missing += [f"{name} {flag}" for flag in sorted(flags) if f"`{flag}" not in guide]
    assert not missing, f"flags missing from the guide: {missing}"


def test_every_choice_value_appears() -> None:
    guide = _guide().replace("\\|", "|")  # markdown tables escape `|`
    for name, sub in _subparsers().items():
        for action in sub._actions:
            for choice in action.choices or ():
                assert f"`{choice}`" in guide or f"|{choice}" in guide or f"{choice}|" in guide, (
                    f"{name} {action.option_strings} choice {choice!r} not in guide"
                )


def test_every_config_key_appears() -> None:
    guide = _guide()
    paths = _schema_key_paths()
    assert "check.rules.skill_unresolved_uses" in paths
    assert "environments.<name>.credentials" in paths
    missing = [p for p in paths if f"`{p}`" not in guide]
    assert not missing, f"config keys missing from the guide: {missing}"


def test_every_exit_code_is_troubleshot() -> None:
    guide = _guide()
    trouble = guide.split("## Troubleshooting", 1)[1]
    for code in range(5):
        assert re.search(rf"^### Exit {code}\b", trouble, re.M), f"exit {code} not covered"


def test_config_reference_has_no_drift() -> None:
    gen = _load_generator()
    assert gen.splice(_guide(), gen.render(json.loads(SCHEMA.read_text()))) == _guide()


def test_generator_detects_drift(tmp_path: Path) -> None:
    gen = _load_generator()
    stale = tmp_path / "guide.md"
    stale.write_text(_guide().replace("`skills_dir`", "`skill_dir`", 1), encoding="utf-8")
    assert gen.main(["--check", "--guide", str(stale)]) == 1
    assert gen.main(["--guide", str(stale)]) == 0
    assert gen.main(["--check", "--guide", str(stale)]) == 0
    assert stale.read_text(encoding="utf-8") == _guide()


def test_generator_requires_markers(tmp_path: Path) -> None:
    gen = _load_generator()
    bad = tmp_path / "guide.md"
    bad.write_text("# no markers\n", encoding="utf-8")
    assert gen.main(["--check", "--guide", str(bad)]) == 2
