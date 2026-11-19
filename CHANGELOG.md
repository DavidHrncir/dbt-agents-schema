# Changelog

All notable changes to this project are documented here. The project follows
[Semantic Versioning](https://semver.org/). The agents schema contract has its own
integer `spec_version` (docs/SPEC.md section 11).

## 0.2.0 — unreleased

Spec version 1 (unchanged). CI parse and published-context checks.

### Changed

- **The GitHub workflow and GitLab template now parse with dbt Fusion by default.**
  Callers that do not set `dbt-engine` (GitLab: `dbt_engine`) switch from dbt-core to
  dbt Fusion, which copies model `meta` to columns. Set `dbt-engine: core` to restore
  the 0.1 behaviour. The default `dbt-fusion-version` is `2.0.0`; it is installed with
  the official installer (`public.cdn.getdbt.com/fs/install/install.sh`).
- **GitHub: `WAREHOUSE_CREDENTIALS` reaches only the publish steps.** The parse profile
  is no longer built from it. A 0.1 caller must add `parse_target` to the environment
  (recommended) or pass `DBT_PROFILES_YML`; otherwise the profile step fails and says
  so. GitLab still accepts the credentials-based profile.
- Both wrappers run `dbt parse --no-partial-parse`, run `check --env <environment>`, and
  run `publish --dry-run` before every publish.
- The GitHub example caller is one job: `check` on pull requests and `publish` on push
  to `main` or a manual run, filtered by `paths`. Checks of a pull request cancel each
  other; publishes share one queue that is never cancelled.

### Added

- `environments.<name>.parse_target` in `agents_schema.yml`: the production database
  and schema for `dbt parse` in CI.
- `write-parse-profile --env NAME`: a placeholder profile from `parse_target` that needs
  no credential and never reads `WAREHOUSE_CREDENTIALS`.
- `check --env NAME`, and the rules `manifest_target_mismatch` (error; also run by
  `publish --env` before it connects), `manifest_empty` (error) and
  `column_meta_missing` (off).
- `check.measured_values` settings (`min_number`, `allow`, `exempt`, `count_nouns`) and
  the `measured_values_in_context` rule id (warning). **Not enforced yet:** the rule
  reports no findings in this release. The SPEC algorithm flags standard identifiers
  such as "ISO 4217"; the fix is proposed as SPEC CHANGE PROPOSAL I in
  docs/PROGRESS.md.
- GitHub workflow inputs `warehouse`, `dbt-engine`, `dbt-fusion-version`,
  `package-spec`, `parse-env`, and the secret `PACKAGE_REPO_TOKEN`. `dbt-adapter` and
  `package-version` are now optional; a 0.1 caller keeps validating.
- GitLab template inputs `warehouse`, `dbt_engine`, `dbt_fusion_version`,
  `package_spec`.

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
