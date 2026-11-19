"""Check engine (SPEC section 8): validate config, manifest and skills; no warehouse.

`check_project` loads the inputs and returns every finding plus the loaded objects, so
`publish` can reuse them. Formatters render findings as text, JSON, GitHub annotations
or a GitLab Code Quality report.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from dbt_agents_schema.config import (
    CONFIG_FILENAME,
    Config,
    ConfigError,
    dbt_project_name,
    default_config,
    load_config,
    validate_project_key,
)
from dbt_agents_schema.manifest import (
    Manifest,
    ManifestContent,
    ManifestError,
    ModelRecord,
    load_manifest,
    read_models,
)
from dbt_agents_schema.skills import Skill, SkillSet, load_skills

FindingSeverity = Literal["error", "warning", "info"]

HARD_ERROR_RULES = frozenset(
    {
        "config_invalid",
        "manifest_missing",
        "project_key_invalid",
        "skill_key_duplicate",
        "skill_frontmatter_invalid",
    }
)
DESCRIPTION_SCOPE_EMPTY = "description_scope_empty"  # the info line of SPEC 3

SAMPLED_VALUE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\b(min|max|mean|avg|average|median|std(dev)?|p\d{2})\s*[:=]\s*-?\d",
        r"\d{1,3}(\.\d+)?\s*%\s*(null|nulls|of rows|non-null|distinct|populated)",
        r"\b(distinct|unique|sample|example|top)\s+values?\s*[:=(]",
        r"\b\d[\d,]{2,}\s+(rows|records)\b",
        r"\bnull\s+(rate|percentage|pct|count)\b",
    )
)

_SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}
_GITLAB_SEVERITY = {"error": "major", "warning": "minor", "info": "info"}
_GITHUB_COMMAND = {"error": "error", "warning": "warning", "info": "notice"}


@dataclass(frozen=True)
class Finding:
    rule: str
    severity: FindingSeverity
    message: str
    file: str | None = None
    line: int | None = None
    node: str | None = None


@dataclass(frozen=True)
class CheckResult:
    findings: tuple[Finding, ...]
    config: Config | None = field(default=None, compare=False)
    manifest: Manifest | None = field(default=None, compare=False)
    skills: SkillSet | None = field(default=None, compare=False)
    manifest_missing: bool = False
    root_package: str | None = None

    @property
    def has_errors(self) -> bool:
        return any(f.severity == "error" for f in self.findings)

    def summary(self) -> dict[str, int]:
        counts = {"error": 0, "warning": 0, "info": 0}
        for f in self.findings:
            counts[f.severity] += 1
        return counts


def looks_like_sampled_values(text: str) -> bool:
    """True if `text` looks like data profiling output (SPEC 8 heuristics)."""
    return any(p.search(text) for p in SAMPLED_VALUE_PATTERNS)


def check_project(
    project_dir: Path,
    *,
    config_path: Path | None = None,
    manifest_path: Path | None = None,
    env: str | None = None,
) -> CheckResult:
    """Run every check rule for the dbt project at `project_dir`.

    `env` turns on the environment-dependent rules (`manifest_target_mismatch`) for that
    environment; an environment that is not defined, or has no `parse_target`, adds none.
    """
    findings: list[Finding] = []

    manifest_file = (
        manifest_path if manifest_path is not None else project_dir / "target" / "manifest.json"
    )
    manifest: Manifest | None = None
    try:
        manifest = load_manifest(manifest_file)
    except ManifestError as exc:
        findings.append(Finding("manifest_missing", "error", str(exc), file=str(manifest_file)))
    manifest_name = manifest.project_name if manifest is not None else None

    config: Config | None
    try:
        config = load_config(project_dir, config_path, manifest_project_name=manifest_name)
    except ConfigError as exc:
        file = str(exc.file) if exc.file is not None else None
        findings.extend(
            Finding(
                "config_invalid",
                "error",
                f"{issue.path}: {issue.message}",
                file=file,
                line=issue.line,
            )
            for issue in exc.issues
        )
        try:
            config = default_config(project_dir, config_path, manifest_project_name=manifest_name)
        except ConfigError:
            config = None
    else:
        if not config.found:
            findings.append(
                Finding(
                    "config_missing",
                    "warning",
                    f"no {CONFIG_FILENAME} at {config.path}; every setting takes its default. "
                    "Run `dbt-agents-schema init` to create one",
                    file=str(config.path),
                )
            )

    skills: SkillSet | None = None
    root_package: str | None = None
    if config is not None:
        key_problem = validate_project_key(config.project)
        if key_problem is not None:
            findings.append(
                Finding("project_key_invalid", "error", key_problem, file=str(config.path))
            )
        skills = load_skills(project_dir, config.skills_dir, config.project)
        findings.extend(
            Finding(f.rule, "error", f.message, file=str(f.file), line=f.line, node=f.node)
            for f in skills.findings
        )
        broken = {f.node for f in skills.findings if f.rule == "skill_frontmatter_invalid"}
        findings.extend(_skill_without_uses(skills.skills, broken))
        if manifest is not None:
            root_package = manifest.project_name or dbt_project_name(project_dir)
            try:
                content = read_models(
                    manifest,
                    project=config.project,
                    include_packages=config.include_packages,
                    exclude_packages=config.exclude_packages,
                    root_package=root_package,
                )
            except ManifestError as exc:
                findings.append(
                    Finding("manifest_missing", "error", str(exc), file=str(manifest_file))
                )
                manifest = None
            else:
                findings.extend(
                    _description_findings(project_dir, root_package, content, manifest, config)
                )
                findings.extend(_unresolved_uses(skills.skills, content, config))
                findings.extend(_manifest_findings(manifest_file, content))
                if env is not None and env in config.environments:
                    findings.extend(
                        _target_mismatch(config, env, content, manifest, project_dir, root_package)
                    )

    return CheckResult(
        findings=_apply_severities(findings, config),
        config=config,
        manifest=manifest,
        skills=skills,
        manifest_missing=manifest is None,
        root_package=root_package,
    )


# --- rules -------------------------------------------------------------------------


def _skill_without_uses(skills: Iterable[Skill], broken: set[str]) -> Iterable[Finding]:
    for skill in skills:
        if not skill.has_uses and skill.key not in broken:
            yield Finding(
                "skill_without_uses",
                "warning",
                f"skill {skill.key} declares no `uses` frontmatter; add `uses: {{schemas: "
                "[...], tables: [...]}` so agents know which data it covers",
                file=str(skill.file),
                line=1,
                node=skill.key,
            )


def _description_findings(
    project_dir: Path,
    root_package: str | None,
    content: ManifestContent,
    manifest: Manifest,
    config: Config,
) -> Iterable[Finding]:
    nodes: dict[str, Any] = dict(manifest.raw["nodes"])
    scope_all = config.check.description_scope == "all"
    in_scope = {m.unique_id for m in content.models if scope_all or m.access == "public"}
    if not scope_all and not in_scope:
        yield Finding(
            DESCRIPTION_SCOPE_EMPTY,
            "info",
            "description_scope is `public` but no published model has `access: public`, "
            "so the description rules cover nothing; set `check.description_scope: all` in "
            f"{CONFIG_FILENAME} or mark public models with `access: public`",
            file=str(config.path) if config.found else None,
        )
    models = {m.unique_id: m for m in content.models}
    for model in content.models:
        file = _model_file(project_dir, root_package, nodes.get(model.unique_id), model)
        if model.unique_id in in_scope and not model.description.strip():
            yield Finding(
                "missing_model_description",
                "warning",
                f"model {model.name} ({model.unique_id}) has no description; add one in its "
                "properties YAML",
                file=file,
                node=model.unique_id,
            )
        if looks_like_sampled_values(model.description):
            yield Finding(
                "sampled_values_in_description",
                "error",
                f"description of model {model.name} looks like data profiling output "
                "(sampled values, row counts or statistics); describe meaning, not data, "
                "because descriptions are published to the warehouse",
                file=file,
                node=model.unique_id,
            )
    for column in content.columns:
        model = models[column.model_id]
        file = _model_file(project_dir, root_package, nodes.get(model.unique_id), model)
        node = f"{column.model_id}.{column.column_name}"
        if column.model_id in in_scope and not column.description.strip():
            yield Finding(
                "missing_column_description",
                "warning",
                f"column {column.column_name} of model {model.name} has no description; add "
                "one in the model's properties YAML",
                file=file,
                node=node,
            )
        if looks_like_sampled_values(column.description):
            yield Finding(
                "sampled_values_in_description",
                "error",
                f"description of column {column.column_name} of model {model.name} looks like "
                "data profiling output (sampled values, row counts or statistics); describe "
                "meaning, not data",
                file=file,
                node=node,
            )


def _manifest_findings(manifest_file: Path, content: ManifestContent) -> Iterable[Finding]:
    if not content.models:
        yield Finding(
            "manifest_empty",
            "error",
            f"manifest {manifest_file} has no published models; check that `dbt parse` ran in "
            f"the right project and that include_packages/exclude_packages in {CONFIG_FILENAME} "
            "do not exclude every package",
            file=str(manifest_file),
        )
        return
    if not any(c.meta for c in content.columns):
        yield Finding(
            "column_meta_missing",
            "warning",
            "no column of any published model has a non-empty `meta`; parse with dbt Fusion "
            "(which copies model meta to columns) or set column meta in the properties YAML",
            file=str(manifest_file),
        )


def _target_mismatch(
    config: Config,
    env: str,
    content: ManifestContent,
    manifest: Manifest,
    project_dir: Path,
    root_package: str | None,
) -> Iterable[Finding]:
    target = config.environments[env].parse_target
    if target is None:
        return
    nodes: dict[str, Any] = dict(manifest.raw["nodes"])
    want_db = target.database.lower() if target.database is not None else None
    want_schema = target.schema.lower()
    expected = (
        f"database {target.database}, schema starting with {target.schema}"
        if target.database is not None
        else f"schema starting with {target.schema}"
    )
    for model in content.models:
        db_ok = want_db is None or (model.database_name or "").lower() == want_db
        schema_ok = (model.schema_name or "").lower().startswith(want_schema)
        if db_ok and schema_ok:
            continue
        yield Finding(
            "manifest_target_mismatch",
            "error",
            f"model {model.name} ({model.unique_id}) resolves to "
            f"{model.database_name}.{model.schema_name}, but environments.{env}.parse_target "
            f"expects {expected}; run `dbt parse` against the production target "
            f"(`write-parse-profile --env {env}`) or fix parse_target in {CONFIG_FILENAME}",
            file=_model_file(project_dir, root_package, nodes.get(model.unique_id), model),
            node=model.unique_id,
        )


def _model_file(project_dir: Path, root_package: str | None, node: Any, model: ModelRecord) -> str:
    """Properties YAML (`patch_path`) of a model, else its SQL file. Files of installed
    packages live under `dbt_packages/<package>/`."""
    patch = node.get("patch_path") if isinstance(node, dict) else None
    if isinstance(patch, str) and patch:
        relative = patch.split("://", 1)[1] if "://" in patch else patch
    else:
        relative = model.file_path or ""
    root = project_dir
    if root_package is not None and model.package_name != root_package:
        root = project_dir / "dbt_packages" / model.package_name
    return str(root / relative)


def _unresolved_uses(
    skills: Iterable[Skill], content: ManifestContent, config: Config
) -> Iterable[Finding]:
    schemas = {m.schema_name.lower() for m in content.models if m.schema_name}
    external_ci = {s.lower() for s in config.check.external_schemas}
    tables: set[str] = set()
    for m in content.models:
        if m.schema_name:
            tables.add(f"{m.schema_name}.{m.alias}".lower())
            if m.database_name:
                tables.add(f"{m.database_name}.{m.schema_name}.{m.alias}".lower())
    for skill in skills:
        for ref in skill.uses_schemas:
            if ref.lower() not in schemas and ref.lower() not in external_ci:
                yield _unresolved(skill, "schemas", ref, "is not the schema of any published model")
        for ref in skill.uses_tables:
            parts = ref.split(".")
            table_schema = parts[-2].lower() if len(parts) >= 2 else None
            if ref.lower() in tables or (table_schema is not None and table_schema in external_ci):
                continue
            yield _unresolved(
                skill,
                "tables",
                ref,
                "matches no published model as schema.alias or database.schema.alias",
            )


def _unresolved(skill: Skill, kind: str, ref: str, why: str) -> Finding:
    return Finding(
        "skill_unresolved_uses",
        "error",
        f"uses.{kind} entry '{ref}' in skill {skill.key} {why}; fix the name, or add the "
        f"schema to check.external_schemas in {CONFIG_FILENAME} if dbt does not build it",
        file=str(skill.file),
        line=_line_of(skill.content, ref),
        node=skill.key,
    )


def _line_of(text: str, needle: str) -> int:
    lines = text.splitlines()
    for number, line in enumerate(lines[1:], start=2):
        if line.strip() in ("---", "..."):
            break
        if needle in line:
            return number
    return 1


def _apply_severities(findings: list[Finding], config: Config | None) -> tuple[Finding, ...]:
    rules = config.check.rules if config is not None else {}
    out: list[Finding] = []
    for f in findings:
        if f.rule in HARD_ERROR_RULES or f.severity == "info":
            out.append(f)
            continue
        severity = rules.get(f.rule, f.severity)
        if severity == "off":
            continue
        out.append(Finding(f.rule, severity, f.message, f.file, f.line, f.node))
    return tuple(sorted(set(out), key=_sort_key))


def _sort_key(f: Finding) -> tuple[str, int, str, str, str]:
    return (f.file or "", f.line or 0, f.rule, f.node or "", f.message)


# --- output formats ----------------------------------------------------------------


def format_text(result: CheckResult) -> str:
    lines: list[str] = []
    for f in result.findings:
        where = f.file or ""
        if f.file and f.line:
            where += f":{f.line}"
        prefix = f"{f.severity}[{f.rule}]"
        lines.append(f"{prefix} {where}: {f.message}" if where else f"{prefix} {f.message}")
    s = result.summary()
    lines.append(f"{s['error']} errors, {s['warning']} warnings, {s['info']} info")
    return "\n".join(lines) + "\n"


def format_json(result: CheckResult) -> str:
    data = {"findings": [asdict(f) for f in result.findings], "summary": result.summary()}
    return json.dumps(data, indent=2) + "\n"


def format_github(result: CheckResult) -> str:
    lines: list[str] = []
    for f in result.findings:
        props: list[str] = []
        if f.file:
            props.append(f"file={_gh_property(f.file)}")
        if f.file and f.line:
            props.append(f"line={f.line}")
        props.append(f"title={_gh_property(f.rule)}")
        lines.append(f"::{_GITHUB_COMMAND[f.severity]} {','.join(props)}::{_gh_data(f.message)}")
    return "".join(line + "\n" for line in lines)


def gitlab_report(result: CheckResult) -> list[dict[str, Any]]:
    """GitLab Code Quality report: one issue per finding."""
    report: list[dict[str, Any]] = []
    for f in result.findings:
        identity = "\x1f".join([f.rule, f.file or "", f.node or "", str(f.line or ""), f.message])
        report.append(
            {
                "description": f"[{f.rule}] {f.message}",
                "check_name": f.rule,
                "fingerprint": hashlib.sha256(identity.encode("utf-8")).hexdigest(),
                "severity": _GITLAB_SEVERITY[f.severity],
                "location": {
                    "path": f.file or CONFIG_FILENAME,
                    "lines": {"begin": f.line or 1},
                },
            }
        )
    return report


def _gh_data(value: str) -> str:
    return value.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def _gh_property(value: str) -> str:
    return _gh_data(value).replace(":", "%3A").replace(",", "%2C")
