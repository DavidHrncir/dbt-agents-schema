"""The committed fixture manifests contain the models the rest of the suite relies on."""

import json
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
MANIFESTS = FIXTURES / "manifests"
PROJECTS = FIXTURES / "projects"

BAD_NAME = "bad_project_with_an_intentionally_long_name_that_breaks_the_project_key_rule"

EXPECTED_MODELS: dict[str, set[str]] = {
    "shared_pkg": {
        "model.shared_pkg.dim_calendar",
        "model.shared_pkg.dim_currency",
    },
    "finance": {
        "model.finance.stg_invoices",
        "model.finance.stg_payments",
        "model.finance.int_invoice_lines",
        "model.finance.fct_revenue",
        "model.finance.fct_payments",
        "model.finance.dim_customers",
        "model.shared_pkg.dim_calendar",
        "model.shared_pkg.dim_currency",
    },
    "finance_v2": {
        "model.finance.stg_invoices",
        "model.finance.stg_payments",
        "model.finance.int_invoice_lines",
        "model.finance.fct_revenue",
        "model.finance.dim_customers",
        "model.shared_pkg.dim_calendar",
        "model.shared_pkg.dim_currency",
    },
    "marketing": {
        "model.marketing.stg_campaigns",
        "model.marketing.stg_ad_spend",
        "model.marketing.fct_campaign_performance",
        "model.marketing.dim_channels",
        "model.marketing.fct_attribution",
        "model.shared_pkg.dim_calendar",
        "model.shared_pkg.dim_currency",
    },
    "bad": {
        f"model.{BAD_NAME}.orders",
        f"model.{BAD_NAME}.customers",
    },
}


def load(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads((MANIFESTS / f"{name}.json").read_text())
    return data


def models(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {k: v for k, v in manifest["nodes"].items() if v["resource_type"] == "model"}


@pytest.mark.parametrize("name", sorted(EXPECTED_MODELS))
def test_manifest_has_expected_models(name: str) -> None:
    assert set(models(load(name))) == EXPECTED_MODELS[name]


@pytest.mark.parametrize("name", sorted(EXPECTED_MODELS))
def test_manifest_has_no_absolute_local_paths(name: str) -> None:
    text = (MANIFESTS / f"{name}.json").read_text()
    assert str(Path.home()) not in text
    assert str(FIXTURES.resolve()) not in text


def test_project_names() -> None:
    assert load("finance")["metadata"]["project_name"] == "finance"
    assert load("finance_v2")["metadata"]["project_name"] == "finance"
    assert load("marketing")["metadata"]["project_name"] == "marketing"
    assert load("bad")["metadata"]["project_name"] == BAD_NAME
    assert len(BAD_NAME) > 63


@pytest.mark.parametrize("name", ["finance", "marketing"])
def test_root_projects_have_sources_seed_public_models_and_descriptions(name: str) -> None:
    manifest = load(name)
    assert any(s["package_name"] == name for s in manifest["sources"].values())
    assert any(
        n["resource_type"] == "seed" and n["package_name"] == name
        for n in manifest["nodes"].values()
    )
    own = [m for m in models(manifest).values() if m["package_name"] == name]
    assert 4 <= len(own) <= 6
    assert sum(m.get("access") == "public" for m in own) >= 2
    assert any(m["description"] for m in own)
    assert any(c["description"] for m in own for c in m["columns"].values())


def test_finance_v2_differences() -> None:
    v1, v2 = models(load("finance")), models(load("finance_v2"))
    assert set(v1) - set(v2) == {"model.finance.fct_payments"}
    rev1 = set(v1["model.finance.fct_revenue"]["columns"])
    rev2 = set(v2["model.finance.fct_revenue"]["columns"])
    assert rev2 - rev1 == {"revenue_eur"}


def test_finance_ephemeral_model() -> None:
    node = models(load("finance"))["model.finance.int_invoice_lines"]
    assert node["config"]["materialized"] == "ephemeral"
    assert node["relation_name"] is None


def test_package_edges() -> None:
    rev = models(load("finance"))["model.finance.fct_revenue"]
    deps = set(rev["depends_on"]["nodes"])
    assert "model.shared_pkg.dim_calendar" in deps
    assert any(d.startswith("seed.finance.") for d in deps)
    stg = models(load("finance"))["model.finance.stg_invoices"]
    assert any(d.startswith("source.finance.") for d in stg["depends_on"]["nodes"])


def skill_files(project: str) -> set[str]:
    root = PROJECTS / project / "agents" / "skills"
    return {p.relative_to(root).as_posix() for p in root.rglob("*.md")}


def test_skills_on_disk() -> None:
    assert skill_files("finance") == {"revenue_overview.md", "payments/reconciliation.md"}
    assert skill_files("finance_v2") == {"revenue_overview.md"}
    assert skill_files("marketing") == {"attribution.md", "campaigns/weekly_review.md"}
    for project in ("finance", "marketing"):
        texts = [
            (PROJECTS / project / "agents" / "skills" / f).read_text() for f in skill_files(project)
        ]
        assert sum("uses:" in t for t in texts) == 1


def test_bad_project_has_no_committed_manifest() -> None:
    # `check --project-dir tests/fixtures/projects/bad` must hit manifest_missing.
    assert not (PROJECTS / "bad" / "target" / "manifest.json").exists()
