"""Command-line entry point (SPEC section 7)."""

from __future__ import annotations

import argparse
import json
import os
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
from dbt_agents_schema.config import Config, ConfigError, load_config
from dbt_agents_schema.credentials import (
    Connection,
    CredentialError,
    from_dbt_profile,
    from_env,
)
from dbt_agents_schema.gitinfo import detect_git
from dbt_agents_schema.publish import publish, unpublish
from dbt_agents_schema.writers.factory import ensure_writer_available, open_writer

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

    profile_options = argparse.ArgumentParser(add_help=False)
    profile_options.add_argument(
        "--env", required=True, help="environment name from agents_schema.yml"
    )
    profile_options.add_argument(
        "--target", default=None, help="dbt target (credentials: dbt_profile only)"
    )
    profile_options.add_argument(
        "--profiles-dir",
        default=None,
        help="directory holding profiles.yml (credentials: dbt_profile only)",
    )

    pub = commands.add_parser(
        "publish",
        parents=[common, profile_options],
        help="publish this project's metadata to the environment's agents schema",
        description=(
            "Check, plan and replace this project's rows in the agents schema. Exit codes: "
            "0 success, 1 check errors, 2 config/manifest/credential error, 3 warehouse "
            "error, 4 ownership conflict."
        ),
    )
    pub.add_argument(
        "--dry-run", action="store_true", help="print the plan summary without connecting"
    )
    pub.add_argument(
        "--force-takeover",
        action="store_true",
        help="publish even if the project was last published from another repository",
    )

    unpub = commands.add_parser(
        "unpublish",
        parents=[common, profile_options],
        help="delete one project's rows from the environment's agents schema",
        description="Delete one project's rows, including its ROOT rows.",
    )
    unpub.add_argument(
        "--project", default=None, help="project key to remove (default: this project's key)"
    )
    unpub.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "check":
        return _cmd_check(args)
    if args.command == "publish":
        return _cmd_publish(args)
    if args.command == "unpublish":
        return _cmd_unpublish(args)
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


def _cmd_publish(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir)
    try:
        _, connection = _connection(args)
        if not args.dry_run:
            ensure_writer_available(connection.warehouse)
    except (ConfigError, CredentialError) as exc:
        # SPEC 6 runs the check (exit 1) before connecting (exit 2): report check
        # errors first so they are not hidden behind a credential problem.
        checked = check_project(
            project_dir,
            config_path=Path(args.config) if args.config else None,
            manifest_path=Path(args.manifest) if args.manifest else None,
        )
        if (
            checked.has_errors
            and not checked.manifest_missing
            and not any(f.rule == "config_invalid" for f in checked.findings)
        ):
            sys.stdout.write(format_text(checked))
            print("error: check found errors; nothing was published", file=sys.stderr)
            return EXIT_CHECK_ERRORS
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    outcome = publish(
        project_dir,
        env=args.env,
        warehouse=connection.warehouse,
        writer_factory=lambda target: open_writer(connection, target),
        git=detect_git(project_dir, os.environ),
        out=sys.stdout,
        err=sys.stderr,
        config_path=Path(args.config) if args.config else None,
        manifest_path=Path(args.manifest) if args.manifest else None,
        target_schema=connection.target_schema,
        target_database=connection.target_database,
        force_takeover=args.force_takeover,
        dry_run=args.dry_run,
    )
    return outcome.exit_code


def _cmd_unpublish(args: argparse.Namespace) -> int:
    project_dir = Path(args.project_dir)
    try:
        config, connection = _connection(args)
        ensure_writer_available(connection.warehouse)
    except (ConfigError, CredentialError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT_ERROR
    project = args.project or config.project
    if not args.yes:
        if not sys.stdin.isatty():
            print(
                "error: unpublish deletes rows and stdin is not a terminal; pass --yes to confirm",
                file=sys.stderr,
            )
            return EXIT_INPUT_ERROR
        print(
            f"Delete every row of project {project!r} from environment {args.env!r}? "
            "Type the project key to confirm: ",
            end="",
            flush=True,
        )
        if sys.stdin.readline().strip() != project:
            print("error: not confirmed; nothing was deleted", file=sys.stderr)
            return EXIT_INPUT_ERROR
    return unpublish(
        project_dir,
        env=args.env,
        warehouse=connection.warehouse,
        writer_factory=lambda target: open_writer(connection, target),
        out=sys.stdout,
        err=sys.stderr,
        project=project,
        config_path=Path(args.config) if args.config else None,
        target_schema=connection.target_schema,
        target_database=connection.target_database,
    )


def _connection(args: argparse.Namespace) -> tuple[Config, Connection]:
    """Load the config and resolve the credentials of `--env` (SPEC 4)."""
    project_dir = Path(args.project_dir)
    config = load_config(project_dir, Path(args.config) if args.config else None)
    env = config.environments.get(args.env)
    if env is None:
        available = ", ".join(sorted(config.environments)) or "none"
        raise CredentialError(
            f"environment {args.env!r} is not defined in {config.path}; available: "
            f"{available}. Pass one of them to --env or add it to the config"
        )
    if env.credentials == "env":
        return config, from_env(os.environ)
    return config, from_dbt_profile(
        project_dir,
        profiles_dir=Path(args.profiles_dir) if args.profiles_dir else None,
        target=args.target,
        environ=os.environ,
    )


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
