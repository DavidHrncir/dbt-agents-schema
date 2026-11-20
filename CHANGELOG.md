# Changelog

All notable changes to this project are documented here. The project follows
[Semantic Versioning](https://semver.org/). The agents schema contract has its own
integer `spec_version` (dev/SPEC.md section 11).

## Unreleased

### Added

- `scripts/demo_multi_project.sh`: a second DuckDB demo that checks the sharing
  guarantees with the real CLI. A dry run connects to nothing; republishing or
  unpublishing one project leaves another's rows byte-identical (an MD5 fingerprint
  over all six tables); another repository using the same project key is refused with
  exit 4. It exits non-zero if any guarantee fails, and runs in `make verify`.

### Changed

- The partner guide is now the implementation guide: `docs/implementation-guide.md`.
- README and guide rewritten for teams adding an agents schema to their own dbt project.
  The install explains that one package serves every warehouse, and the bracketed name
  only adds that warehouse's driver. "Migrating from the dbt Labs tool" moved to the end
  of the guide, marked optional.

## 0.2.2 — 2026-11-19

Spec version 1 (unchanged). Documentation and repository layout only; no code or
behaviour changes from 0.2.1.

### Added

- Partner guide: "What gets published", covering the six tables, their columns, the
  `ROOT` keys, and example queries.

### Changed

- README rewritten for users: what it does, quick start, documentation, local demo.
- Partner guide: Install leads with the git install, since the package is not on PyPI.
- The build kit (spec, plan, build log, goals, headless runners) moved from `docs/` and
  `scripts/` to `dev/`. None of it is needed to use the tool. The spec's path is now
  `dev/SPEC.md`.
- Example callers and the partner guide pin `v0.2.2`.

## 0.2.1 — 2026-11-19

Spec version 1 (unchanged). No code or behaviour changes from 0.2.0.

### Added

- MIT `LICENSE`, declared in the package metadata.
- README notice: this is a personal, temporary project, not an official Fivetran or
  dbt Labs product, and it may be removed once an official replacement exists.

### Changed

- Partner guide: removed the stale "rule does not report findings yet" note for
  `measured_values_in_context`, and replaced an `allow` example that is now built in.
- Example callers and the partner guide pin `v0.2.1`.

## 0.2.0 — 2026-11-18

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
  the `measured_values_in_context` rule (warning): flags row counts, ratios and other
  measured values in model and column descriptions and `meta`, and in skill files.
  Standard identifiers such as "ISO 4217" or "RFC 3339" are allowed built in.
- GitHub workflow inputs `warehouse`, `dbt-engine`, `dbt-fusion-version`,
  `package-spec`, `parse-env`, and the secret `PACKAGE_REPO_TOKEN`. `dbt-adapter` and
  `package-version` are now optional; a 0.1 caller keeps validating.
- GitLab template inputs `warehouse`, `dbt_engine`, `dbt_fusion_version`,
  `package_spec`.

### Fixed

- `write-parse-profile --env` for DuckDB honours `parse_target.database`: the profile
  path becomes `<database>.duckdb`, so the parsed catalog name matches and
  `manifest_target_mismatch` passes.
- `check --env` with an invalid `agents_schema.yml` reports the `config_invalid`
  findings instead of "environment is not defined".

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

- SPEC CHANGE PROPOSALS A–D in `dev/PROGRESS.md`: the `schema` key for
  `write-parse-profile`, the GitLab File-type credentials variable, the GitLab `mode: auto`
  input, and uppercase Snowflake column names.
