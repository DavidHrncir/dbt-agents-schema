"""Manifest reader tests (SPEC 5.2-5.4 and 5.7, T05)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from dbt_agents_schema.manifest import (
    Manifest,
    ManifestError,
    ManifestMissingError,
    load_manifest,
    read_models,
)

MANIFESTS = Path(__file__).parent / "fixtures" / "manifests"

FINANCE_ROOT = {
    "model.finance.stg_invoices",
    "model.finance.stg_payments",
    "model.finance.int_invoice_lines",
    "model.finance.fct_revenue",
    "model.finance.fct_payments",
    "model.finance.dim_customers",
}
SHARED = {"model.shared_pkg.dim_calendar", "model.shared_pkg.dim_currency"}


def finance() -> Manifest:
    return load_manifest(MANIFESTS / "finance.json")


def ids(manifest: Manifest, **kwargs: Any) -> set[str]:
    content = read_models(manifest, project="finance", **kwargs)
    return {m.unique_id for m in content.models}


# --- loading ---------------------------------------------------------------------


def test_missing_manifest_says_run_dbt_parse(tmp_path: Path) -> None:
    path = tmp_path / "target" / "manifest.json"
    with pytest.raises(ManifestMissingError) as exc:
        load_manifest(path)
    assert "run `dbt parse` first" in str(exc.value)
    assert str(path) in str(exc.value)
    assert isinstance(exc.value, ManifestError)


def test_invalid_json_is_manifest_error(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError) as exc:
        load_manifest(path)
    assert not isinstance(exc.value, ManifestMissingError)
    assert str(path) in str(exc.value)


def test_manifest_without_nodes_is_error(tmp_path: Path) -> None:
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps({"metadata": {}}), encoding="utf-8")
    with pytest.raises(ManifestError) as exc:
        load_manifest(path)
    assert "nodes" in str(exc.value)


def test_metadata() -> None:
    m = finance()
    assert m.project_name == "finance"
    assert m.dbt_version == "1.12.7"
    assert isinstance(m.generated_at, datetime)
    assert m.generated_at.tzinfo is not None
    assert m.generated_at.utcoffset() == UTC.utcoffset(None)


# --- package selection -----------------------------------------------------------


def test_root_and_package_models_by_default() -> None:
    assert ids(finance()) == FINANCE_ROOT | SHARED


def test_include_packages_false_keeps_only_root() -> None:
    assert ids(finance(), include_packages=False) == FINANCE_ROOT


def test_exclude_packages() -> None:
    assert ids(finance(), exclude_packages=("shared_pkg",)) == FINANCE_ROOT


def test_root_models_always_included_even_if_excluded() -> None:
    assert ids(finance(), exclude_packages=("finance",), include_packages=False) == FINANCE_ROOT
    assert ids(finance(), exclude_packages=("finance", "shared_pkg")) == FINANCE_ROOT


def test_only_models_are_published() -> None:
    content = read_models(finance(), project="finance")
    assert all(m.unique_id.startswith("model.") for m in content.models)


def test_disabled_nodes_ignored() -> None:
    m = finance()
    assert "model.finance.legacy_revenue" in m.raw["disabled"]
    assert "model.finance.legacy_revenue" not in ids(m)


def test_node_with_enabled_false_in_nodes_is_ignored(tmp_path: Path) -> None:
    raw = json.loads((MANIFESTS / "finance.json").read_text())
    raw["nodes"]["model.finance.fct_payments"]["config"]["enabled"] = False
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    assert "model.finance.fct_payments" not in ids(load_manifest(path))


# --- model records ---------------------------------------------------------------


def model(unique_id: str) -> Any:
    content = read_models(finance(), project="finance")
    return next(m for m in content.models if m.unique_id == unique_id)


def test_model_fields() -> None:
    m = model("model.finance.fct_revenue")
    assert m.project == "finance"
    assert m.name == "fct_revenue"
    assert m.package_name == "finance"
    assert m.database_name == "memory"
    assert m.schema_name == "finance_marts"
    assert m.alias == "fct_revenue"
    assert m.relation_name == '"memory"."finance_marts"."fct_revenue"'
    assert m.materialization == "view"
    assert m.access == "public"
    assert m.description == "Recognized revenue per invoice in US dollars."
    assert m.file_path == "models/marts/fct_revenue.sql"
    assert m.tags == ("finance", "revenue")
    assert m.meta == {"owner": "revenue-team"}


def test_ephemeral_model_has_null_relation_name() -> None:
    m = model("model.finance.int_invoice_lines")
    assert m.materialization == "ephemeral"
    assert m.relation_name is None


def test_top_level_meta_used_when_config_meta_missing(tmp_path: Path) -> None:
    raw = json.loads((MANIFESTS / "finance.json").read_text())
    node = raw["nodes"]["model.finance.dim_customers"]
    node["config"].pop("meta", None)
    node["meta"] = {"tier": "gold"}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    content = read_models(load_manifest(path), project="finance")
    m = next(x for x in content.models if x.name == "dim_customers")
    assert m.meta == {"tier": "gold"}


def test_missing_fields_get_defaults(tmp_path: Path) -> None:
    raw = json.loads((MANIFESTS / "finance.json").read_text())
    node = raw["nodes"]["model.finance.dim_customers"]
    node["description"] = None
    node["alias"] = None
    node.pop("meta", None)
    node["config"].pop("meta", None)
    node.pop("tags", None)
    node.pop("access", None)
    node["config"].pop("access", None)
    for col in node["columns"].values():
        col["description"] = None
        col["data_type"] = None
        col.pop("meta", None)
        col.get("config", {}).pop("meta", None)
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    content = read_models(load_manifest(path), project="finance")
    m = next(x for x in content.models if x.name == "dim_customers")
    assert m.description == ""
    assert m.alias == "dim_customers"
    assert m.meta == {}
    assert m.tags == ()
    assert m.access is None
    cols = [c for c in content.columns if c.model_id == m.unique_id]
    assert cols
    assert all(c.description == "" and c.data_type == "" and c.meta == {} for c in cols)


# --- columns ---------------------------------------------------------------------


def test_columns_for_published_models() -> None:
    content = read_models(finance(), project="finance")
    published = {m.unique_id for m in content.models}
    assert {c.model_id for c in content.columns} <= published
    rev = {c.column_name: c for c in content.columns if c.model_id == "model.finance.fct_revenue"}
    assert set(rev) == {"customer_id", "invoice_date", "revenue_usd"}
    assert rev["invoice_date"].data_type == "date"
    assert rev["invoice_date"].meta == {"grain": "day"}
    assert rev["customer_id"].data_type == ""
    assert rev["customer_id"].description == "Customer identifier."
    assert all(c.project == "finance" for c in content.columns)


def test_columns_of_excluded_packages_dropped() -> None:
    content = read_models(finance(), project="finance", include_packages=False)
    assert not any(c.model_id.startswith("model.shared_pkg.") for c in content.columns)


# --- dependencies ----------------------------------------------------------------


def deps(manifest: Manifest, **kwargs: Any) -> dict[tuple[str, str], Any]:
    content = read_models(manifest, project="finance", **kwargs)
    return {(d.upstream_id, d.downstream_id): d for d in content.dependencies}


def test_dependency_edges() -> None:
    d = deps(finance())
    rev = "model.finance.fct_revenue"
    assert set(k for k in d if k[1] == rev) == {
        ("model.finance.int_invoice_lines", rev),
        ("seed.finance.fx_rates", rev),
        ("model.shared_pkg.dim_calendar", rev),
    }
    assert all(x.downstream_type == "model" for x in d.values())


def test_edges_to_sources_and_seeds_carry_this_project() -> None:
    d = deps(finance())
    src = d[("source.finance.erp.invoices", "model.finance.stg_invoices")]
    assert (src.upstream_type, src.upstream_project) == ("source", "finance")
    seed = d[("seed.finance.fx_rates", "model.finance.fct_revenue")]
    assert (seed.upstream_type, seed.upstream_project) == ("seed", "finance")
    assert src.project == "finance"


def test_edges_to_unpublished_models_kept() -> None:
    d = deps(finance(), include_packages=False)
    edge = d[("model.shared_pkg.dim_calendar", "model.finance.fct_revenue")]
    assert edge.upstream_type == "model"
    assert edge.upstream_project == "finance"  # still a node in this manifest


def test_unknown_upstream_has_null_project_and_type(tmp_path: Path) -> None:
    raw = json.loads((MANIFESTS / "finance.json").read_text())
    raw["nodes"]["model.finance.dim_customers"]["depends_on"]["nodes"] += [
        "model.other_project.thing",
        "weird",
        "model.other_project.thing",
    ]
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    d = deps(load_manifest(path))
    other = d[("model.other_project.thing", "model.finance.dim_customers")]
    assert (other.upstream_type, other.upstream_project) == ("model", None)
    weird = d[("weird", "model.finance.dim_customers")]
    assert (weird.upstream_type, weird.upstream_project) == ("unknown", None)
    # duplicates collapse to one row per primary key
    content = read_models(load_manifest(path), project="finance")
    keys = [(x.upstream_id, x.downstream_id) for x in content.dependencies]
    assert len(keys) == len(set(keys))


# --- ordering --------------------------------------------------------------------


def test_rows_sorted_by_primary_key() -> None:
    content = read_models(finance(), project="finance")
    mk = [(m.project, m.unique_id) for m in content.models]
    ck = [(c.project, c.model_id, c.column_name) for c in content.columns]
    dk = [(d.project, d.upstream_id, d.downstream_id) for d in content.dependencies]
    assert mk == sorted(mk)
    assert ck == sorted(ck)
    assert dk == sorted(dk)


def test_marketing_has_shared_pkg_models() -> None:
    m = load_manifest(MANIFESTS / "marketing.json")
    content = read_models(m, project="marketing")
    assert {x.unique_id for x in content.models} >= SHARED
    assert all(x.project == "marketing" for x in content.models)
