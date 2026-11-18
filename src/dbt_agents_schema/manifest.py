"""Read a dbt `manifest.json` into typed records (SPEC 5.2-5.4 and 5.7).

Pure logic: reads one file and returns frozen records; never touches a warehouse.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


class ManifestError(Exception):
    """The manifest cannot be used. Maps to exit code 2."""

    def __init__(self, path: Path, message: str) -> None:
        self.path = path
        super().__init__(f"{path}: {message}")


class ManifestMissingError(ManifestError):
    """No manifest at the expected path (check rule `manifest_missing`)."""

    def __init__(self, path: Path) -> None:
        super().__init__(
            path,
            "manifest not found; run `dbt parse` first (or pass --parse, or point "
            "--manifest at an existing manifest.json)",
        )


@dataclass(frozen=True)
class Manifest:
    path: Path
    raw: Mapping[str, Any]
    project_name: str | None
    dbt_version: str | None
    generated_at: datetime | None


@dataclass(frozen=True)
class ModelRecord:
    """One `DBT_MODEL` row (SPEC 5.2)."""

    project: str
    unique_id: str
    name: str
    package_name: str
    database_name: str | None
    schema_name: str | None
    alias: str
    relation_name: str | None
    materialization: str | None
    access: str | None
    description: str
    file_path: str | None
    tags: tuple[str, ...]
    meta: Mapping[str, Any]


@dataclass(frozen=True)
class ColumnRecord:
    """One `DBT_COLUMN` row (SPEC 5.3)."""

    project: str
    model_id: str
    column_name: str
    data_type: str
    description: str
    meta: Mapping[str, Any]


@dataclass(frozen=True)
class DependencyRecord:
    """One `DBT_DEPENDENCY` row (SPEC 5.4)."""

    project: str
    upstream_id: str
    downstream_id: str
    upstream_type: str
    downstream_type: str
    upstream_project: str | None


@dataclass(frozen=True)
class ManifestContent:
    """Published content of one manifest, each tuple sorted by primary key."""

    models: tuple[ModelRecord, ...]
    columns: tuple[ColumnRecord, ...]
    dependencies: tuple[DependencyRecord, ...]


def load_manifest(path: Path) -> Manifest:
    """Load and minimally validate `manifest.json`."""
    if not path.is_file():
        raise ManifestMissingError(path)
    try:
        raw: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError(
            path, f"cannot read manifest as JSON ({exc}); re-run `dbt parse` to regenerate it"
        ) from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("nodes"), dict):
        raise ManifestError(
            path, "not a dbt manifest: missing a `nodes` mapping; re-run `dbt parse`"
        )
    metadata = raw.get("metadata")
    metadata = metadata if isinstance(metadata, dict) else {}
    return Manifest(
        path=path,
        raw=raw,
        project_name=_str_or_none(metadata.get("project_name")),
        dbt_version=_str_or_none(metadata.get("dbt_version")),
        generated_at=_parse_timestamp(path, metadata.get("generated_at")),
    )


def read_models(
    manifest: Manifest,
    *,
    project: str,
    include_packages: bool = True,
    exclude_packages: Iterable[str] = (),
    root_package: str | None = None,
) -> ManifestContent:
    """Select published models (SPEC 5.7) and build model, column and dependency records.

    `root_package` defaults to `metadata.project_name`; root models are always included.
    """
    root = root_package if root_package is not None else manifest.project_name
    if root is None:
        raise ManifestError(
            manifest.path,
            "manifest has no metadata.project_name, so the root package is unknown; "
            "re-run `dbt parse` with a current dbt version",
        )
    excluded = frozenset(exclude_packages)
    nodes: Mapping[str, Any] = manifest.raw["nodes"]
    sources = manifest.raw.get("sources")
    known_ids = set(nodes) | (set(sources) if isinstance(sources, dict) else set())

    models: list[ModelRecord] = []
    columns: dict[tuple[str, str], ColumnRecord] = {}
    deps: dict[tuple[str, str], DependencyRecord] = {}
    for unique_id, node in nodes.items():
        if not isinstance(node, dict) or node.get("resource_type") != "model":
            continue
        config = _mapping(node.get("config"))
        if config.get("enabled") is False:
            continue
        package = str(node.get("package_name") or "")
        if package != root and (not include_packages or package in excluded):
            continue

        models.append(_model(project, unique_id, node, config))
        for col_name, col in (node.get("columns") or {}).items():
            if isinstance(col, dict):
                name = str(col.get("name") or col_name)
                columns.setdefault((unique_id, name), _column(project, unique_id, name, col))
        depends_on = node.get("depends_on") or {}
        for upstream_id in depends_on.get("nodes") or ():
            upstream = str(upstream_id)
            deps[(upstream, unique_id)] = DependencyRecord(
                project=project,
                upstream_id=upstream,
                downstream_id=unique_id,
                upstream_type=upstream.split(".", 1)[0] if "." in upstream else "unknown",
                downstream_type="model",
                upstream_project=project if upstream in known_ids else None,
            )

    return ManifestContent(
        models=tuple(sorted(models, key=lambda m: (m.project, m.unique_id))),
        columns=tuple(
            sorted(columns.values(), key=lambda c: (c.project, c.model_id, c.column_name))
        ),
        dependencies=tuple(
            sorted(deps.values(), key=lambda d: (d.project, d.upstream_id, d.downstream_id))
        ),
    )


# --- internals ---------------------------------------------------------------------


def _model(
    project: str, unique_id: str, node: Mapping[str, Any], config: Mapping[str, Any]
) -> ModelRecord:
    name = str(node.get("name") or "")
    access = node.get("access") or config.get("access")
    tags = node.get("tags")
    return ModelRecord(
        project=project,
        unique_id=unique_id,
        name=name,
        package_name=str(node.get("package_name") or ""),
        database_name=_str_or_none(node.get("database")),
        schema_name=_str_or_none(node.get("schema")),
        alias=str(node.get("alias") or name),
        relation_name=_str_or_none(node.get("relation_name")),
        materialization=_str_or_none(config.get("materialized")),
        access=_str_or_none(access),
        description=str(node.get("description") or ""),
        file_path=_str_or_none(node.get("original_file_path")),
        tags=tuple(str(t) for t in tags) if isinstance(tags, list) else (),
        meta=_meta(node, config),
    )


def _column(project: str, model_id: str, name: str, col: Mapping[str, Any]) -> ColumnRecord:
    config = _mapping(col.get("config"))
    return ColumnRecord(
        project=project,
        model_id=model_id,
        column_name=name,
        data_type=str(col.get("data_type") or ""),
        description=str(col.get("description") or ""),
        meta=_meta(col, config),
    )


def _meta(obj: Mapping[str, Any], config: Mapping[str, Any]) -> Mapping[str, Any]:
    """`config.meta`, else `meta`, else `{}` (an empty mapping counts as absent)."""
    for candidate in (config.get("meta"), obj.get("meta")):
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, dict) else {}


def _str_or_none(value: Any) -> str | None:
    return None if value is None or value == "" else str(value)


def _parse_timestamp(path: Path, value: Any) -> datetime | None:
    if value is None or value == "":
        return None
    try:
        parsed = datetime.fromisoformat(str(value))
    except ValueError as exc:
        raise ManifestError(
            path, f"metadata.generated_at {value!r} is not an ISO timestamp; re-run `dbt parse`"
        ) from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
