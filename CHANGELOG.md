# Changelog

All notable changes to this project are documented here. The project follows
[Semantic Versioning](https://semver.org/). The agents schema contract has its own
integer `spec_version` (docs/SPEC.md section 11).

## 0.2.0 — unreleased

### Changed

- GitHub reusable workflow: dbt Fusion is the default engine (`dbt-engine: fusion`),
  pinned to `dbt-fusion-version` (default `2.0.0`). Set `dbt-engine: core` for the v0.1
  behaviour.

## 0.1.0 — 2026-11-18

First release. Spec version 1.

### Added

- `dbt-agents-schema` CLI: `check`, `publish`, `unpublish`, `init`, `fetch-manifest`,
  `write-parse-profile`, `--version`.
- Multi-project agents schema: every `DBT_*` and `SKILL_USE` row carries `project`, and a
  publish replaces only its own project's rows (and its `ROOT` keys). New `DBT_PROJECT`
  table. The dbt Labs `agents_schema` table and column names are kept, and tables it
  created are migrated in place (`ADD COLUMN`; legacy `project IS NULL` rows removed).
- Writers for Snowflake, BigQuery and Databricks, plus DuckDB for local use and tests.
  The swap is transactional on Snowflake, BigQuery and DuckDB, and atomic per table
  (`INSERT INTO ... REPLACE WHERE`) on Databricks.
- Guards: the ownership guard on `repo_url` (exit 4, `--force-takeover`), the spec
  version guard, `--dry-run`.
- `agents_schema.yml` config with a JSON Schema, environments with `env` or
  `dbt_profile` credentials, and schema/database templates.
- Check engine with hard errors and configurable rules, including
  `sampled_values_in_description` and skill `uses` resolution. Output formats: text,
  JSON, GitHub annotations, GitLab Code Quality.
- A built-in analyst skill per warehouse dialect, published as
  `skill/dbt-agents-schema-analyst`.
- CI wrappers: a GitHub reusable workflow, a GitLab CI template, a pre-commit hook, and
  example callers.
- `fetch-manifest` for dbt platform jobs (Administrative API v2).
- Partner guide (`docs/partner-guide.md`) with a config reference generated from the
  schema, and an end-to-end DuckDB demo (`scripts/demo_duckdb.sh`).

### Known deviations awaiting approval

- SPEC CHANGE PROPOSALS A–D in `docs/PROGRESS.md`: the `schema` key for
  `write-parse-profile`, the GitLab File-type credentials variable, the GitLab `mode: auto`
  input, and uppercase Snowflake column names.
