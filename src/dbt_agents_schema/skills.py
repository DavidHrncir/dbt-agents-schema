"""Load markdown skill files from `skills_dir` (SPEC 5.5, 5.6 and section 8).

Pure logic: reads files and returns typed records; never touches a warehouse. Problems
in individual files become findings, never exceptions, so `check` can report them all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import yaml

SkillRule = Literal["skill_frontmatter_invalid", "skill_key_duplicate"]

_FRONTMATTER_DELIMITER = re.compile(r"^---[ \t]*$")
_FRONTMATTER_END = re.compile(r"^(?:---|\.\.\.)[ \t]*$")
_USES_KEYS = ("schemas", "tables")


@dataclass(frozen=True)
class Skill:
    key: str  # `skill/<project>/<path>`
    path: str  # path under skills_dir, `/`-separated, without `.md`
    file: Path
    content: str
    uses_schemas: tuple[str, ...] = ()
    uses_tables: tuple[str, ...] = ()

    @property
    def has_uses(self) -> bool:
        return bool(self.uses_schemas or self.uses_tables)


@dataclass(frozen=True)
class SkillFinding:
    rule: SkillRule
    message: str
    file: Path
    line: int | None
    node: str


@dataclass(frozen=True)
class SkillSet:
    skills: tuple[Skill, ...]  # sorted by key, at most one per case-insensitive key
    findings: tuple[SkillFinding, ...]


def skill_key(project: str, relative_path: str) -> str:
    """ROOT key for a skill file: `\\` counts as a separator; the `.md` suffix is dropped."""
    return f"skill/{project}/{_skill_path(relative_path)}"


def load_skills(project_dir: Path, skills_dir: str, project: str) -> SkillSet:
    """Walk `<project_dir>/<skills_dir>` for `*.md` files. A missing directory is no skills."""
    root = project_dir / skills_dir
    if not root.is_dir():
        return SkillSet(skills=(), findings=())

    files = sorted(
        (p for p in root.rglob("*") if p.is_file() and p.suffix.lower() == ".md"),
        key=lambda p: p.relative_to(root).as_posix(),
    )
    findings: list[SkillFinding] = []
    by_key: dict[str, Skill] = {}
    for file in files:
        relative = file.relative_to(root).as_posix()
        key = skill_key(project, relative)
        existing = by_key.get(key.lower())
        if existing is not None:
            findings.append(
                SkillFinding(
                    rule="skill_key_duplicate",
                    message=(
                        f"{file} and {existing.file} both map to skill key {existing.key!r} "
                        "(keys are compared case-insensitively and `\\` counts as `/`); "
                        "rename or remove one of them"
                    ),
                    file=file,
                    line=None,
                    node=existing.key,
                )
            )
            continue
        skill, finding = _load_one(file, key, _skill_path(relative))
        by_key[key.lower()] = skill
        if finding is not None:
            findings.append(finding)

    skills = tuple(sorted(by_key.values(), key=lambda s: s.key))
    return SkillSet(skills=skills, findings=tuple(findings))


# --- internals ---------------------------------------------------------------------


def _skill_path(relative_path: str) -> str:
    path = relative_path.replace("\\", "/")
    if path.lower().endswith(".md"):
        path = path[: -len(".md")]
    return "/".join(part for part in path.split("/") if part)


def _load_one(file: Path, key: str, path: str) -> tuple[Skill, SkillFinding | None]:
    content = file.read_text(encoding="utf-8-sig")
    plain = Skill(key=key, path=path, file=file, content=content)

    def invalid(message: str, line: int | None) -> tuple[Skill, SkillFinding]:
        return plain, SkillFinding(
            rule="skill_frontmatter_invalid",
            message=f"{file}: {message}",
            file=file,
            line=line,
            node=key,
        )

    lines = content.splitlines()
    if not lines or not _FRONTMATTER_DELIMITER.match(lines[0]):
        return plain, None
    end = next((i for i in range(1, len(lines)) if _FRONTMATTER_END.match(lines[i])), None)
    if end is None:
        return invalid("frontmatter starts with `---` but has no closing `---` line", 1)

    try:
        data: Any = yaml.safe_load("\n".join(lines[1:end]))
    except yaml.YAMLError as exc:
        mark = getattr(exc, "problem_mark", None)
        line = mark.line + 2 if mark is not None else 1
        problem = getattr(exc, "problem", None) or str(exc)
        return invalid(f"frontmatter is not valid YAML ({problem}); fix the YAML syntax", line)

    if data is None:
        return plain, None
    if not isinstance(data, dict):
        return invalid("frontmatter must be a YAML mapping such as `uses: {schemas: [...]}`", 1)
    uses = data.get("uses")
    if uses is None:
        return plain, None
    expected = "`uses` must be a mapping with optional `schemas` and `tables` lists of strings"
    if not isinstance(uses, dict):
        return invalid(f"{expected}; got {type(uses).__name__}", 2)
    unknown = sorted(str(k) for k in uses if k not in _USES_KEYS)
    if unknown:
        return invalid(f"{expected}; unknown key(s): {', '.join(unknown)}", 2)
    values: dict[str, tuple[str, ...]] = {}
    for name in _USES_KEYS:
        raw = uses.get(name)
        if raw is None:
            values[name] = ()
            continue
        if not isinstance(raw, list) or not all(isinstance(v, str) for v in raw):
            return invalid(f"{expected}; `uses.{name}` is not a list of strings", 2)
        values[name] = tuple(v.strip() for v in raw if v.strip())
    return (
        Skill(
            key=key,
            path=path,
            file=file,
            content=content,
            uses_schemas=values["schemas"],
            uses_tables=values["tables"],
        ),
        None,
    )
