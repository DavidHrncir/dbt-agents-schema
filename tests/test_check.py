"""Check engine tests (SPEC section 8, T08)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import jsonschema
import pytest

from dbt_agents_schema.check import (
    HARD_ERROR_RULES,
    SAMPLED_VALUE_PATTERNS,
    CheckResult,
    Finding,
    check_project,
    format_github,
    format_json,
    format_text,
    gitlab_report,
    looks_like_sampled_values,
)

HERE = Path(__file__).parent
PROJECTS = HERE / "fixtures" / "projects"
MANIFESTS = HERE / "fixtures" / "manifests"
BAD_NAME = "bad_project_with_an_intentionally_long_name_that_breaks_the_project_key_rule"


# --- helpers -----------------------------------------------------------------------


def copy_project(tmp_path: Path, name: str, manifest: str | None = None) -> Path:
    """Copy a fixture project into tmp_path and put its manifest at target/manifest.json."""
    root = tmp_path / name
    shutil.copytree(PROJECTS / name, root)
    if manifest is not None:
        (root / "target").mkdir()
        shutil.copy(MANIFESTS / f"{manifest}.json", root / "target" / "manifest.json")
    return root


def edit_manifest(root: Path, fn: Any) -> None:
    path = root / "target" / "manifest.json"
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data))


def check(root: Path, **kwargs: Any) -> CheckResult:
    return check_project(root, **kwargs)


def rules(result: CheckResult) -> set[str]:
    return {f.rule for f in result.findings}


def of(result: CheckResult, rule: str) -> list[Finding]:
    return [f for f in result.findings if f.rule == rule]


def write_config(root: Path, text: str) -> None:
    (root / "agents_schema.yml").write_text(text, encoding="utf-8")


def write_skill(root: Path, rel: str, text: str) -> None:
    path = root / "agents" / "skills" / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def finance(tmp_path: Path) -> Path:
    return copy_project(tmp_path, "finance", manifest="finance")


@pytest.fixture
def marketing(tmp_path: Path) -> Path:
    return copy_project(tmp_path, "marketing", manifest="marketing")


# --- clean fixtures ----------------------------------------------------------------


def test_finance_is_clean_except_skill_without_uses(finance: Path) -> None:
    result = check(finance)
    assert not result.has_errors
    assert rules(result) == {"skill_without_uses"}
    (f,) = of(result, "skill_without_uses")
    assert f.severity == "warning"
    assert f.node == "skill/finance/revenue_overview"
    assert "add `uses: {schemas: [...], tables: [...]}` so agents" in f.message


def test_marketing_is_clean_except_skill_without_uses(marketing: Path) -> None:
    result = check(marketing)
    assert not result.has_errors
    assert rules(result) == {"skill_without_uses"}


def test_result_exposes_loaded_inputs(finance: Path) -> None:
    result = check(finance)
    assert result.config is not None and result.config.project == "finance"
    assert result.manifest is not None and result.manifest.project_name == "finance"
    assert result.skills is not None and len(result.skills.skills) == 2


# --- the bad fixture triggers every rule -------------------------------------------


ALL_RULES = {
    "config_invalid",
    "manifest_missing",
    "project_key_invalid",
    "skill_key_duplicate",
    "skill_frontmatter_invalid",
    "config_missing",
    "missing_model_description",
    "missing_column_description",
    "sampled_values_in_description",
    "skill_without_uses",
    "skill_unresolved_uses",
}


def test_bad_fixture_triggers_every_rule(tmp_path: Path) -> None:
    bad = copy_project(tmp_path, "bad")
    (bad / "agents" / "skills" / "Reports\\Daily.md").write_text("# dup\n", encoding="utf-8")
    seen: set[str] = set()
    manifest = MANIFESTS / "bad.json"
    seen |= rules(check(bad, manifest_path=manifest))
    seen |= rules(check(bad))  # no target/manifest.json
    seen |= rules(check(bad, manifest_path=manifest, config_path=bad / "missing.yml"))
    seen |= rules(check(bad, manifest_path=manifest, config_path=bad / "agents_schema.invalid.yml"))
    seen |= rules(
        check(bad, manifest_path=manifest, config_path=bad / "agents_schema.unparseable.yml")
    )
    assert seen >= ALL_RULES


def test_bad_default_run(tmp_path: Path) -> None:
    bad = copy_project(tmp_path, "bad")
    result = check(bad, manifest_path=MANIFESTS / "bad.json")
    assert result.has_errors
    assert rules(result) >= {
        "project_key_invalid",
        "skill_frontmatter_invalid",
        "skill_without_uses",
        "skill_unresolved_uses",
        "missing_model_description",
        "missing_column_description",
        "sampled_values_in_description",
    }
    nodes = {f.node for f in of(result, "missing_model_description")}
    assert nodes == {f"model.{BAD_NAME}.orders"}
    cols = {f.node for f in of(result, "missing_column_description")}
    assert cols == {f"model.{BAD_NAME}.orders.order_id"}
    sampled = {f.node for f in of(result, "sampled_values_in_description")}
    assert sampled == {
        f"model.{BAD_NAME}.customers",
        f"model.{BAD_NAME}.customers.customer_id",
        f"model.{BAD_NAME}.customers.lifetime_value",
        f"model.{BAD_NAME}.customers.tier",
    }


# --- hard errors -------------------------------------------------------------------


def test_hard_error_rules() -> None:
    assert (
        frozenset(
            {
                "config_invalid",
                "manifest_missing",
                "project_key_invalid",
                "skill_key_duplicate",
                "skill_frontmatter_invalid",
            }
        )
        == HARD_ERROR_RULES
    )


def test_config_invalid_reports_json_path(finance: Path) -> None:
    write_config(finance, "version: 1\nproject: finance\ncolour: blue\n")
    result = check(finance)
    (f,) = of(result, "config_invalid")
    assert f.severity == "error"
    assert "$" in f.message and "colour" in f.message
    assert f.file == str(finance / "agents_schema.yml")
    assert f.line is not None


def test_config_invalid_yaml_parse_error(finance: Path) -> None:
    write_config(finance, "version: 1\nproject: [finance\n")
    result = check(finance)
    assert of(result, "config_invalid")
    assert result.has_errors


def test_config_invalid_still_checks_skills_with_defaults(finance: Path) -> None:
    write_config(finance, "version: 1\nproject: finance\ncolour: blue\n")
    result = check(finance)
    assert "skill_without_uses" in rules(result)
    assert "config_missing" not in rules(result)


def test_config_valid_negative(finance: Path) -> None:
    assert "config_invalid" not in rules(check(finance))


def test_manifest_missing(tmp_path: Path) -> None:
    root = copy_project(tmp_path, "finance")
    result = check(root)
    (f,) = of(result, "manifest_missing")
    assert f.severity == "error"
    assert "run `dbt parse` first" in f.message
    assert f.file == str(root / "target" / "manifest.json")
    assert result.manifest_missing


def test_manifest_missing_negative(finance: Path) -> None:
    result = check(finance)
    assert "manifest_missing" not in rules(result)
    assert not result.manifest_missing


def test_explicit_manifest_path(tmp_path: Path) -> None:
    root = copy_project(tmp_path, "finance")
    assert "manifest_missing" not in rules(check(root, manifest_path=MANIFESTS / "finance.json"))


def test_manifest_unreadable_is_manifest_finding(finance: Path) -> None:
    (finance / "target" / "manifest.json").write_text("{nope", encoding="utf-8")
    result = check(finance)
    (f,) = of(result, "manifest_missing")
    assert "JSON" in f.message
    assert result.manifest_missing


def test_project_key_invalid(finance: Path) -> None:
    write_config(finance, "version: 1\nproject: Finance\n")
    result = check(finance)
    # The JSON Schema leaves the key pattern to check, so this is not config_invalid.
    (f,) = of(result, "project_key_invalid")
    assert f.severity == "error"
    assert "^[a-z][a-z0-9_]{0,62}$" in f.message


def test_project_key_valid_negative(finance: Path) -> None:
    assert "project_key_invalid" not in rules(check(finance))


def test_skill_key_duplicate(finance: Path) -> None:
    # A separate file even on case-insensitive filesystems; maps to the same key.
    write_skill(finance, "Payments\\Reconciliation.md", "# dup\n")
    result = check(finance)
    (f,) = of(result, "skill_key_duplicate")
    assert f.severity == "error"


def test_skill_frontmatter_invalid(finance: Path) -> None:
    write_skill(finance, "broken.md", "---\nuses: [a\n---\n")
    result = check(finance)
    (f,) = of(result, "skill_frontmatter_invalid")
    assert f.severity == "error"
    assert f.line is not None
    # no extra skill_without_uses noise for the broken file
    assert "skill/finance/broken" not in {x.node for x in of(result, "skill_without_uses")}


def test_hard_errors_cannot_be_turned_off(finance: Path) -> None:
    write_skill(finance, "broken.md", "---\nuses: [a\n---\n")
    write_config(
        finance, "version: 1\nproject: finance\ncheck:\n  rules:\n    config_missing: off\n"
    )
    assert of(check(finance), "skill_frontmatter_invalid")[0].severity == "error"


# --- configurable rules ------------------------------------------------------------


def test_config_missing(finance: Path) -> None:
    (finance / "agents_schema.yml").unlink()
    (f,) = of(check(finance), "config_missing")
    assert f.severity == "warning"
    assert f.file == str(finance / "agents_schema.yml")


def test_config_missing_negative(finance: Path) -> None:
    assert "config_missing" not in rules(check(finance))


def test_config_missing_explicit_path(finance: Path) -> None:
    result = check(finance, config_path=finance / "nope.yml")
    assert of(result, "config_missing")


def drop_description(data: dict[str, Any], uid: str, column: str | None = None) -> None:
    node = data["nodes"][uid]
    if column is None:
        node["description"] = ""
    else:
        node["columns"][column]["description"] = "  "


def test_missing_model_description_in_public_scope(finance: Path) -> None:
    edit_manifest(finance, lambda d: drop_description(d, "model.finance.fct_revenue"))
    (f,) = of(check(finance), "missing_model_description")
    assert f.node == "model.finance.fct_revenue"
    assert f.severity == "warning"
    assert f.file == str(finance / "models" / "schema.yml")


def test_missing_model_description_outside_public_scope_ignored(finance: Path) -> None:
    edit_manifest(finance, lambda d: drop_description(d, "model.finance.stg_invoices"))
    assert "missing_model_description" not in rules(check(finance))


def test_missing_model_description_scope_all(finance: Path) -> None:
    edit_manifest(finance, lambda d: drop_description(d, "model.finance.stg_invoices"))
    write_config(finance, "version: 1\nproject: finance\ncheck:\n  description_scope: all\n")
    (f,) = of(check(finance), "missing_model_description")
    assert f.node == "model.finance.stg_invoices"


def test_missing_model_description_negative(finance: Path) -> None:
    write_config(finance, "version: 1\nproject: finance\ncheck:\n  description_scope: all\n")
    assert "missing_model_description" not in rules(check(finance))


def test_missing_column_description(finance: Path) -> None:
    edit_manifest(
        finance, lambda d: drop_description(d, "model.finance.fct_revenue", "revenue_usd")
    )
    (f,) = of(check(finance), "missing_column_description")
    assert f.node == "model.finance.fct_revenue.revenue_usd"
    assert f.severity == "warning"


def test_missing_column_description_out_of_scope(finance: Path) -> None:
    edit_manifest(
        finance, lambda d: drop_description(d, "model.finance.stg_invoices", "invoice_id")
    )
    assert "missing_column_description" not in rules(check(finance))


def test_missing_column_description_negative(finance: Path) -> None:
    write_config(finance, "version: 1\nproject: finance\ncheck:\n  description_scope: all\n")
    assert "missing_column_description" not in rules(check(finance))


def test_excluded_package_models_not_checked(finance: Path) -> None:
    edit_manifest(finance, lambda d: drop_description(d, "model.shared_pkg.dim_calendar"))
    write_config(
        finance,
        "version: 1\nproject: finance\ninclude_packages: false\ncheck:\n  description_scope: all\n",
    )
    assert "missing_model_description" not in rules(check(finance))


def test_public_scope_with_no_public_models_emits_info(finance: Path) -> None:
    def no_access(d: dict[str, Any]) -> None:
        for n in d["nodes"].values():
            if n["resource_type"] == "model":
                n["access"] = "protected"
                n["config"]["access"] = "protected"
                n["description"] = ""

    edit_manifest(finance, no_access)
    result = check(finance)
    info = [f for f in result.findings if f.severity == "info"]
    assert len(info) == 1
    assert "all" in info[0].message
    assert "missing_model_description" not in rules(result)
    assert not result.has_errors


def test_no_info_when_public_models_exist(finance: Path) -> None:
    assert not [f for f in check(finance).findings if f.severity == "info"]


def test_sampled_values_in_model_description(finance: Path) -> None:
    def sample(d: dict[str, Any]) -> None:
        d["nodes"]["model.finance.stg_invoices"]["description"] = "Invoices. 12,345 rows."

    edit_manifest(finance, sample)
    (f,) = of(check(finance), "sampled_values_in_description")
    assert f.severity == "error"
    assert f.node == "model.finance.stg_invoices"  # applies beyond description_scope


def test_sampled_values_in_column_description(finance: Path) -> None:
    def sample(d: dict[str, Any]) -> None:
        col = d["nodes"]["model.finance.fct_revenue"]["columns"]["revenue_usd"]
        col["description"] = "Revenue. min: 0, max: 10"

    edit_manifest(finance, sample)
    (f,) = of(check(finance), "sampled_values_in_description")
    assert f.node == "model.finance.fct_revenue.revenue_usd"


def test_sampled_values_negative(finance: Path) -> None:
    assert "sampled_values_in_description" not in rules(check(finance))


def test_rule_severity_override_and_off(finance: Path) -> None:
    write_config(
        finance,
        "version: 1\nproject: finance\ncheck:\n  rules:\n    skill_without_uses: error\n",
    )
    result = check(finance)
    assert of(result, "skill_without_uses")[0].severity == "error"
    assert result.has_errors
    write_config(
        finance, "version: 1\nproject: finance\ncheck:\n  rules:\n    skill_without_uses: off\n"
    )
    assert check(finance).findings == ()


def test_skill_without_uses_negative(marketing: Path) -> None:
    nodes = {f.node for f in of(check(marketing), "skill_without_uses")}
    assert "skill/marketing/attribution" not in nodes


# --- skill_unresolved_uses ---------------------------------------------------------


def uses(schemas: list[str] | None = None, tables: list[str] | None = None) -> str:
    lines = ["---", "uses:"]
    if schemas is not None:
        lines.append(f"  schemas: {json.dumps(schemas)}")
    if tables is not None:
        lines.append(f"  tables: {json.dumps(tables)}")
    return "\n".join([*lines, "---", "# skill", ""])


def unresolved(root: Path) -> list[Finding]:
    return of(check(root), "skill_unresolved_uses")


def test_unresolved_schema(finance: Path) -> None:
    write_skill(finance, "x.md", uses(schemas=["nowhere"]))
    (f,) = unresolved(finance)
    assert f.severity == "error"
    assert f.node == "skill/finance/x"
    assert "nowhere" in f.message
    assert f.line == 3


def test_resolved_schema(finance: Path) -> None:
    write_skill(finance, "x.md", uses(schemas=["finance_marts", "finance_staging", "finance"]))
    assert unresolved(finance) == []


def test_schema_in_external_schemas(finance: Path) -> None:
    write_skill(finance, "x.md", uses(schemas=["raw_erp"]))
    write_config(finance, "version: 1\nproject: finance\ncheck:\n  external_schemas: [raw_erp]\n")
    assert unresolved(finance) == []


def test_table_matches_schema_alias_case_insensitive(finance: Path) -> None:
    write_skill(finance, "x.md", uses(tables=["FINANCE_MARTS.Fct_Revenue", "finance.dim_calendar"]))
    assert unresolved(finance) == []


def test_table_matches_database_schema_alias(finance: Path) -> None:
    write_skill(finance, "x.md", uses(tables=["MEMORY.finance_marts.fct_revenue"]))
    assert unresolved(finance) == []


def test_unresolved_tables(finance: Path) -> None:
    write_skill(
        finance,
        "x.md",
        uses(tables=["finance_marts.nope", "other.finance_marts.fct_revenue", "fct_revenue"]),
    )
    found = {f.message.split("'")[1] for f in unresolved(finance)}
    assert found == {"finance_marts.nope", "other.finance_marts.fct_revenue", "fct_revenue"}


def test_table_in_external_schema(finance: Path) -> None:
    write_skill(finance, "x.md", uses(tables=["RAW_ERP.invoices", "db.raw_erp.payments"]))
    write_config(finance, "version: 1\nproject: finance\ncheck:\n  external_schemas: [raw_erp]\n")
    assert unresolved(finance) == []


def test_table_of_unpublished_package_model(finance: Path) -> None:
    write_skill(finance, "x.md", uses(tables=["finance.dim_calendar"]))
    write_config(finance, "version: 1\nproject: finance\ninclude_packages: false\n")
    assert len(unresolved(finance)) == 1


def test_unresolved_skipped_without_manifest(tmp_path: Path) -> None:
    root = copy_project(tmp_path, "finance")
    write_skill(root, "x.md", uses(schemas=["nowhere"]))
    assert of(check(root), "skill_unresolved_uses") == []


# --- sampled-values heuristics -----------------------------------------------------

HEURISTIC_EXAMPLES: dict[int, tuple[list[str], list[str]]] = {
    1: (
        ["min: 0", "Order total. MAX = 9999", "mean:-3.2 over the year", "p95: 120ms", "stddev=4"],
        [
            "Maximum order value allowed by policy",
            "The minimum is configured per region",
            "Average basket size, see the dashboard",
        ],
    ),
    2: (
        ["3% null", "12.5 % of rows are blank", "100% distinct", "45% non-null", "99.9% populated"],
        [
            "Percent of revenue recognized",
            "Discount rate as a percentage",
            "Share of orders, 0-100%",
        ],
    ),
    3: (
        ["Distinct values: a, b", "Sample values (3): x", "top values: gold", "Example value = 7"],
        [
            "Unique identifier of the customer",
            "Sample of orders used for QA is excluded",
            "Top level category",
        ],
    ),
    4: (
        ["1,204,331 rows", "about 120 records", "9,999 ROWS loaded"],
        [
            "One row per order",
            "Rows are deduplicated on load",
            "12 rows",
        ],
    ),
    5: (
        ["null rate is low", "Null percentage 2", "NULL count: 4", "null pct"],
        [
            "Null when the order was cancelled",
            "Never null",
            "Nullable foreign key to customers",
        ],
    ),
}


def test_five_heuristics() -> None:
    assert len(SAMPLED_VALUE_PATTERNS) == 5


@pytest.mark.parametrize("index", sorted(HEURISTIC_EXAMPLES))
def test_heuristic_positive_examples(index: int) -> None:
    positives, _ = HEURISTIC_EXAMPLES[index]
    assert len(positives) >= 3
    pattern = SAMPLED_VALUE_PATTERNS[index - 1]
    for text in positives:
        assert pattern.search(text), (index, text)
        assert looks_like_sampled_values(text)


@pytest.mark.parametrize("index", sorted(HEURISTIC_EXAMPLES))
def test_heuristic_negative_examples(index: int) -> None:
    _, negatives = HEURISTIC_EXAMPLES[index]
    assert len(negatives) >= 3
    pattern = SAMPLED_VALUE_PATTERNS[index - 1]
    for text in negatives:
        assert not pattern.search(text), (index, text)
        assert not looks_like_sampled_values(text), text


@pytest.mark.parametrize(
    "text", ["Maximum order value allowed by policy", "Percent of revenue recognized", ""]
)
def test_required_prose_negatives(text: str) -> None:
    assert not looks_like_sampled_values(text)


# --- output formats ----------------------------------------------------------------


def bad_result(tmp_path: Path) -> CheckResult:
    return check(copy_project(tmp_path, "bad"), manifest_path=MANIFESTS / "bad.json")


def test_text_format(tmp_path: Path) -> None:
    result = bad_result(tmp_path)
    text = format_text(result)
    assert "error" in text and "warning" in text
    assert "skill_unresolved_uses" in text
    assert "unresolved.md" in text
    last = text.strip().splitlines()[-1]
    assert "error" in last and "warning" in last


def test_text_format_clean(finance: Path) -> None:
    text = format_text(check(finance))
    assert "0 errors" in text
    assert "skill_without_uses" in text


def test_json_format(tmp_path: Path) -> None:
    data = json.loads(format_json(bad_result(tmp_path)))
    assert data["summary"]["error"] >= 1
    first = data["findings"][0]
    assert set(first) == {"rule", "severity", "message", "file", "line", "node"}


def test_github_format(tmp_path: Path) -> None:
    out = format_github(bad_result(tmp_path))
    lines = out.strip().splitlines()
    assert any(line.startswith("::error ") for line in lines)
    assert any(line.startswith("::warning ") for line in lines)
    unresolved_line = next(line for line in lines if "skill_unresolved_uses" in line)
    assert "file=" in unresolved_line and "line=" in unresolved_line
    assert all(line.startswith("::") for line in lines)


def test_github_format_escapes() -> None:
    finding = Finding(
        rule="config_invalid",
        severity="error",
        message="50% bad\nsecond line",
        file="a,b:c.yml",
        line=2,
        node=None,
    )
    out = format_github(CheckResult(findings=(finding,)))
    assert out.startswith("::error file=a%2Cb%3Ac.yml,line=2,title=config_invalid::")
    assert "50%25 bad%0Asecond line" in out
    assert "\n" not in out.rstrip("\n")


def test_github_info_is_notice() -> None:
    finding = Finding(rule="description_scope_empty", severity="info", message="m")
    assert format_github(CheckResult(findings=(finding,))).startswith("::notice ")


CODE_QUALITY_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "required": ["description", "check_name", "fingerprint", "severity", "location"],
        "properties": {
            "description": {"type": "string", "minLength": 1},
            "check_name": {"type": "string", "minLength": 1},
            "fingerprint": {"type": "string", "minLength": 1},
            "severity": {"enum": ["info", "minor", "major", "critical", "blocker"]},
            "location": {
                "type": "object",
                "required": ["path", "lines"],
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "lines": {
                        "type": "object",
                        "required": ["begin"],
                        "properties": {"begin": {"type": "integer", "minimum": 1}},
                    },
                },
            },
        },
    },
}


def test_gitlab_report_validates(tmp_path: Path) -> None:
    result = bad_result(tmp_path)
    report = gitlab_report(result)
    jsonschema.validate(report, CODE_QUALITY_SCHEMA)
    assert len(report) == len(result.findings)
    fingerprints = [item["fingerprint"] for item in report]
    assert len(set(fingerprints)) == len(fingerprints)
    assert {item["severity"] for item in report} >= {"major", "minor"}


def test_gitlab_report_stable_fingerprints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    reports = []
    for run in ("a", "b"):
        (tmp_path / run).mkdir()
        copy_project(tmp_path / run, "bad")
        monkeypatch.chdir(tmp_path / run)
        reports.append(gitlab_report(check(Path("bad"), manifest_path=MANIFESTS / "bad.json")))
    assert [x["fingerprint"] for x in reports[0]] == [x["fingerprint"] for x in reports[1]]


def test_gitlab_report_without_file() -> None:
    finding = Finding(rule="description_scope_empty", severity="info", message="m")
    (item,) = gitlab_report(CheckResult(findings=(finding,)))
    assert item["location"]["path"]
    assert item["location"]["lines"]["begin"] == 1
    assert item["severity"] == "info"


def test_findings_sorted_deterministically(tmp_path: Path) -> None:
    a = bad_result(tmp_path / "a")
    keys = [(f.file or "", f.line or 0, f.rule, f.node or "", f.message) for f in a.findings]
    assert keys == sorted(keys)


# --- Phase 1 review regressions ------------------------------------------------------


def test_manifest_without_project_name_falls_back_to_dbt_project_yml(finance: Path) -> None:
    edit_manifest(finance, lambda d: d["metadata"].pop("project_name"))
    result = check(finance)
    assert not result.has_errors
    assert rules(result) == {"skill_without_uses"}
    assert result.root_package == "finance"


def test_manifest_without_any_project_name_is_manifest_finding(finance: Path) -> None:
    edit_manifest(finance, lambda d: d["metadata"].pop("project_name"))
    (finance / "dbt_project.yml").unlink()
    result = check(finance)
    assert "manifest_missing" in rules(result)
    assert result.manifest_missing


def test_uppercase_schema_entry_resolves(finance: Path) -> None:
    # The SPEC 8 frontmatter example uses uppercase names; DuckDB schemas are lowercase.
    write_skill(
        finance, "x.md", uses(schemas=["FINANCE_MARTS"], tables=["FINANCE_MARTS.FCT_REVENUE"])
    )
    assert unresolved(finance) == []


def test_uppercase_external_schema_resolves(finance: Path) -> None:
    write_skill(finance, "x.md", uses(schemas=["raw_erp"], tables=["raw_erp.invoices"]))
    write_config(finance, "version: 1\nproject: finance\ncheck:\n  external_schemas: [RAW_ERP]\n")
    assert unresolved(finance) == []


def test_non_utf8_skill_is_finding(finance: Path) -> None:
    path = finance / "agents" / "skills" / "latin1.md"
    path.write_bytes("# caf\xe9\n".encode("latin-1"))
    result = check(finance)
    (f,) = of(result, "skill_frontmatter_invalid")
    assert f.file == str(path)
    assert "UTF-8" in f.message
