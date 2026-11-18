"""Publish and unpublish pipelines (SPEC 6), independent of how credentials are found.

The CLI resolves credentials and git metadata, then calls `publish` / `unpublish` with a
`writer_factory` that opens a `Writer` for the resolved target. Everything here returns
an exit code (SPEC 6): 0 success · 1 check errors · 2 config, manifest or credential
error · 3 warehouse error · 4 ownership conflict.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TextIO

from dbt_agents_schema.check import check_project, format_text
from dbt_agents_schema.config import ConfigError, ResolvedTarget, load_config, resolve_target
from dbt_agents_schema.plan import SPEC_VERSION, TABLES, Plan, RunMetadata, build_plan
from dbt_agents_schema.writers import (
    EnsureResult,
    MissingExtraError,
    SwapResult,
    WarehouseError,
    Writer,
)

EXIT_OK = 0
EXIT_CHECK = 1
EXIT_CONFIG = 2
EXIT_WAREHOUSE = 3
EXIT_OWNERSHIP = 4

WriterFactory = Callable[[ResolvedTarget], Writer]


@dataclass(frozen=True)
class GitInfo:
    """Repository facts for `DBT_PROJECT` (SPEC 7, "Git metadata"); None when unknown."""

    repo_url: str | None = None
    sha: str | None = None
    branch: str | None = None


@dataclass(frozen=True)
class PublishOutcome:
    exit_code: int
    plan: Plan | None = None
    target: ResolvedTarget | None = None
    ensure: EnsureResult | None = None
    swap: SwapResult | None = None


class _Abort(Exception):
    def __init__(self, exit_code: int, message: str) -> None:
        super().__init__(message)
        self.exit_code = exit_code


def publish(
    project_dir: Path,
    *,
    env: str,
    warehouse: str,
    writer_factory: WriterFactory,
    git: GitInfo,
    out: TextIO,
    err: TextIO,
    config_path: Path | None = None,
    manifest_path: Path | None = None,
    target_schema: str | None = None,
    target_database: str | None = None,
    force_takeover: bool = False,
    dry_run: bool = False,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> PublishOutcome:
    """Run SPEC 6 steps 1-8 and return the outcome with its exit code."""
    started = time.monotonic()

    # 1-2. Load and check.
    result = check_project(project_dir, config_path=config_path, manifest_path=manifest_path)
    if result.findings:
        out.write(format_text(result))
    if result.manifest_missing:
        err.write("error: no readable dbt manifest; run `dbt parse` first\n")
        return PublishOutcome(EXIT_CONFIG)
    if any(f.rule == "config_invalid" and f.severity == "error" for f in result.findings):
        err.write("error: the agents_schema.yml config is invalid; fix the errors above\n")
        return PublishOutcome(EXIT_CONFIG)
    if result.has_errors:
        err.write("error: check found errors; nothing was published\n")
        return PublishOutcome(EXIT_CHECK)
    config, manifest, skills = result.config, result.manifest, result.skills
    assert config is not None and manifest is not None and skills is not None

    try:
        target = resolve_target(
            config,
            env,
            warehouse=warehouse,
            target_schema=target_schema,
            target_database=target_database,
        )
    except ConfigError as exc:
        err.write(f"error: {exc}\n")
        return PublishOutcome(EXIT_CONFIG)

    # 3. Plan (pure).
    run = RunMetadata(
        environment=env,
        warehouse=warehouse,
        published_at=now(),
        repo_url=git.repo_url,
        git_sha=git.sha,
        git_branch=git.branch,
    )
    plan = build_plan(config, manifest, skills, run, root_package=result.root_package)
    where = _describe(target)
    if dry_run:
        out.write(f"dry run: would publish project {plan.project!r} to {where}\n")
        _write_counts(out, "rows", plan.counts())
        return PublishOutcome(EXIT_OK, plan=plan, target=target)

    # 4-7. Connect, guard, ensure structure, swap.
    ensure: EnsureResult | None = None
    swap: SwapResult | None = None
    try:
        writer = writer_factory(target)
        try:
            newest = writer.max_spec_version()
            if newest is not None and newest > SPEC_VERSION:
                raise _Abort(
                    EXIT_CONFIG,
                    f"{where} holds DBT_PROJECT rows with spec_version {newest}, newer than "
                    f"this tool's spec_version {SPEC_VERSION}; upgrade dbt-agents-schema "
                    "before publishing here",
                )
            ensure = writer.ensure_structure()
            for table, columns in ensure.columns_added.items():
                out.write(f"migrated {table}: added columns {', '.join(columns)}\n")
            existing = writer.read_project_row(plan.project)
            previous_repo = existing.get("repo_url") if existing is not None else None
            if (
                previous_repo is not None
                and git.repo_url is not None
                and previous_repo != git.repo_url
            ):
                if not force_takeover:
                    raise _Abort(
                        EXIT_OWNERSHIP,
                        f"project {plan.project!r} in {where} was published from "
                        f"{previous_repo}, not {git.repo_url}. Two repositories must not "
                        "share a project key: change `project` in agents_schema.yml, or "
                        "pass --force-takeover if this repository now owns it",
                    )
                out.write(
                    f"taking over project {plan.project!r} from {previous_repo} "
                    "(--force-takeover)\n"
                )
            swap = writer.swap(plan)
        finally:
            writer.close()
    except _Abort as exc:
        err.write(f"error: {exc}\n")
        return PublishOutcome(exc.exit_code, plan=plan, target=target, ensure=ensure)
    except MissingExtraError as exc:
        err.write(f"error: {exc}\n")
        return PublishOutcome(EXIT_CONFIG, plan=plan, target=target)
    except WarehouseError as exc:
        err.write(f"error: {exc}\n")
        return PublishOutcome(EXIT_WAREHOUSE, plan=plan, target=target, ensure=ensure)

    # 8. Report.
    out.write(f"published project {plan.project!r} to {where}\n")
    _write_counts(out, "rows written", swap.rows_written)
    out.write(f"legacy rows removed: {swap.total_legacy_removed}\n")
    out.write(f"elapsed: {time.monotonic() - started:.2f}s\n")
    return PublishOutcome(EXIT_OK, plan=plan, target=target, ensure=ensure, swap=swap)


def unpublish(
    project_dir: Path,
    *,
    env: str,
    warehouse: str,
    writer_factory: WriterFactory,
    out: TextIO,
    err: TextIO,
    project: str | None = None,
    config_path: Path | None = None,
    target_schema: str | None = None,
    target_database: str | None = None,
) -> int:
    """Delete one project's rows (default: this config's project) from the env's schema."""
    try:
        config = load_config(project_dir, config_path)
        target = resolve_target(
            config,
            env,
            warehouse=warehouse,
            target_schema=target_schema,
            target_database=target_database,
        )
    except ConfigError as exc:
        err.write(f"error: {exc}\n")
        return EXIT_CONFIG
    key = project or config.project
    where = _describe(target)
    try:
        writer = writer_factory(target)
        try:
            newest = writer.max_spec_version()
            if newest is not None and newest > SPEC_VERSION:
                err.write(
                    f"error: {where} holds DBT_PROJECT rows with spec_version {newest}, newer "
                    f"than this tool's spec_version {SPEC_VERSION}; upgrade "
                    "dbt-agents-schema before unpublishing here\n"
                )
                return EXIT_CONFIG
            result = writer.unpublish(key)
        finally:
            writer.close()
    except MissingExtraError as exc:
        err.write(f"error: {exc}\n")
        return EXIT_CONFIG
    except WarehouseError as exc:
        err.write(f"error: {exc}\n")
        return EXIT_WAREHOUSE
    out.write(f"unpublished project {key!r} from {where}\n")
    _write_counts(out, "rows deleted", result.rows_deleted)
    return EXIT_OK


def _describe(target: ResolvedTarget) -> str:
    parts = [p for p in (target.database, target.schema) if p]
    return f"schema {'.'.join(parts)} (environment {target.environment})"


def _write_counts(out: TextIO, label: str, counts: Mapping[str, int]) -> None:
    out.write(f"{label}:\n")
    for table in TABLES:
        out.write(f"  {table}: {counts.get(table, 0)}\n")
