"""Skills loader tests (SPEC 5.5, 5.6 and the skill rules of section 8, T06)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from dbt_agents_schema.skills import SkillSet, load_skills, skill_key

FIXTURES = Path(__file__).parent / "fixtures" / "projects"


def make(root: Path, rel: str, text: str) -> Path:
    path = root / "agents" / "skills" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def load(root: Path, project: str = "proj") -> SkillSet:
    return load_skills(root, "agents/skills", project)


def rules(result: SkillSet) -> list[str]:
    return [f.rule for f in result.findings]


# --- discovery and keys ----------------------------------------------------------


def test_missing_directory_means_no_skills(tmp_path: Path) -> None:
    result = load(tmp_path)
    assert result.skills == ()
    assert result.findings == ()


def test_finance_fixture_keys_and_uses() -> None:
    result = load_skills(FIXTURES / "finance", "agents/skills", "finance")
    assert [s.key for s in result.skills] == [
        "skill/finance/payments/reconciliation",
        "skill/finance/revenue_overview",
    ]
    recon, overview = result.skills
    assert recon.uses_schemas == ("finance_marts",)
    assert recon.uses_tables == ("FINANCE_MARTS.FCT_PAYMENTS",)
    assert recon.has_uses is True
    assert overview.has_uses is False
    assert overview.uses_schemas == () and overview.uses_tables == ()
    assert recon.path == "payments/reconciliation"
    assert recon.content.startswith("---\nuses:")
    assert result.findings == ()


def test_nested_folders(tmp_path: Path) -> None:
    make(tmp_path, "a/b/c/deep.md", "# deep\n")
    make(tmp_path, "top.md", "# top\n")
    result = load(tmp_path)
    assert [s.key for s in result.skills] == ["skill/proj/a/b/c/deep", "skill/proj/top"]
    assert result.skills[0].file == tmp_path / "agents" / "skills" / "a" / "b" / "c" / "deep.md"


def test_only_markdown_files(tmp_path: Path) -> None:
    make(tmp_path, "notes.txt", "x")
    make(tmp_path, "README", "x")
    make(tmp_path, "upper.MD", "# upper\n")
    make(tmp_path, "ok.md", "# ok\n")
    assert [s.key for s in load(tmp_path).skills] == ["skill/proj/ok", "skill/proj/upper"]


def test_custom_skills_dir(tmp_path: Path) -> None:
    (tmp_path / "docs" / "agent").mkdir(parents=True)
    (tmp_path / "docs" / "agent" / "x.md").write_text("# x\n", encoding="utf-8")
    result = load_skills(tmp_path, "docs/agent", "proj")
    assert [s.key for s in result.skills] == ["skill/proj/x"]


@pytest.mark.parametrize(
    ("rel", "expected"),
    [
        ("reports\\daily.md", "skill/p/reports/daily"),
        ("a\\b/c.md", "skill/p/a/b/c"),
        ("a/b/c.md", "skill/p/a/b/c"),
        ("x.md", "skill/p/x"),
        ("x.MD", "skill/p/x"),
    ],
)
def test_skill_key_windows_separators(rel: str, expected: str) -> None:
    assert skill_key("p", rel) == expected


def test_windows_separator_in_file_name(tmp_path: Path) -> None:
    # On POSIX a backslash is a legal file-name character; treat it as a separator.
    make(tmp_path, "reports\\weekly.md", "# weekly\n")
    assert [s.key for s in load(tmp_path).skills] == ["skill/proj/reports/weekly"]


# --- frontmatter -----------------------------------------------------------------


def test_no_frontmatter(tmp_path: Path) -> None:
    make(tmp_path, "plain.md", "# Plain\n\n---\nnot frontmatter\n")
    (skill,) = load(tmp_path).skills
    assert skill.has_uses is False
    assert load(tmp_path).findings == ()


def test_frontmatter_without_uses(tmp_path: Path) -> None:
    make(tmp_path, "s.md", "---\ntitle: hi\n---\n# s\n")
    result = load(tmp_path)
    assert result.skills[0].has_uses is False
    assert result.findings == ()


def test_crlf_frontmatter(tmp_path: Path) -> None:
    path = tmp_path / "agents" / "skills" / "win.md"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"---\r\nuses:\r\n  schemas: [A]\r\n---\r\n# win\r\n")
    (skill,) = load(tmp_path).skills
    assert skill.uses_schemas == ("A",)


def test_invalid_yaml_frontmatter_is_finding(tmp_path: Path) -> None:
    make(tmp_path, "good.md", "# good\n")
    path = make(tmp_path, "broken.md", "---\nuses:\n  schemas: [BAD\n---\n# broken\n")
    result = load(tmp_path)
    assert rules(result) == ["skill_frontmatter_invalid"]
    finding = result.findings[0]
    assert finding.file == path
    assert finding.line is not None and finding.line >= 2
    assert finding.node == "skill/proj/broken"
    assert "broken.md" in finding.message
    # the skill still loads (published without uses) and the good one is unaffected
    assert [s.key for s in result.skills] == ["skill/proj/broken", "skill/proj/good"]
    assert result.skills[0].has_uses is False


def test_bad_fixture_broken_frontmatter() -> None:
    result = load_skills(FIXTURES / "bad", "agents/skills", "bad")
    invalid = [f for f in result.findings if f.rule == "skill_frontmatter_invalid"]
    assert [f.node for f in invalid] == ["skill/bad/broken_frontmatter"]


def test_unclosed_frontmatter_is_finding(tmp_path: Path) -> None:
    make(tmp_path, "open.md", "---\nuses:\n  schemas: [A]\n# no closing line\n")
    result = load(tmp_path)
    assert rules(result) == ["skill_frontmatter_invalid"]
    assert result.findings[0].line == 1


@pytest.mark.parametrize(
    "front",
    [
        "uses: FINANCE_MARTS",
        "uses: [FINANCE_MARTS]",
        "uses:\n  schemas: FINANCE_MARTS",
        "uses:\n  schemas: [1, 2]",
        "uses:\n  tables: [{a: b}]",
        "uses:\n  views: [X]",
        "- just\n- a list",
        "just a string",
    ],
)
def test_malformed_uses_is_finding_not_crash(tmp_path: Path, front: str) -> None:
    make(tmp_path, "m.md", f"---\n{front}\n---\n# m\n")
    result = load(tmp_path)
    assert rules(result) == ["skill_frontmatter_invalid"]
    assert result.findings[0].node == "skill/proj/m"
    assert result.skills[0].has_uses is False


def test_empty_uses_counts_as_no_uses(tmp_path: Path) -> None:
    make(tmp_path, "e.md", "---\nuses:\n---\n# e\n")
    make(tmp_path, "f.md", "---\nuses:\n  schemas: []\n---\n# f\n")
    result = load(tmp_path)
    assert result.findings == ()
    assert [s.has_uses for s in result.skills] == [False, False]


def test_uses_values_are_stripped_and_kept_in_order(tmp_path: Path) -> None:
    make(tmp_path, "u.md", "---\nuses:\n  tables: [' B.X ', A.Y]\n  schemas: [B, A]\n---\n")
    (skill,) = load(tmp_path).skills
    assert skill.uses_tables == ("B.X", "A.Y")
    assert skill.uses_schemas == ("B", "A")


# --- duplicates ------------------------------------------------------------------


def test_duplicate_keys_differing_only_by_case(tmp_path: Path) -> None:
    make(tmp_path, "reports/daily.md", "# a\n")
    make(tmp_path, "Reports\\Daily.md", "# b\n")  # same key once `\` is a separator
    result = load(tmp_path)
    assert rules(result) == ["skill_key_duplicate"]
    finding = result.findings[0]
    assert "reports/daily" in finding.message.lower()
    assert "Reports\\Daily.md" in finding.message
    assert "daily.md" in finding.message
    # only one skill per key survives so the plan cannot hold duplicate primary keys
    assert len([s for s in result.skills if s.key.lower() == "skill/proj/reports/daily"]) == 1


def test_bad_fixture_duplicate_in_tmp_copy(tmp_path: Path) -> None:
    root = tmp_path / "bad"
    shutil.copytree(FIXTURES / "bad", root)
    (root / "agents" / "skills" / "Reports\\Daily.md").write_text("# dup\n", encoding="utf-8")
    result = load_skills(root, "agents/skills", "bad")
    dups = [f for f in result.findings if f.rule == "skill_key_duplicate"]
    assert len(dups) == 1
    assert dups[0].node is not None and dups[0].node.lower() == "skill/bad/reports/daily"


def test_deterministic_order(tmp_path: Path) -> None:
    for name in ["z.md", "a.md", "m/b.md", "B.md"]:
        make(tmp_path, name, "# x\n")
    first = load(tmp_path)
    second = load(tmp_path)
    assert first == second
    keys = [s.key for s in first.skills]
    assert keys == sorted(keys)


def test_non_utf8_file_is_finding_not_crash(tmp_path: Path) -> None:
    path = tmp_path / "agents" / "skills" / "latin1.md"
    path.parent.mkdir(parents=True)
    path.write_bytes("# caf\xe9\n".encode("latin-1"))
    result = load(tmp_path)
    assert rules(result) == ["skill_frontmatter_invalid"]
    assert "UTF-8" in result.findings[0].message
    assert result.findings[0].file == path
