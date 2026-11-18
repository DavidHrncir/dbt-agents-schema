"""Command-line entry point (SPEC section 7)."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from dbt_agents_schema import __version__
from dbt_agents_schema.check import (
    check_project,
    format_github,
    format_json,
    format_text,
    gitlab_report,
)

EXIT_OK = 0
EXIT_CHECK_ERRORS = 1
EXIT_INPUT_ERROR = 2  # config, manifest or credential error

DEFAULT_GITLAB_REPORT = "gl-code-quality-report.json"


def _common_options() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--project-dir", default=".", help="dbt project directory (default: current directory)"
    )
    common.add_argument(
        "--config", default=None, help="config file (default: <project-dir>/agents_schema.yml)"
    )
    common.add_argument(
        "--manifest",
        default=None,
        help="dbt manifest (default: <project-dir>/target/manifest.json)",
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dbt-agents-schema",
        description="Publish dbt project metadata into a shared warehouse agents schema.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    common = _common_options()

    check = commands.add_parser(
        "check",
        parents=[common],
        help="validate config, manifest and skills (no warehouse access)",
        description="Validate config, manifest and skills. Exit 1 on any error finding.",
    )
    check.add_argument(
        "--parse", action="store_true", help="run `dbt parse` in the project directory first"
    )
    check.add_argument(
        "--format",
        choices=("text", "json", "github", "gitlab"),
        default="text",
        help="output format (default: text)",
    )
    check.add_argument(
        "--output",
        default=None,
        help=(
            "write the report to this file; with --format gitlab the Code Quality report "
            f"(default {DEFAULT_GITLAB_REPORT}) while text still goes to stdout"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return _cmd_check(args)
    parser.print_help()
    return EXIT_OK


def main_entry() -> None:
    sys.exit(main())


# --- commands ----------------------------------------------------------------------


def _cmd_check(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir)
    if args.parse:
        code = _run_dbt_parse(project_dir)
        if code != EXIT_OK:
            return code

    result = check_project(
        project_dir,
        config_path=Path(args.config) if args.config else None,
        manifest_path=Path(args.manifest) if args.manifest else None,
    )

    if args.format == "gitlab":
        report_path = Path(args.output or DEFAULT_GITLAB_REPORT)
        _write(report_path, json.dumps(gitlab_report(result), indent=2) + "\n")
        sys.stdout.write(format_text(result))
    else:
        rendered = {"text": format_text, "json": format_json, "github": format_github}[args.format](
            result
        )
        if args.output:
            _write(Path(args.output), rendered)
        else:
            sys.stdout.write(rendered)

    if result.manifest_missing:
        missing = next(f for f in result.findings if f.rule == "manifest_missing")
        print(f"error: {missing.message}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    return EXIT_CHECK_ERRORS if result.has_errors else EXIT_OK


def _run_dbt_parse(project_dir: Path) -> int:
    try:
        completed = subprocess.run(["dbt", "parse"], cwd=project_dir)
    except FileNotFoundError:
        print(
            "error: --parse needs the `dbt` executable on PATH; install dbt with your "
            "adapter (for example `pip install dbt-snowflake`) or run `dbt parse` yourself",
            file=sys.stderr,
        )
        return EXIT_INPUT_ERROR
    if completed.returncode != 0:
        print(
            f"error: `dbt parse` failed in {project_dir} (exit {completed.returncode}); fix "
            "the dbt errors above and retry",
            file=sys.stderr,
        )
        return EXIT_INPUT_ERROR
    return EXIT_OK


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
