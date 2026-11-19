# Progress log

Append one entry per task, newest last. Humans read this between phases.

Entry format:

```
## T<NN> <title> — done | blocked
- Changed: <files or modules>
- Evidence: <make verify summary line>; tests: <names>
- Decisions: <anything not dictated by SPEC, or "none">
```

Blocked tasks and spec proposals use these headings so they are easy to find:

```
## T<NN> <title> — blocked
- Tried: <attempts>
- Needs: <what a human must decide or provide>

## SPEC CHANGE PROPOSAL (T<NN>)
- Problem: <what in SPEC.md is wrong, ambiguous, or impossible>
- Options: <A / B>
- Recommendation: <one>
```

---

## T01 Scaffold the package — done
- Changed: pyproject.toml (hatchling, extras snowflake/bigquery/databricks/duckdb, dev group), uv.lock, Makefile, .github/workflows/ci.yml, src/dbt_agents_schema/{__init__,__main__,cli}.py, py.typed, tests/test_cli.py
- Evidence: `make install && make verify` exit 0 (`3 passed`, mypy "no issues found in 3 source files", check-jsonschema "ok -- validation done"); `uv run dbt-agents-schema --version` → `0.1.0`; tests: test_version_flag_prints_version, test_version_constant, test_version_via_module
- Decisions: console script points at `cli:main_entry` (wraps `main(argv) -> int` with `sys.exit`) so CLI tests can call `main(argv)` in-process. `validate-ci` uses Make `$(wildcard ...)` over `.github/workflows/*.yml`, `examples/github/*.yml`, `templates/gitlab/*.yml`, `examples/gitlab/.gitlab-ci.yml` so absent files are skipped; actionlint runs only when installed. Added `python -m dbt_agents_schema` entry point.

## T02 Fixture dbt projects — done
- Changed: tests/fixtures/projects/{shared_pkg,finance,finance_v2,marketing,bad}, tests/fixtures/profiles.yml, tests/fixtures/manifests/*.json (5, built with dbt-core 1.12.7 + dbt-duckdb 1.11.1), scripts/build_fixtures.sh, scripts/scrub_manifest.py, tests/test_fixtures.py
- Evidence: `make fixtures` regenerated all five manifests; `make verify` exit 0 (`21 passed`); `import dbt` fails in the dev env (ModuleNotFoundError), so the suite runs without dbt; tests: test_manifest_has_expected_models[*], test_manifest_has_no_absolute_local_paths[*], test_project_names, test_root_projects_have_sources_seed_public_models_and_descriptions[*], test_finance_v2_differences, test_finance_ephemeral_model, test_package_edges, test_skills_on_disk, test_bad_project_has_no_committed_manifest
- Decisions:
  - The build script parses copies of the projects in a temp dir so the fixture tree never gets target/, dbt_packages/ or package-lock.yml. scrub_manifest.py swaps local absolute paths for placeholders and empties `metadata.env` so manifests can be committed.
  - finance_v2 keeps dbt project name `finance` (publishes as the same project). It drops `fct_payments` and the `payments/reconciliation` skill (the skill with `uses`, which points at `FCT_PAYMENTS`, so T13 can see its SKILL_USE rows disappear) and adds `fct_revenue.revenue_eur`. finance also has an ephemeral model (`int_invoice_lines`) for T05.
  - Profiles: one per project (`shared_pkg`, `finance`, `marketing`, `bad`), target schema = project name. Models use `+schema: staging|marts`, so relation schemas are e.g. `finance_marts`.
  - `bad` cannot trigger mutually exclusive rules in one run. `config_missing` comes from a nonexistent `--config`; `config_invalid` from `agents_schema.invalid.yml` / `agents_schema.unparseable.yml`; `manifest_missing` from having no `target/manifest.json`; `project_key_invalid` from a dbt project name longer than 63 chars. The mapping is in tests/fixtures/projects/bad/README.md.
  - `skill_key_duplicate`: case-insensitive filesystems (macOS default) cannot hold two files whose paths differ only by case, so the fixture uses `reports/daily.md` plus a root file literally named `Reports\Daily.md`. That relies on T06 treating `\` as a path separator (its Windows-separator test needs that anyway).

## T03 Phase 0 review — done
- Reviewer: Phase 0 review: 0 blockers, 3 should-fix, 4 nits
- Fixed (should-fix):
  - No fixture had disabled nodes, so T05 could not prove "disabled nodes ignored". Added `legacy_revenue` (`enabled=false`) to finance and finance_v2.
  - Skill `uses.schemas` were uppercase (`FINANCE_MARTS`) while DuckDB schemas are lowercase. SPEC 8 makes only table matching case-insensitive, so the clean fixtures could have hit `skill_unresolved_uses`. Lowercased the schema entries; table entries stay mixed-case to exercise the case-insensitive table match.
  - Removed the committed `bad/agents/skills/Reports\Daily.md`, which cannot be checked out on Windows; this reverses the T02 decision. T06/T08 tests create the duplicate in a `tmp_path` copy of `bad` (README updated).
- Fixed (nits): fixture profiles use `path: "{{ env_var('FIXTURE_DUCKDB_PATH', ':memory:') }}"` so T14 can point dbt_profile at a file. finance `fct_revenue` has `config.meta`, `tags` and column `data_type`/`meta`; `dim_customers` has top-level `meta`. dbt 1.12 rejects a model that has both top-level `meta` and `config.meta`, so they are on different models.
- Deferred nits: no structural test of `bad`'s rule triggers (T08's "bad triggers every rule" covers it). Whether an explicit `--config` pointing at a missing file means `config_missing` or a config error is for T04 to decide.
- Changed: tests/fixtures/projects/{finance,finance_v2,marketing,bad}, tests/fixtures/profiles.yml, tests/fixtures/manifests/*.json (regenerated), tests/test_fixtures.py
- Evidence: `make verify` exit 0 (`26 passed`); new tests: test_finance_has_disabled_model, test_finance_meta_tags_and_data_types, test_clean_fixture_skill_uses_resolve[finance|finance_v2|marketing]
- Decisions: in fixtures, `uses.schemas` match model schemas exactly (case-sensitive), which is the literal reading of SPEC 8.

## T04 Config loader — done
- Changed: src/dbt_agents_schema/config.py, src/dbt_agents_schema/schemas/agents_schema.schema.json, tests/test_config.py, pyproject.toml + uv.lock (dev: types-jsonschema for mypy --strict)
- Evidence: `make verify` exit 0 (`93 passed`); tests: test_defaults_with_no_file, test_explicit_missing_config_path_means_defaults, test_partial_rules_merge_with_defaults, test_yaml_off_is_a_severity_not_false, test_version_required, test_version_only_1, test_unknown_top_level_key, test_unknown_nested_keys, test_environment_name_pattern, test_environment_name_too_long, test_credentials_enum, test_description_scope_enum, test_rule_severity_enum, test_types_checked, test_template_target_schema, test_template_target_database, test_template_project, test_template_env, test_unknown_template_variables_are_config_errors[*], test_target_vars_with_env_credentials_are_errors[*], test_snowflake_uppercases_rendered_values, test_other_warehouses_keep_case[*], test_project_key_invalid[*]
- Decisions:
  - An explicit `--config` path that does not exist means defaults with `found=False` (so `check` reports `config_missing`), same as an absent default file. This settles the item T03 deferred.
  - YAML is loaded so that only `true`/`false` are booleans; bare `off` stays the string severity `"off"`.
  - `load_config` does not enforce the project key pattern; `validate_project_key` returns the message so `check` can report `project_key_invalid` and publish can fail on it.
  - Templates are validated at load time (unknown variables, `target_*` with `credentials: env`, stray `{{`/`{%`); rendering happens in `resolve_target`, where a missing profile value is a config error.
  - `environments` in the file replaces the default prod/dev set; it is not merged with it.

## T05 Manifest reader — done
- Changed: src/dbt_agents_schema/manifest.py, tests/test_manifest.py
- Evidence: `make verify` exit 0 (`116 passed`); tests: test_missing_manifest_says_run_dbt_parse, test_invalid_json_is_manifest_error, test_root_and_package_models_by_default, test_include_packages_false_keeps_only_root, test_exclude_packages, test_root_models_always_included_even_if_excluded, test_disabled_nodes_ignored, test_node_with_enabled_false_in_nodes_is_ignored, test_ephemeral_model_has_null_relation_name, test_edges_to_sources_and_seeds_carry_this_project, test_edges_to_unpublished_models_kept, test_unknown_upstream_has_null_project_and_type, test_rows_sorted_by_primary_key, test_model_fields, test_columns_for_published_models, test_missing_fields_get_defaults
- Decisions:
  - Root package = `metadata.project_name` (overridable via `root_package`); `exclude_packages` never removes root models.
  - Besides ignoring the `disabled` section, a `nodes` entry with `config.enabled: false` is also skipped.
  - `meta`: an empty `config.meta` counts as absent, so a non-empty top-level `meta` is used.
  - `access` falls back to `config.access` when the node-level `access` is missing.
  - `upstream_project` = this project when the upstream id is a key in `nodes` or `sources` (package models included); duplicate `depends_on` entries collapse to one row per PK.
  - `generated_at` is parsed to an aware UTC datetime; a naive value is taken as UTC.
  - `ManifestMissingError` (subclass of `ManifestError`) carries the "run `dbt parse` first" message for check's `manifest_missing` and exit 2.

## T06 Skills loader — done
- Changed: src/dbt_agents_schema/skills.py, tests/test_skills.py
- Evidence: `make verify` exit 0 (`146 passed`); tests: test_nested_folders, test_skill_key_windows_separators[*], test_windows_separator_in_file_name, test_no_frontmatter, test_invalid_yaml_frontmatter_is_finding, test_unclosed_frontmatter_is_finding, test_malformed_uses_is_finding_not_crash[*], test_duplicate_keys_differing_only_by_case, test_bad_fixture_duplicate_in_tmp_copy, test_bad_fixture_broken_frontmatter, test_finance_fixture_keys_and_uses, test_crlf_frontmatter, test_deterministic_order
- Decisions:
  - Malformed `uses` (not a mapping, unknown keys, values not lists of strings), non-mapping frontmatter, and a `---` block with no closing line are all reported as `skill_frontmatter_invalid` (hard error). SPEC 8 only names "not valid YAML"; these shapes are the same kind of problem, and a hard error stops a skill from silently publishing without its `uses`.
  - A skill with invalid frontmatter is still loaded (with no `uses`), so other checks and the key-duplicate check still see it.
  - `uses: null`, `uses: {}` and empty lists count as "no uses" (`skill_without_uses` applies, no finding here). Entries are stripped; empty strings are dropped.
  - Files match `*.md` case-insensitively. Keys keep their on-disk case. On a case-insensitive key collision, the first file in POSIX-path order is kept and each later one gets a `skill_key_duplicate` finding naming both files.
  - `content` is the full file text (BOM stripped), frontmatter included, for the ROOT row.

## T07 Plan builder — done
- Changed: src/dbt_agents_schema/plan.py, src/dbt_agents_schema/skills/{snowflake,bigquery,databricks,duckdb}.md (placeholders), tests/test_plan.py, tests/golden/{finance,marketing}.json
- Evidence: `make verify` exit 0 (`186 passed`); tests: test_golden[finance], test_golden[marketing], test_plan_identical_across_runs, test_project_row_counts_match_other_tables, test_rows_sorted_and_unique_by_primary_key[*], test_rows_have_exact_columns[*], test_all_root_rows_present, test_shared_root_rows_identical_across_projects, test_project_overview_content, test_skill_use_rows, test_finance_v2_drops_skill_use, test_analyst_skill_matches_warehouse[*]
- Decisions:
  - `published_at` comes in through `RunMetadata` (aware datetime, UTC) and is written to both the `DBT_PROJECT` row and the `project/<p>` overview, which keeps the plan a pure, repeatable function. The publish command (T14) takes it from the clock right before the writer runs. SPEC 5.1 says "set by the writer"; supplying it at publish time meets that, and the overview text matches the row.
  - The plan is warehouse-aware only through the analyst skill body (`RunMetadata.warehouse`). The bodies in `src/dbt_agents_schema/skills/*.md` are placeholders; T21 writes the real SPEC 9 content and regenerates the goldens with `UPDATE_GOLDEN=1`.
  - Rows are plain dicts in `TABLE_COLUMNS` order; JSON columns (`tags`, `meta`) stay as Python lists and dicts, and writers serialize them. `TABLE_COLUMNS`, `PRIMARY_KEYS`, `SHARED_ROOT_KEYS` and `ANALYST_SKILL_KEY` live in plan.py so writers share one definition.
  - `skill_count` counts the project's skill files (not the built-in analyst skill). Unknown overview values render as `unknown`.
  - The overview's example query puts the project key into the markdown text as a literal (it matches `^[a-z][a-z0-9_]*$`); this is content only and is never executed by the tool.

## T08 Check engine — done
- Changed: src/dbt_agents_schema/check.py, src/dbt_agents_schema/config.py (`default_config`), tests/test_check.py
- Evidence: `make verify` exit 0 (`256 passed`); tests: test_bad_fixture_triggers_every_rule, test_bad_default_run, test_hard_error_rules, test_config_invalid_reports_json_path, test_manifest_missing, test_manifest_unreadable_is_manifest_finding, test_project_key_invalid, test_skill_key_duplicate, test_skill_frontmatter_invalid, test_config_missing(+_negative), test_missing_model_description_in_public_scope / _outside_public_scope_ignored / _scope_all / _negative, test_missing_column_description(+_out_of_scope, _negative), test_sampled_values_in_model_description / _column_description / _negative, test_unresolved_schema, test_resolved_schema, test_schema_in_external_schemas, test_table_matches_schema_alias_case_insensitive, test_table_matches_database_schema_alias, test_unresolved_tables, test_table_in_external_schema, test_public_scope_with_no_public_models_emits_info, test_heuristic_positive_examples[1-5], test_heuristic_negative_examples[1-5], test_required_prose_negatives, test_text_format, test_json_format, test_github_format, test_github_format_escapes, test_gitlab_report_validates
- Decisions:
  - `check_project(project_dir, config_path=, manifest_path=)` returns a `CheckResult` with the findings plus the loaded config, manifest and skills, so publish can reuse them.
  - An unreadable or invalid manifest is reported under `manifest_missing` with the specific reason (`CheckResult.manifest_missing` is set either way). No separate rule id, because SPEC 8 fixes the list.
  - After `config_invalid`, checking continues with `default_config` so skill problems still surface; `config_missing` is not added in that case.
  - `sampled_values_in_description` applies to every published model and its columns, not only `description_scope`. SPEC 8 limits only the two `missing_*` rules to the scope, and any published description reaches the warehouse.
  - The SPEC 3 info line uses rule id `description_scope_empty`, severity `info`. It fires when the scope is `public` and no published model has `access: public`. dbt writes `protected` by default, so "sets no access anywhere" cannot be told apart from explicit `protected`. Info findings never affect exit codes and are not configurable.
  - `skill_unresolved_uses`: `uses.schemas` must match a published model schema or an `external_schemas` entry exactly (as T03 decided). `uses.tables` is matched case-insensitively against `schema.alias` and `database.schema.alias`; the schema part of a table entry is compared to `external_schemas` case-insensitively. The check is skipped when there is no manifest. Skills with invalid frontmatter get no extra `skill_without_uses`.
  - Model findings point at the properties YAML (`patch_path`), else the SQL file, under `dbt_packages/<pkg>/` for package models. Unresolved-uses findings carry the frontmatter line of the entry.
  - GitLab severities: error→major, warning→minor, info→info. The fingerprint is the sha256 of rule, file, node, line and message. Findings with no file use `agents_schema.yml` as the path and line 1. GitHub annotations: info→`::notice`; data and properties are escaped per the workflow-command rules.

## T09 `check` command — done
- Changed: src/dbt_agents_schema/cli.py, tests/test_cli_check.py
- Evidence: `make verify` exit 0 (`273 passed`); tests: test_clean_finance_exits_0_with_warnings, test_bad_exits_1, test_missing_manifest_exits_2, test_parse_runs_dbt_parse, test_parse_failure_exits_2, test_parse_without_dbt_installed_exits_2, test_no_parse_never_calls_subprocess, test_format_json, test_format_github, test_format_gitlab_writes_default_report_and_prints_text, test_format_gitlab_output_path, test_output_for_json, test_gitlab_report_written_even_when_manifest_missing, test_config_flag, test_default_project_dir_is_cwd
- Decisions:
  - Exit codes for `check`: 2 when the manifest is missing or unreadable (findings are still printed, and the "run `dbt parse` first" message goes to stderr); otherwise 1 on any error finding and 0 if there are none. `config_invalid` exits 1 under `check` because it is a check finding (SPEC 7: "Exit 1 on any error finding"). `publish` will exit 2 for config errors (SPEC 6).
  - `--parse` runs `dbt parse` with `cwd=<project-dir>` (no shell). A non-zero exit, or `dbt` missing from PATH, exits 2 with a fix hint.
  - With `--format gitlab`, `--output` is the Code Quality report path (default `gl-code-quality-report.json`, relative to cwd) and text goes to stdout. With text, json or github, `--output` writes the rendered output to that file instead of stdout.
  - Common options (`--project-dir`, `--config`, `--manifest`) live on each subcommand through a shared parent parser. Finding paths keep the `--project-dir` form as given, so running from the repo root yields repo-relative paths for annotations.
  - Running with no command still prints help and exits 0, as in T01.

## T10 Phase 1 review — done
- Reviewer: Phase 1 review: 0 blockers, 3 should-fix, 3 nits
- Fixed (should-fix):
  - A manifest without `metadata.project_name` crashed `check` with a traceback. The root package now falls back to the `dbt_project.yml` name (`config.dbt_project_name`, now public). `check_project` catches `ManifestError` from `read_models` and reports `manifest_missing` (exit 2). `CheckResult.root_package` carries the value, and `build_plan(..., root_package=)` accepts it for publish.
  - `uses.schemas` and `external_schemas` are now compared case-insensitively, like `uses.tables`. The SPEC 8 frontmatter example (`schemas: [FINANCE_MARTS]`) failed against lowercase DuckDB schemas. This supersedes the case-sensitive schema decision in T03/T08.
  - A skill file that cannot be read (not UTF-8, or an OS error) is now a `skill_frontmatter_invalid` finding naming the file and the fix, not an uncaught exception.
- Fixed (nits): `RunMetadata.published_at` is normalized to UTC. `DBT_COLUMN` records are deduplicated by primary key. `test_gitlab_report_stable_fingerprints` now asserts fingerprint equality across two checkouts that use the same relative `--project-dir`.
- Changed: src/dbt_agents_schema/{config,manifest,skills,plan,check}.py, tests/test_{check,skills,manifest,plan}.py
- Evidence: `make verify` exit 0 (`282 passed`); new tests: test_manifest_without_project_name_falls_back_to_dbt_project_yml, test_manifest_without_any_project_name_is_manifest_finding, test_uppercase_schema_entry_resolves, test_uppercase_external_schema_resolves, test_non_utf8_skill_is_finding, test_non_utf8_file_is_finding_not_crash, test_duplicate_column_names_collapse, test_root_package_override_when_metadata_lacks_name, test_published_at_normalized_to_utc; strengthened: test_gitlab_report_stable_fingerprints
- Decisions: none beyond the fixes above.

## T11 Writer interface — done
- Changed: src/dbt_agents_schema/writers/__init__.py (new), src/dbt_agents_schema/dialects/{__init__,snowflake,bigquery,databricks,duckdb}.py (new), tests/fakes.py (new), tests/test_writer_interface.py (new)
- Evidence: `make verify` exit 0 (`311 passed`); tests: test_fake_satisfies_protocol, test_protocol_methods_documented, test_fake_publish_two_projects_and_unpublish, test_fake_swap_replaces_only_own_rows_and_counts_legacy, test_swap_result_totals, test_root_scope_publish_owns_shared_and_project_keys, test_root_scope_unpublish_leaves_shared_keys, test_root_scope_like_pattern_escapes_wildcards, test_one_dialect_module_per_warehouse, test_unknown_dialect_names_choices, test_quote_identifier[*], test_quote_rejects_empty_and_nul, test_qualified_name_skips_missing_parts, test_type_map_matches_spec[*], test_every_column_has_a_logical_type, test_column_ddl_types, test_import_extra_names_the_extra, test_import_extra_returns_module
- Decisions:
  - `Writer` is a `runtime_checkable` Protocol in `dbt_agents_schema.writers`: `ensure_structure() -> EnsureResult` (columns added per table), `read_project_row(project) -> Mapping | None`, `max_spec_version() -> int | None`, `swap(plan) -> SwapResult` (rows written and legacy rows removed per table), `unpublish(project) -> UnpublishResult`, `close()`. A writer is built for one already-resolved database and schema. Failures raise `WarehouseError` (exit 3). A missing client library raises `MissingExtraError` naming the extra (exit 2).
  - `RootScope` is the one definition of which ROOT keys an operation owns. Publish owns the six shared keys, the analyst skill key, `project/<p>` and the `skill/<p>/` prefix. Unpublish owns only `project/<p>` and `skill/<p>/`, because shared rows still serve the other projects. Project keys may contain `_`, a LIKE wildcard, so `skill_like_pattern(escape)` escapes it; SQL writers must use that pattern or a prefix function.
  - Dialects: `dialects/<warehouse>.py` each define `DIALECT` (quoting helper + SPEC 5 type map). `LOGICAL_TYPES` gives every column's logical type in one place. Snowflake quoting keeps case as given; the uppercasing rule is left to T16.
  - `tests/fakes.py` has an in-memory `FakeWriter` and a `fixture_plan()` helper for later CLI tests.

## T12 DuckDB writer — done
- Changed: src/dbt_agents_schema/writers/duckdb.py (new), tests/test_duckdb_writer.py (new), tests/fakes.py (`DBT_LABS_TABLES`, `create_dbt_labs_tables`)
- Evidence: `make verify` exit 0 (`335 passed`); tests: test_ensure_structure_creates_schema_and_tables, test_ensure_structure_is_idempotent, test_ensure_structure_migrates_dbt_labs_tables, test_ensure_structure_keeps_extra_columns, test_ensure_structure_matches_existing_names_case_insensitively, test_explicit_database, test_swap_writes_every_plan_row, test_swap_round_trips_values, test_swap_replaces_own_rows_only, test_swap_twice_is_stable, test_swap_removes_legacy_rows_and_counts_them, test_swap_root_prefix_is_literal, test_swap_rolls_back_when_insert_fails, test_swap_rolls_back_on_non_database_error, test_values_are_bound_not_interpolated, test_read_project_row_missing, test_max_spec_version, test_reads_before_structure_exists, test_unpublish_removes_only_that_project, test_unpublish_without_tables_is_a_no_op, test_database_errors_become_warehouse_errors, test_connect_from_path_and_close_twice, test_connect_bad_path_is_warehouse_error
- Decisions:
  - No primary keys are declared on DuckDB. DuckDB enforces them (they are not informational), and migrated dbt Labs tables could not get them anyway. Uniqueness comes from the plan (SPEC 5).
  - Existing tables and columns are matched case-insensitively through `information_schema`, because DuckDB identifiers are case-insensitive. Extra columns in existing tables are left alone (never dropped).
  - Swap runs inside `BEGIN TRANSACTION … COMMIT`. Every exception, database or not, triggers `ROLLBACK` and is re-raised; DuckDB errors become `WarehouseError` (exit 3) naming the action and schema. Legacy `project IS NULL` rows are counted before they are deleted, returned per table, and logged.
  - ROOT deletes use `starts_with(key, ?)` for the `skill/<p>/` prefix, so `_` in a project key is never a wildcard. Only `provider = 'dbt'` rows are touched.
  - JSON columns are bound as `json.dumps` text. Timestamps are bound as aware datetimes into TIMESTAMPTZ. A connection the writer opens itself (`DuckDBWriter.connect`) runs `SET TimeZone = 'UTC'`.
  - `read_project_row`, `max_spec_version` and `unpublish` return None or 0 when the tables do not exist yet, so the guards and `unpublish` never create structure.
  - The dbt Labs table shape used in tests (`tests/fakes.py::DBT_LABS_TABLES`) is the SPEC 5 tables minus every column marked "new", with no DBT_PROJECT table.

## T13 Acceptance test: many projects, one schema — done
- Changed: tests/acceptance/test_multi_project.py (new), src/dbt_agents_schema/publish.py (new), pyproject.toml (`pythonpath = ["tests"]` so acceptance tests can import `fakes`)
- Evidence: `make verify` exit 0 (`341 passed`); `uv run pytest tests/acceptance/test_multi_project.py -v`: test_1_two_projects_share_one_schema PASSED, test_2_republish_one_project_leaves_the_other_untouched PASSED, test_3_ownership_guard PASSED (exit 4, then 0 with force), test_4_migrates_dbt_labs_tables PASSED ("legacy rows removed: 8"), test_5_newer_spec_version_refuses PASSED (exit 2), test_6_unpublish_removes_only_that_project PASSED — 6 passed
- Decisions:
  - The scenarios need publish exit codes before the CLI exists (T14). So the orchestration lives in `dbt_agents_schema.publish`: `publish(project_dir, env=, warehouse=, writer_factory=, git=, out=, err=, …) -> PublishOutcome` and `unpublish(...) -> int`. T14 adds credential resolution, git detection and the CLI on top. The acceptance test drives these functions against a DuckDB file.
  - Order within publish: check → plan → connect → spec version guard → ensure structure → ownership guard → swap. The spec guard runs before `ensure_structure`, so an older tool never ALTERs a newer schema; that is what SPEC 6's guard intends. The ownership guard follows SPEC order (after ensure). A refused publish leaves every row unchanged (asserted).
  - Publish exit codes: `config_invalid` and a missing or unreadable manifest → 2; other error findings → 1; `MissingExtraError` → 2; `WarehouseError` → 3; ownership → 4; newer spec → 2. Check findings are printed as text before publishing.
  - The ownership comparison is an exact string match of `repo_url`. Credential stripping and normalization belong to git detection in T14.

## T14 Credentials and `publish` command — done
- Changed: src/dbt_agents_schema/{credentials,gitinfo}.py (new), src/dbt_agents_schema/writers/factory.py (new), src/dbt_agents_schema/cli.py (`publish`, `unpublish`), tests/test_credentials.py, tests/test_gitinfo.py, tests/test_cli_publish.py (new)
- Evidence: `make verify` exit 0 (`409 passed`); tests: test_publish_env_credentials, test_publish_dbt_profile_dev_schema (renders `finance_AGENTS` from the fixture profile), test_publish_dbt_profile_target_flag, test_dry_run_never_connects (factory never called), test_missing_credentials_exit_2, test_unknown_environment_exit_2, test_missing_manifest_exit_2, test_check_errors_exit_1_without_connecting, test_writer_not_available_exit_2, test_secrets_never_printed_or_logged, test_secret_from_profile_env_var_not_printed, test_force_takeover_flag, test_unpublish_with_yes, test_unpublish_refuses_without_yes_when_not_a_tty, test_unpublish_prompts_on_tty[*]; test_credentials.py (env shapes and aliases for all four warehouses, env_var rendering with and without default, missing env var, unsupported Jinja, profiles.yml lookup order, missing profile, missing target, unsupported adapter); test_gitinfo.py (GitHub, GitLab, local git, no git, URL cleaning)
- Decisions:
  - `WAREHOUSE_CREDENTIALS` is parsed and validated for all four warehouses now (SPEC 4.1 aliases normalized to canonical keys). Profile mapping covers only DuckDB (`PROFILE_ADAPTERS`); T19 adds Snowflake, BigQuery and Databricks. Error messages name keys, files and env vars, never values. A YAML parse error is replaced by a generic message, because PyYAML errors quote the source text. `Connection.settings` is excluded from `repr`.
  - `writers/factory.py`: `AVAILABLE_WRITERS = ("duckdb",)`. Publishing or unpublishing to another warehouse exits 2 with a clear message until Phase 3 adds those writers. `--dry-run` works for every warehouse because it never opens a writer.
  - `--dry-run` still resolves credentials, because the warehouse type picks the analyst skill and Snowflake uppercasing, and dbt_profile envs need `target_schema`. Missing credentials exit 2 even in a dry run.
  - Git metadata: GitHub vars, then GitLab vars, then `git remote get-url origin` / `rev-parse` (detached HEAD gives a null branch), then null. `clean_repo_url` always drops userinfo, strips `.git` and trailing `/`, and maps SSH and scp-style remotes (`git@host:a/b.git`) to `https://host/a/b`. Without that, a local ssh remote would trip the ownership guard against the CI https URL.
  - `unpublish` also accepts `--target`/`--profiles-dir` (needed for dbt_profile envs). Without `--yes` and stdin not a TTY it exits 2 before connecting. On a TTY it asks for the project key to be typed, and any other answer exits 2 with nothing deleted. Its default project key comes from config/`dbt_project.yml`, because unpublish does not need a manifest.
  - `publish` loads the config once up front to find the environment's credential kind. An unknown `--env` or invalid config exits 2 before check runs.

## T15 Phase 2 review — done
- Reviewer: Phase 2 review: 0 blockers, 1 should-fix, 3 nits
- Fixed (should-fix):
  - A DuckDB `path` carrying a MotherDuck token (`…?motherduck_token=…`) was echoed twice in connect errors. `writers/duckdb.py` now prints `redact_path(path)` (query string replaced by `<redacted>`) and scrubs the path and any `token=` value from driver messages, in both `connect` and `_guard`. Test: test_duckdb_path_token_never_printed, test_redact_path.
- Fixed (nits):
  - Check errors now win over credential errors. When credentials fail to resolve, `publish` runs the check, and if it has error findings it prints them and exits 1 (SPEC 6: check before connect). Test: test_check_errors_win_over_credential_errors.
  - `unpublish` applies the spec version guard (exit 2, nothing deleted) when `DBT_PROJECT` holds a newer `spec_version`. Test: test_unpublish_refuses_newer_spec_version.
  - A dbt-duckdb profile without `database` gives `target_database` = the file stem of `path` (`memory` for `:memory:`, the database name for `md:` paths), matching dbt-duckdb. Tests: test_profile_duckdb_target_database[*], test_profile_duckdb_no_path_is_memory.
- Changed: src/dbt_agents_schema/{cli,credentials,publish}.py, src/dbt_agents_schema/writers/duckdb.py, tests/test_{cli_publish,credentials,duckdb_writer}.py
- Evidence: `make verify` exit 0 (`417 passed`); `tests/acceptance/test_multi_project.py`: 6 passed (scenarios 1–6).

## T16 Snowflake writer — done
- Changed: src/dbt_agents_schema/writers/snowflake.py (new), src/dbt_agents_schema/writers/factory.py (snowflake available), tests/fake_dbapi.py (new: recording DB-API fake), tests/test_snowflake_writer.py (new), tests/conftest.py (new: offline guard), tests/live/harness.py (new: T13 scenarios for any warehouse), tests/live/test_snowflake_live.py (new)
- Evidence: `make verify` exit 0 (`449 passed, 5 deselected`); `uv run pytest -m live tests/live` → `5 skipped` ("LIVE_SNOWFLAKE_CREDENTIALS is not set"). Tests: test_swap_statement_order (BEGIN, deletes per table + ROOT, inserts, COMMIT), test_values_are_bound_never_in_sql_text, test_swap_rolls_back_on_error (last statement ROLLBACK, no COMMIT), test_swap_rolls_back_on_non_database_error, test_odd_identifiers_are_quoted (`"my ""db"""."we.ird schema"`), test_identifiers_are_uppercase_and_quoted, test_inserts_parse_json_and_timestamps, test_inserts_are_batched, test_swap_root_delete_uses_bound_prefix_not_like, test_ensure_structure_migrates_dbt_labs_tables, test_unpublish, test_connect_with_encrypted_private_key_pem, test_connect_with_private_key_path, test_bad_private_key_names_key_not_value, test_wrong_passphrase, test_connect_failure_hides_password, test_missing_extra_names_it, test_factory_opens_snowflake_writer
- Decisions:
  - Snowflake table **and column** names are uppercased before quoting (`"PROJECT"`). SPEC 5 says column names are lowercase, which is the logical name. On Snowflake an unquoted lowercase name resolves to uppercase, so uppercase storage lets analysts write `select project from dbt_model`. It also matches tables the dbt Labs tool created with unquoted names, so migration adds no duplicate columns. Existing-column detection is case-insensitive. Database and schema are quoted as given (config already uppercases rendered names on Snowflake).
  - Inserts use `INSERT INTO t (...) SELECT $1, PARSE_JSON($n), TO_TIMESTAMP_TZ($m) ... FROM VALUES (?, ...), ...`, because Snowflake rejects `PARSE_JSON` inside `VALUES`. Batches hold up to 1000 rows and at most 10,000 bound parameters. The paramstyle is `qmark`, and the session sets `TIMEZONE=UTC`. JSON is bound as JSON text, timestamps as ISO-8601 text.
  - The ROOT skill prefix uses `STARTSWITH("KEY", ?)`, never `LIKE`.
  - Key-pair auth: `private_key_pem` or `private_key_path` (+ optional `private_key_passphrase`) is loaded with `cryptography` and passed as PKCS#8 DER `private_key`. Key errors name the key, never its value. Driver connect errors are scrubbed of password, PEM and passphrase.
  - `tests/conftest.py` replaces `snowflake.connector.connect` with an offline stub for every non-live test. The existing test_secrets_never_printed_or_logged would otherwise try a real network connection now that Snowflake is an available writer. The stub echoes its arguments in its error, so that test now also proves the scrubbing works.
  - Live tests share `tests/live/harness.py`: scenarios 1, 2, 3, 5 and 6 of T13 against a `DBT_AGENTS_SCHEMA_TEST_<hex>` schema that is dropped afterwards. Scenario 4 (dbt Labs migration) needs warehouse-specific legacy DDL and is covered by the fake-connection test.

## T17 BigQuery writer — done
- Changed: src/dbt_agents_schema/writers/bigquery.py (new), src/dbt_agents_schema/writers/factory.py (bigquery available), tests/test_bigquery_writer.py (new, fake `bigquery.Client`), tests/live/test_bigquery_live.py (new), tests/live/harness.py (scenario 5's UPDATE gets `WHERE TRUE`; BigQuery requires a WHERE clause), tests/conftest.py (offline stubs for `bigquery.Client` and `google.auth.default`)
- Evidence: `make verify` exit 0 (`475 passed, 10 deselected`); `uv run pytest -m live tests/live` → `10 skipped`. Tests: test_ensure_structure_creates_dataset_and_tables (no TRANSACTION in any DDL job), test_ensure_structure_migrates_with_add_column, test_swap_is_one_script_with_rollback (exact script shape), test_swap_predicates_and_parameters, test_inline_rows_are_struct_array_parameters, test_values_are_bound_never_in_sql_text, test_large_plan_uses_load_job_staging (staging path forced with `staging_rows=5`: load job before the script, expiring staging table, DROP after), test_staging_chosen_by_bytes, test_staged_json_and_timestamps, test_swap_failure_is_warehouse_error_and_cleans_up, test_reads_before_dataset_exists, test_unpublish_script, test_connect_service_account, test_connect_adc, test_connect_keyfile_path, test_bad_service_account_names_key_not_value, test_missing_extra_names_it, test_factory_opens_bigquery_writer
- Decisions:
  - DDL (`CREATE SCHEMA`, `CREATE TABLE IF NOT EXISTS`, `ALTER TABLE … ADD COLUMN IF NOT EXISTS`) runs as separate jobs. The swap is one script, `BEGIN BEGIN TRANSACTION; deletes; inserts; COMMIT TRANSACTION; EXCEPTION WHEN ERROR THEN ROLLBACK TRANSACTION; RAISE USING MESSAGE = @@error.message; END;`, so rollback is explicit and the error still surfaces (exit 3).
  - Row transport is chosen per table. A table with ≤ 2000 rows whose JSON size is ≤ 1 MB goes in as an `ARRAY<STRUCT>` query parameter `@rows_<TABLE>` read with `UNNEST`. A larger table is loaded with `load_table_from_json` into `_DBT_AGENTS_SCHEMA_STAGE_<TABLE>_<hex>` before the script. That table is created by DDL with a 1-day `expiration_timestamp` and dropped in `finally`. A failed drop only logs a warning, since the table expires anyway. JSON travels as STRING and is converted with `PARSE_JSON` in the INSERT. Timestamps are TIMESTAMP parameters, or ISO text in load jobs.
  - Legacy `project IS NULL` counts come from one `COUNTIF` query just before the script, and unpublish counts from one `count(*)` query. The script cannot return per-statement row counts. The numbers are for the report only.
  - `INFORMATION_SCHEMA` is read once per call at dataset level. A missing dataset (`NotFound`) means "no tables", so the spec and ownership guards work before the first publish.
  - Table names are uppercase (case-sensitive in BigQuery); column names are lowercase. The project is `ResolvedTarget.database`, else `project_id`. The client is billed to `project_id` and uses `location` for every job.
  - Auth: `credentials_json`, then `keyfile` (a path, added for T19's `service-account` profile method), then ADC. Errors name the key, never key material.

## T18 Databricks writer — done
- Changed: src/dbt_agents_schema/writers/databricks.py (new), src/dbt_agents_schema/writers/factory.py (all four warehouses available), tests/test_databricks_writer.py (new), tests/live/test_databricks_live.py (new), tests/conftest.py (offline stub for `databricks.sql.connect`), tests/test_cli_publish.py (see below)
- Evidence: `make verify` exit 0 (`499 passed, 15 deselected`); `uv run pytest -m live tests/live` → `15 skipped`. Tests: test_one_replace_statement_per_table (exactly one `INSERT INTO … REPLACE WHERE` per table, in order DBT_PROJECT … SKILL_USE, ROOT, with no DELETE/MERGE/BEGIN), test_exact_predicates (project tables `` `project` = :project OR `project` IS NULL ``; ROOT `` `provider` = :provider AND (`key` IN (:root_key_0, …) OR startswith(`key`, :skill_prefix)) ``), test_replace_selects_from_staged_table, test_stage_inserts_are_bound_and_batched, test_json_is_bound_as_text, test_empty_table_is_still_replaced, test_legacy_counts, test_failure_is_warehouse_error_and_stages_dropped, test_staging_fails_before_any_replace, test_ensure_structure_*, test_odd_identifiers_are_quoted, test_unpublish, test_connect*, test_missing_extra_names_it, test_factory_opens_databricks_writer
- Decisions:
  - Staging uses a real Delta table `_DBT_AGENTS_SCHEMA_STAGE_<TABLE>_<hex>` per table in the agents schema, not a temporary view, because Databricks forbids parameter markers in view definitions. SPEC 6 allows "a staged temporary view or table". Each stage is filled with batched `INSERT … VALUES (:v0_0, …)` (≤ 500 rows, ≤ 2000 parameters). Every stage is created and filled before the first replace, so a staging failure changes nothing. Stages are dropped in `finally`, and each name is registered before its CREATE so a half-built stage is dropped too.
  - Across tables the swap is per-table atomic, not all-or-nothing, as SPEC 6 accepts for Databricks. A failure after some replaces leaves those tables on the new rows. A re-run converges.
  - `REPLACE WHERE` takes no column list. The SELECT therefore follows the target's column order from `information_schema.columns` (`ORDER BY ordinal_position`), and columns this tool does not know get `NULL`. That keeps migrated dbt Labs tables, where `project` is appended last, correct.
  - Values use native named parameters (`:name`). JSON is bound as JSON text into STRING columns. The ROOT prefix uses `startswith`, never `LIKE`. The host is normalized (scheme and trailing `/` removed), and the token is scrubbed from connect errors.
  - Legacy and unpublish counts come from `count(*)` queries just before the statements. They are for the report only.
  - tests/test_cli_publish.py::test_writer_not_available_exit_2 used `type: databricks` as its example of a warehouse with no writer. Now every warehouse has one, so the test simulates the remaining way a writer is unavailable, a missing extra (`sys.modules["databricks.sql"] = None`). All its assertions are unchanged: exit 2, "databricks" in stderr, secret not printed.

## T19 dbt profile mapping for all warehouses — done
- Changed: src/dbt_agents_schema/credentials.py (`PROFILE_ADAPTERS` = snowflake, bigquery, databricks, duckdb; `_map_snowflake`, `_map_bigquery`, `_map_databricks`), tests/test_profile_mapping.py (new)
- Evidence: `make verify` exit 0 (`526 passed, 15 deselected`). Tests: test_snowflake_password, test_snowflake_private_key_path_and_passphrase, test_snowflake_private_key_pem, test_snowflake_private_key_base64_der[*], test_snowflake_externalbrowser, test_snowflake_unsupported_authenticator, test_snowflake_needs_auth, test_snowflake_missing_required_key, test_snowflake_writer_accepts_externalbrowser, test_bigquery_oauth_is_adc, test_bigquery_service_account_keyfile, test_bigquery_service_account_json, test_bigquery_schema_fallback, test_bigquery_service_account_needs_keyfile, test_bigquery_service_account_json_needs_mapping, test_bigquery_unsupported_method, test_bigquery_missing_project, test_databricks, test_databricks_without_catalog, test_databricks_missing_token, test_env_var_without_default, test_env_var_with_default, test_env_var_overrides_default, test_env_var_missing_names_variable_and_key, test_unsupported_jinja_names_key, test_unsupported_adapter_lists_supported
- Decisions:
  - Profile outputs map onto the same normalized `settings` keys as `WAREHOUSE_CREDENTIALS`, so each writer has one connect path. Snowflake: `private_key` → `private_key_pem`, `authenticator: externalbrowser` passed through. BigQuery: `project` → `project_id`; `oauth` → `auth_method: adc`; `service-account` → `keyfile` (the BigQuery writer loads the file); `service-account-json` → `credentials_json`. Databricks: fields as named.
  - Snowflake auth precedence when several are set: password, private_key_path, private_key, authenticator. Only `externalbrowser` is accepted as an authenticator, per the SPEC table. Any other value is an error naming `outputs.<target>.authenticator`.
  - dbt-snowflake's `private_key` may be PEM text or base64 PKCS#8 DER. DER is wrapped as PEM (`ENCRYPTED PRIVATE KEY` when a passphrase is set), so the writer has one key loader.
  - BigQuery `method` is required (dbt requires it too). Databricks `catalog` is optional, giving `target_database` None, so the session default catalog is used.
  - Errors name `<profiles.yml path>: outputs.<target>.<key>`, never values (each error test also asserts the secret is absent).

## T20 Phase 3 review — done
- Reviewer: Phase 3 review: 0 blockers, 3 should-fix, 4 nits
- Fixed (should-fix):
  - **Informational primary keys** (SPEC 5) on newly created tables: Snowflake `PRIMARY KEY (...)`, BigQuery `PRIMARY KEY (...) NOT ENFORCED`, Databricks `CONSTRAINT pk_<table> PRIMARY KEY (...)` with `NOT NULL` key columns. Migrated tables and stage tables are left without them. Tests: test_new_tables_declare_primary_keys in each of test_{snowflake,bigquery,databricks}_writer.py.
  - **Migrated column order.** The Databricks `REPLACE WHERE` SELECT following `information_schema` order is now tested with a dbt Labs-shaped table (`project` last, plus an unknown legacy column filled with `NULL AS`): test_replace_follows_migrated_column_order. The live harness gained T13 scenario 4 (dbt Labs migration) for Snowflake, BigQuery and Databricks, using legacy DDL built from `fakes.DBT_LABS_TABLES`, so each live module now runs scenarios 1–6.
  - **Concurrent publishes.** A new shared `writers.retry_on_conflict` re-runs a whole swap up to 3 times, backing off 1s, 2s, 4s, only when the error matches a concurrency conflict. BigQuery: "concurrent update" / "could not serialize access"; the script is all-or-nothing, so a re-run is clean. Databricks: `Concurrent*Exception` / `DELTA_CONCURRENT*`; each replace is idempotent for the same plan and stages are rebuilt. Snowflake waits on locks and needs none. Tests: test_concurrent_update_is_retried, test_retries_are_bounded, test_other_errors_are_not_retried, test_concurrent_modification_is_retried, test_databricks_retries_are_bounded, test_databricks_other_errors_not_retried.
- Fixed (nits):
  - The Snowflake connect error now also scrubs the decrypted PKCS#8 DER key bytes (repr, str, hex). Test: test_connect_failure_hides_private_key.
  - BigQuery gained a total inline budget (`inline_budget_bytes`, 4 MB) across all `@rows_<TABLE>` parameters; tables past it are staged. A test at the default thresholds stages a 2100-row DBT_COLUMN: test_default_threshold_large_plan_is_staged, test_total_inline_budget_spans_tables.
- Not fixed (nits, deferred):
  - A Databricks profile without `catalog` on a `hive_metastore`-default workspace fails with a generic exit 3 (no `information_schema`). Recommend documenting "Unity Catalog required" in the partner guide (T27).
  - A Databricks stage table left behind by a killed process is not swept automatically. Its name `_DBT_AGENTS_SCHEMA_STAGE_<TABLE>_<hex>` makes it identifiable; T27's guide should document cleanup.
- Changed: src/dbt_agents_schema/writers/{__init__,snowflake,bigquery,databricks}.py, tests/fake_dbapi.py (`fail_times`, `fail_message`), tests/test_{snowflake,bigquery,databricks}_writer.py, tests/live/harness.py, tests/live/test_{snowflake,bigquery,databricks}_live.py
- Evidence: `make verify` exit 0 (`539 passed, 18 deselected`); `uv run pytest -m live` → `18 skipped` (each LIVE_* variable unset).

## T21 Built-in analyst skills — done
- Changed: src/dbt_agents_schema/skills/{snowflake,bigquery,databricks,duckdb}.md (full bodies replace the T07 placeholders), tests/test_analyst_skills.py (new), tests/acceptance/test_multi_project.py (scenario 7 added, existing scenarios untouched), tests/golden/{finance,marketing}.json (regenerated; the only diff is the embedded DuckDB analyst body)
- Evidence: `make verify` exit 0 (`549 passed, 18 deselected`). Tests: test_every_sql_block_parses_in_its_dialect[snowflake|bigquery|databricks|duckdb] (12 blocks each, one statement per block, each an `exp.Query`, parsed with `sqlglot` in the matching dialect), test_body_covers_spec_9[*], test_each_dialect_has_its_own_body, test_7_analyst_skill_examples_return_rows (publishes finance and marketing, checks the published ROOT body equals the DuckDB body, runs all 12 DuckDB blocks: 13, 2, 1, 8, 2, 8, 3, 6, 2, 5, 1, 5 rows).
- Decisions:
  - Examples use unqualified table names. Each body says how to set the current schema first (Snowflake `USE SCHEMA`, BigQuery default dataset or qualified names, Databricks `USE CATALOG`/`USE SCHEMA`, DuckDB `SET search_path`), so one set of SQL stays valid in every dialect.
  - Example literals are the fixture values (`finance`, `fct_revenue`, `model.shared_pkg.dim_calendar`), marked as placeholders in the text. The acceptance test can then require rows from every example.
  - Recursive lineage uses `WITH RECURSIVE … UNION ALL` with aliases inside the CTE (BigQuery rejects CTE column lists) and a depth cap of 20. It joins edges on `unique_id` with no `project` filter, so lineage crosses projects. The downstream example from `dim_calendar` returns models in both finance and marketing.
  - Case-insensitive search uses `LOWER(x) LIKE`, which works in all four dialects. JSON access differs by dialect (`meta:owner::VARCHAR`, `JSON_VALUE`, `get_json_object`, `json_extract_string`).
  - The Databricks body notes that recursive CTEs need DBR 17.0+ or a current SQL warehouse.

## T22 GitHub reusable workflow — done
- Changed: .github/workflows/dbt-agents-schema.yml (new, `on: workflow_call`), examples/github/agents-schema.yml (new), src/dbt_agents_schema/parse_profile.py (new), src/dbt_agents_schema/cli.py (`write-parse-profile --out DIR`), tests/test_parse_profile.py (new), tests/test_ci_github.py (new)
- Evidence: `make verify` exit 0 (`567 passed, 18 deselected`). `check-jsonschema --builtin-schema vendor.github-workflows .github/workflows/ci.yml .github/workflows/dbt-agents-schema.yml examples/github/agents-schema.yml` → `ok -- validation done`. actionlint is not installed, so it was skipped. Tests: test_snowflake, test_snowflake_key_pair_is_not_written, test_bigquery_service_account, test_bigquery_adc_schema_alias, test_databricks, test_duckdb, test_duckdb_schema_defaults_to_main, test_missing_schema_names_key_and_fix[*], test_invalid_credentials_are_rejected, test_missing_profile_in_dbt_project, test_write_creates_private_file_and_refuses_overwrite, test_cli, test_cli_error_exit_2, test_reusable_workflow_inputs_and_secrets, test_reusable_workflow_steps, test_example_caller.
- Smoke test outside the suite (throwaway venv, dbt-core 1.12.6 with dbt-snowflake 1.12.5, dbt-bigquery 1.12.4, dbt-databricks 1.13.0, dbt-duckdb 1.11.1): real `dbt parse` exits 0 for each warehouse with a profile written by `write-parse-profile`. The relation names come out as `ANALYTICS.MARTS.m`, `` `acme-proj`.`marts`.`m` ``, `` `main`.`marts`.`m` `` and `"w"."marts"."m"`.
- Decisions:
  - **No secrets on disk.** `dbt parse` never connects. The written profile leaves out password, keys and service-account JSON. BigQuery gets `method: oauth`, and the Databricks `token` is a fixed placeholder because dbt-databricks requires the key. The file is created with mode 0600 and `O_EXCL`, so an existing profiles.yml is never overwritten.
  - **Target schema.** dbt writes the profile's schema into every model's `relation_name`/`schema_name`, which are published to `DBT_MODEL`. A guessed schema would publish wrong relation names. `write-parse-profile` therefore reads an optional `schema` key (BigQuery `dataset`, then `schema`; DuckDB defaults to `main`) from `WAREHOUSE_CREDENTIALS`. When it is missing, it exits 2 with an error naming the key and the `DBT_PROFILES_YML` alternative. `from_env` ignores unknown keys, so existing dbt Labs-shaped secrets still work for publish. T27's partner guide must document this key.
  - Workflow: inputs and secrets reach shell only via `env:`, never as `${{ }}` inside `run:` (a test enforces this). `mode` and `dbt-adapter` are validated in the first step, because `workflow_call` has no choice type. The extra is derived as `${DBT_ADAPTER#dbt-}`. Publish fails early without `WAREHOUSE_CREDENTIALS`. The profiles dir is `$RUNNER_TEMP/dbt-agents-schema-profiles`, removed in an `always()` step. Check mode runs `check --format github` after explicit `dbt deps` and `dbt parse` (SPEC 10: wrappers run them).
  - Example caller pins `@v0.1.0` and `package-version: "0.1.0"` on `DavidHrncir/dbt-agents-schema` (the current origin; H2 decides the final home). Publish uses `concurrency: dbt-agents-schema-${{ github.repository }}` with `cancel-in-progress: false`.

## T23 GitLab template — done
- Changed: templates/gitlab/dbt-agents-schema.yml (new), examples/gitlab/.gitlab-ci.yml (new), scripts/validate_gitlab_ci.py (new), Makefile (`validate-ci` runs the GitLab files through the new script), tests/test_ci_gitlab.py (new)
- Evidence: `make verify` exit 0 (`575 passed, 18 deselected`). validate-ci: `templates/gitlab/dbt-agents-schema.yml: 2 document(s)`, `examples/gitlab/.gitlab-ci.yml: 1 document(s)`, `ok -- validation done` against `vendor.gitlab-ci`. Tests: test_header_declares_snake_case_inputs, test_job_names, test_check_job_on_merge_requests_with_codequality, test_publish_job_on_default_branch_serialized, test_jobs_install_pinned_package_and_parse, test_every_input_is_used, test_example_caller, test_validator_checks_each_document (a misspelled `resource_groupp:` in the body document fails validation).
- Decisions:
  - **Multi-document validation.** A `spec: inputs` template is two YAML documents, and check-jsonschema only reads single-document files ("expected a single document in the stream"). `scripts/validate_gitlab_ci.py` splits each file into a `tempfile.TemporaryDirectory` and runs `python -m check_jsonschema --builtin-schema vendor.gitlab-ci` on every document. A first attempt that cleaned up with `rm -rf "$tmp"` inside the Makefile recipe was blocked by the harness safety check before it ran; nothing was deleted.
  - Inputs match GitHub's in snake_case. `dbt_adapter` uses `options` with the four adapters, and `package_version`/`dbt_adapter` have no default, so they are required. `mode` is `auto | check | publish` (default `auto`). One include gives both jobs: with `auto`, rules choose check on merge requests and publish on the default branch, and `check`/`publish` keep only that job. The rules compare the interpolated literal, e.g. `'"$[[ inputs.mode ]]" != "publish" && $CI_PIPELINE_SOURCE == "merge_request_event"'`. This could not be run on a real GitLab instance offline. It passes the schema and should be confirmed in H1/H2.
  - Check writes `gl-code-quality-report.json` at the repo root with `artifacts: when: always`, so the report uploads when check exits 1. Finding paths are `--project-dir`-relative joins and the job runs from `$CI_PROJECT_DIR`, so report paths are repo-relative as Code Quality expects.
  - Publish has `resource_group: dbt-agents-schema-$CI_PROJECT_PATH` and fails early with a clear message when `WAREHOUSE_CREDENTIALS` is unset. `DBT_PROFILES_YML` may be a plain or file-type variable. Without it, `write-parse-profile` (T22) is used. The profile lives in a `mktemp -d` dir outside the checkout.
  - The example caller includes the template by raw GitHub URL pinned to `v0.1.0`, with `workflow: rules` for merge-request and default-branch pipelines. The `include: project:` and local-copy alternatives are in its comments.

## T24 Pre-commit hook and `init` — done
- Changed: .pre-commit-hooks.yaml (new), src/dbt_agents_schema/init.py (new), src/dbt_agents_schema/init_templates/{agents_schema.yml,example_skill.md,github.yml,gitlab.yml} (new), src/dbt_agents_schema/cli.py (`init [--ci github|gitlab|none] [--force]`), tests/test_init.py (new)
- Evidence: `make verify` exit 0 (`587 passed, 18 deselected`). Tests: test_init_output_passes_check_and_ci_validation[github|gitlab] (init on the marketing fixture minus its config and skills; `check` exits 0; the caller passes `check-jsonschema --builtin-schema vendor.github-workflows` or `scripts/validate_gitlab_ci.py`; the adapter `dbt-duckdb` is detected from the manifest), test_init_ci_none_writes_no_caller, test_init_default_ci_is_github, test_init_refuses_to_overwrite_without_force, test_init_refuses_when_only_the_caller_exists, test_init_force_overwrites, test_init_without_manifest_uses_dbt_project_name, test_init_invalid_dbt_name_leaves_project_unset, test_examples_match_init_templates, test_pre_commit_hook_definition, test_package_ships_init_templates.
- Decisions:
  - **One source for callers.** The callers are package templates with `@PACKAGE_VERSION@`/`@DBT_ADAPTER@` placeholders, and `init` pins them to this package's version. `examples/github/agents-schema.yml` and `examples/gitlab/.gitlab-ci.yml` must equal the templates rendered for `0.1.0` and `dbt-snowflake` (test_examples_match_init_templates), so the two cannot drift.
  - **All or nothing.** If any target file exists, `init` writes nothing and exits 2 listing every existing file and `--force`. Caller paths: `.github/workflows/agents-schema.yml`, `.gitlab-ci.yml`, both in `--project-dir`. When the dbt project is a subdirectory of the repo, the user must move the caller to the repo root and set `project-dir`. T27's guide should say this.
  - `project` is written from the normalized `dbt_project.yml` name only when it passes the key pattern. Otherwise the line is a comment and a stderr note tells the user to set it. The adapter comes from `target/manifest.json` `metadata.adapter_type` when it is one of the four supported adapters. Otherwise it is `dbt-snowflake`, with a note.
  - The example skill has comment-only frontmatter showing the `uses` shape. That yields a `skill_without_uses` warning rather than a `skill_unresolved_uses` error, so a fresh `init` passes `check`.
  - **Pre-commit snippet** (printed, not written) includes `additional_dependencies: ["dbt-core>=1.8,<2", "<adapter>"]`. `language: python` hooks run in an isolated venv, and `check --parse` needs `dbt` there. `.pre-commit-hooks.yaml` matches SPEC 10.3 exactly, plus `name` (required by pre-commit) and `description`.

## T25 `fetch-manifest` — done
- **Endpoint used:** `GET https://<host>/api/v2/accounts/<account_id>/jobs/<job_id>/artifacts/manifest.json` with `Authorization: Bearer <DBT_CLOUD_API_TOKEN>`. This is the Administrative API v2 operation "Retrieve Job Artifact": "Given a job id and a set of Run filters return the latest matching artifact for that job". The security scheme is HTTP bearer, and the servers are `cloud.getdbt.com` and `<prefix>.us1.dbt.com` access URLs. Verified on 2026-11-18 against the official spec https://github.com/dbt-labs/dbt-cloud-openapi-spec/blob/main/openapi-v2.yaml (commit cf39d43, 2026-11-17), rendered at https://docs.getdbt.com/dbt-cloud/api-v2. https://docs.getdbt.com/docs/dbt-apis/admin-api says v3 is preferred but not every v2 route is ported. The v3 spec has no artifact routes, so v2 is correct.
- Changed: src/dbt_agents_schema/fetch.py (new), src/dbt_agents_schema/cli.py (`fetch-manifest --account-id --job-id [--host] [--out]`), tests/test_fetch_manifest.py (new)
- Evidence: `make verify` exit 0 (`612 passed, 18 deselected`). Tests (fake HTTP callable; conftest also blocks sockets): test_success_writes_manifest, test_host_forms[*], test_bad_host[*], test_ids_must_be_numeric[*], test_unauthorized[401|403], test_not_found, test_non_json_body, test_json_that_is_not_a_manifest, test_server_error, test_network_error, test_token_is_not_forwarded_on_redirect, test_cli_success_default_out, test_cli_out_and_host, test_cli_missing_token, test_cli_http_error_exit_code.
- Decisions:
  - No HTTP dependency: urllib behind an injectable `Http` callable (`fetch.default_http`). Headers are added with `add_unredirected_header`, so a redirect to another host (e.g. a storage URL) never carries the token.
  - Exit codes: 2 for input, token or not found (missing `DBT_CLOUD_API_TOKEN`, bad `--host`/ids, 401/403, 404); 3 for remote problems (other non-200, network error, non-JSON body, JSON without `metadata`). SPEC 7 defines no codes for this command; these mirror publish's "your input" (2) and "remote system" (3).
  - Default `--out` is `<project-dir>/target/manifest.json`, where `check` and `publish` look by default. The file is written via a temp file and `os.replace`, so a failed download keeps any existing manifest. Errors include the dbt platform `status.user_message` when present and never the token. `--host` accepts a bare host or `https://host/`. `http://` is rejected.

## T26 Phase 4 review — done
- Reviewer: Phase 4 review: 0 blockers, 3 should-fix, 7 nits
- Fixed (should-fix):
  - **GitHub secrets scoped to steps.** `WAREHOUSE_CREDENTIALS` and `DBT_PROFILES_YML` were in job-level `env:`, so pip, `dbt deps` and `dbt parse` (third-party package code, `env_var()`) could read them in PR check runs. Now only "Write profiles.yml" (both) and "Publish" (`WAREHOUSE_CREDENTIALS`) receive them. "Validate inputs" gets only `HAS_WAREHOUSE_CREDENTIALS: ${{ secrets.WAREHOUSE_CREDENTIALS != '' }}`. Cleanup uses `rm -rf "${DBT_PROFILES_DIR:?}"`. Test: test_secrets_reach_only_the_steps_that_need_them.
  - **GitLab credentials.** GitLab cannot mask a multi-line JSON/YAML document, and a file-type variable holds a path that `from_env` rejected. The template now reads `WAREHOUSE_CREDENTIALS` from the file when it is a path (wrapper plumbing; the CLI still receives the document as SPEC 4.1 says). Both variables are moved into an unexported shell variable and `unset` before `dbt deps`/`dbt parse`, and publish gets them back explicitly. The header comment now recommends a protected File variable plus an unprotected, secret-free `DBT_PROFILES_YML` (from `write-parse-profile`) for merge request checks, which also answers nit 6. The example and init callers no longer say "masked". Test: test_secrets_are_unset_before_dbt_runs. See SPEC CHANGE PROPOSAL B.
  - **`write-parse-profile` schema key** is a SPEC deviation, now logged as SPEC CHANGE PROPOSAL A. Behaviour is unchanged, because a guessed schema would publish wrong relation names.
- Fixed (nits):
  - bigquery.md shows backtick-quoted `` `<project>.<agents dataset>.ROOT` `` (project ids with `-`).
  - fetch-manifest also maps `http.client.HTTPException` (e.g. `IncompleteRead`) to exit 3 instead of a traceback with exit 1. Test: test_interrupted_download.
  - The GitHub caller's concurrency comment now says a queued publish is replaced by a newer one and a running one is never cancelled.
- Not fixed (nits, deferred):
  - GitLab `mode: auto` divergence: logged as SPEC CHANGE PROPOSAL C.
  - The literal-to-literal GitLab rules (`'"$[[ inputs.mode ]]" != "publish" && …'`) are schema-valid but unverified on a real GitLab instance. Confirm with `mode` auto/check/publish during H1/H2.
  - Recursive lineage CTEs use `UNION ALL` with a depth cap of 20. Results are correct, but path counts can grow on DAGs with many diamonds. Worth a sentence in T27's guide if partners have very large DAGs.
- Changed: .github/workflows/dbt-agents-schema.yml, templates/gitlab/dbt-agents-schema.yml, examples/{github/agents-schema.yml,gitlab/.gitlab-ci.yml}, src/dbt_agents_schema/init_templates/{github,gitlab}.yml, src/dbt_agents_schema/skills/bigquery.md, src/dbt_agents_schema/fetch.py, tests/test_ci_{github,gitlab}.py, tests/test_fetch_manifest.py
- Evidence: `make verify` exit 0 (`615 passed, 18 deselected`; GitHub and GitLab CI files `ok -- validation done`).

### SPEC CHANGE PROPOSAL A — target schema for `write-parse-profile` (SPEC 4.1, 7)
- Problem: `dbt parse` writes the profile's schema into every model's `relation_name` and `schema_name`, which are published. SPEC 4.1's `WAREHOUSE_CREDENTIALS` shape has no schema, so a profile built only from it would publish made-up relation names. The current code requires an optional `schema` key (BigQuery `dataset`/`schema`; DuckDB defaults to `main`) and exits 2 without it. A team reusing its dbt Labs secret without `DBT_PROFILES_YML` must add one key.
- Options: (a) add the optional `schema`/`dataset` key to SPEC 4.1, used only by `write-parse-profile` (current behaviour); (b) add `write-parse-profile --schema` and a `parse-schema`/`parse_schema` wrapper input; (c) require `DBT_PROFILES_YML` whenever the schema is unknown.
- Recommendation: (a). It is one optional key, publish ignores it, and the error names the key and the `DBT_PROFILES_YML` alternative. (b) can be added later. No task is blocked; this records the deviation for human approval.

### SPEC CHANGE PROPOSAL B — GitLab credentials variable (SPEC 10.2)
- Problem: SPEC 10.2 says "a masked CI/CD variable `WAREHOUSE_CREDENTIALS`". GitLab masking requires a single line from a restricted character set, which a JSON/YAML credentials document (braces, quotes, spaces, newlines, PEM keys) does not meet.
- Options: (a) SPEC says "a protected CI/CD variable, type File recommended" and the wrapper reads the file (current behaviour); (b) also accept base64-encoded `WAREHOUSE_CREDENTIALS` in the CLI, which can be masked; (c) keep "masked" and accept that it is unattainable.
- Recommendation: (a) now. Consider (b) for v0.2 if partners want masking in job logs.

### SPEC CHANGE PROPOSAL C — GitLab `mode` input (SPEC 10.2)
- Problem: SPEC 10.2 asks for "the same inputs as GitHub", where `mode` is `check | publish`. A GitLab `include` creates both jobs at once, and including the template twice with different inputs would collide on job names.
- Options: (a) allow `mode: auto | check | publish` (default `auto`: check on merge requests, publish on the default branch) (current behaviour); (b) drop `mode` from the GitLab template and rely on rules only.
- Recommendation: (a). It keeps the input names aligned and the common case a single include.

## T27 Partner guide — done
- Changed: docs/partner-guide.md (new), scripts/gen_config_reference.py (new), src/dbt_agents_schema/schemas/agents_schema.schema.json (annotations only), Makefile (`docs-check` in `verify`), README.md (points to the guide), tests/test_partner_guide.py (new)
- Evidence: `make verify` exit 0 (`634 passed, 18 deselected`; `config reference up to date`). Tests: test_guide_exists_and_readme_points_to_it, test_guide_has_section[*] (10 sections), test_every_command_appears, test_every_cli_flag_from_help_appears (flags parsed from each subcommand's `--help`), test_every_choice_value_appears, test_every_config_key_appears (key paths walked from the schema), test_every_exit_code_is_troubleshot, test_config_reference_has_no_drift, test_generator_detects_drift, test_generator_requires_markers.
- Decisions:
  - The config reference is generated from the shipped schema, between markers in the guide. To make that possible, the schema gained `description`/`default` annotations, which do not change validation. Prose defaults that are not valid instances (`project`, `environments`, `database`) use a custom `x-default` annotation, so editors never offer them as values.
  - Install instructions show both `pip install "dbt-agents-schema[<extra>]==0.1.0"` and a git URL. Where partners install from is H2's decision, and the CI wrappers assume an index that serves the pinned version.
  - The guide mentions the T26-deferred lineage depth-20 note under Troubleshooting.

## T28 End-to-end demo — done
- Changed: scripts/demo_duckdb.sh (new), tests/test_demo.py (new)
- Evidence: `make verify` exit 0 (`636 passed, 18 deselected`). `bash scripts/demo_duckdb.sh` exit 0: finance and marketing checks pass (1 warning each); finance and marketing go to `AGENTS` (prod), finance to `finance_AGENTS` (dev); 3 analyst queries return rows. Tests: test_demo_exits_zero_and_shows_results, test_demo_leaves_the_checkout_clean.
- Decisions:
  - The demo uses the real CLI through `uv run --project <repo>`, so it works from any directory. Prod uses `credentials: env` (`WAREHOUSE_CREDENTIALS` with `type: duckdb`). Dev uses `credentials: dbt_profile` through `tests/fixtures/profiles.yml` with `FIXTURE_DUCKDB_PATH`.
  - The fixture manifests are checked in, so the demo passes `--manifest` instead of running `dbt parse`. It needs no network and no dbt adapter install.
  - The three queries come out of the published `skill/dbt-agents-schema-analyst` ROOT row, the same way an agent would read them: projects, downstream lineage of `model.shared_pkg.dim_calendar` across both projects, and the skills list. The script exits non-zero if any of them returns no rows.
  - The DuckDB file goes to `$DEMO_DIR` (default: a new temp dir). Nothing is written in the checkout.
  - Noticed for T29: the `skill_without_uses` message prints `{schemas: [...], tables: [...]}}` (one `}` too many).

## T29 Final review — done
- Reviewer: Final review: 1 blockers, 1 should-fix, 5 nits
- Fixed (blocker):
  - **`unpublish` accepted any project key.** `--project 'finance/payments'` widened the ROOT `skill/<project>/` prefix delete into finance's skills, which the reviewer reproduced. `publish.unpublish` now calls `validate_project_key` before connecting and exits 2. An empty `--project ""` no longer falls back to the config key (`is None` instead of `or`, also in `cli._cmd_unpublish`). The `RootScope` docstring now says where the key is validated. Test: test_unpublish_rejects_invalid_project_key[finance/payments|Finance|fin%|] (exit 2, row counts unchanged).
- Fixed (should-fix):
  - The partner guide no longer says a failed publish always leaves the previous rows. Databricks is atomic per table, so the guide says to publish again (What it does, Exit 3).
- Fixed (nits):
  - The `skill_without_uses` message had an extra `}`. Test: test_finance_is_clean_except_skill_without_uses now asserts the exact text.
  - The guide says `publish` reports an invalid config with exit 2. The pre-commit snippet pins `dbt-core>=1.8,<2`, as `init` prints. The Databricks section says Unity Catalog is required and leftover `_DBT_AGENTS_SCHEMA_STAGE_*` tables must be dropped by hand.
  - Snowflake uppercase column names: logged as SPEC CHANGE PROPOSAL D (no code change).
- Release: CHANGELOG.md `0.1.0` added. Local commit `release: 0.1.0`. No tag, no push (H2).
- Changed: src/dbt_agents_schema/{publish,cli,check}.py, src/dbt_agents_schema/writers/__init__.py, tests/test_cli_publish.py, tests/test_check.py, docs/partner-guide.md, CHANGELOG.md (new)
- Evidence: see the `make verify` and `bash scripts/demo_duckdb.sh` runs recorded at commit time (both exit 0).

### SPEC CHANGE PROPOSAL D — Snowflake column name case (SPEC 5)
- Problem: SPEC 5 says "Column names are lowercase". The Snowflake writer creates quoted, uppercased table and column names (T16), because the dbt Labs tool's Snowflake tables use uppercase columns and unquoted queries (`SELECT project FROM DBT_MODEL`) resolve to uppercase. Quoted lowercase columns would force agents to write `"project"` and would clash with migrated dbt Labs tables.
- Options: (a) SPEC 5 says column names are lowercase except on Snowflake, where they are stored uppercase so unquoted SQL works (current behaviour); (b) quoted lowercase on Snowflake too.
- Recommendation: (a). The analyst skill's unquoted SQL works on every warehouse.
- Evidence (T29, recorded): `make verify` exit 0 (`640 passed, 18 deselected`; `config reference up to date`). `bash scripts/demo_duckdb.sh` exit 0 (prod: finance, marketing; dev: finance; analyst queries return 2, 2 and 5 rows).

## SPEC update — proposals A–D accepted
- Changed: docs/SPEC.md only. 4.1: optional `schema`/`dataset` key, read only by `write-parse-profile` (A). 5: Snowflake stores table and column names uppercase (D). 7: `write-parse-profile` row says where the schema comes from (A). 10.2: GitLab `mode` is `auto | check | publish`, default `auto` (C); `WAREHOUSE_CREDENTIALS` is a protected variable, File type recommended, not masked (B). No code changes.
- Evidence: `make verify` exit 0 (`640 passed, 18 deselected`).

## SPEC CHANGE PROPOSALS E–H — v0.2.0: CI parse and published-context checks
- Source: review of the `dbt_fivetran_platform_governance` workflow (dbt Labs CLI) against the v0.1.0 reusable workflow, 2026-11-18. That workflow parses with dbt Fusion against a production-shaped profile holding placeholder keys, fails when the manifest did not resolve to production, and fails when published text contains measured values. v0.1.0 does none of these.
- Decisions (user, 2026-11-18): Fusion is the default parse engine and dbt-core stays available; `measured_values_in_context` defaults to `warning`; a placeholder profile is the default when `DBT_PROFILES_YML` is absent and the environment has a `parse_target`.
- Status: awaiting acceptance. Phase 6 of PLAN.md starts only after SPEC.md is updated.

### SPEC CHANGE PROPOSAL E — production parse target (SPEC 3, 7)
- Problem: `dbt parse` resolves each model's database, schema and `relation_name` from the profile target, and those values are published. In CI the profile comes from `DBT_PROFILES_YML` or from `write-parse-profile`, which reads the real `WAREHOUSE_CREDENTIALS` plus a `schema` key (proposal A). That puts a live credential next to `dbt deps`/`dbt parse`, which run third-party code, and makes pull request checks depend on the publish secret. Parse never connects, so no real credential is needed.
- Options: (a) optional `environments.<name>.parse_target` in `agents_schema.yml`, and `write-parse-profile --env NAME` writes a profile from it with placeholder credentials and reads no secret; (b) workflow inputs `parse-database`/`parse-schema`/`parse-location`; (c) keep v0.1 behaviour.
- Recommendation: (a). One source of truth, also used by proposal F; the wrappers stay thin; it works the same in GitHub, GitLab and locally. (b) would put the target in two files that can drift.
- Proposed SPEC text:
  - SPEC 3, optional key under each environment:
    ```yaml
        parse_target:                     # optional; production shape for `dbt parse` in CI
          type: bigquery                  # snowflake | bigquery | databricks | duckdb; required
          name: prod                      # profile target name; default: the environment name
          database: internal-sales        # Snowflake database, BigQuery project, Databricks catalog; required except duckdb
          schema: dh_dbt_ft_platform_gov  # dbt target schema (BigQuery dataset); required
          location: US                    # BigQuery only; optional
    ```
    Plain strings, no templates. Unknown keys are a config error.
  - SPEC 7, `write-parse-profile`: add `--env NAME`. When that environment has `parse_target`, write the profile from it: profile name = `profile` in `dbt_project.yml`, target = `parse_target.name`, and connection fields filled with obvious placeholders (BigQuery `method: service-account-json` with a dummy key, Snowflake `password`, Databricks `token`, DuckDB `path: ":memory:"`). It must not read `WAREHOUSE_CREDENTIALS`. Without `--env`, or when the environment has no `parse_target`, behaviour is unchanged. File mode 0600.
  - SPEC 7, `check`: add `--env NAME`, which turns on the environment-dependent rules of proposal F. `publish --env` turns them on too.

### SPEC CHANGE PROPOSAL F — manifest target rules (SPEC 8)
- Problem: a parse against the wrong profile succeeds silently and publishes relation names that point nowhere; an empty parse publishes zero models and stays green; parsing with dbt-core instead of Fusion silently publishes empty column `meta` (Fusion copies model `meta` to columns, dbt-core does not). v0.1 catches none of these.
- Options: (a) three configurable rules in the check engine; (b) leave it to each project's workflow as inline Python (what the governance project does today).
- Recommendation: (a). The checks then also run locally, in pre-commit and in GitLab, and have tests.
- Proposed SPEC text (SPEC 8, configurable rules):

  | id | Default | Condition |
  | --- | --- | --- |
  | `manifest_target_mismatch` | error | Only with `--env` and a `parse_target`. A published model's `database` differs from `parse_target.database`, or its `schema` does not start with `parse_target.schema` (both case-insensitive; the database comparison is skipped when `parse_target.database` is unset). One finding per model, naming the model, its resolved `database.schema` and the expected values. |
  | `manifest_empty` | error | The manifest has no published models. |
  | `column_meta_missing` | off | No column of any published model has a non-empty `meta`. Projects that rely on Fusion's column `meta` set it to `error`. |

### SPEC CHANGE PROPOSAL G — measured values in published context (SPEC 3, 8)
- Problem: published descriptions, `meta` and skills should describe method, not measurements. A row count or ratio written into a description goes stale, nothing re-checks it, and an agent quotes it as current instead of querying. `sampled_values_in_description` only catches profiler output (five patterns, descriptions only); it misses hand-written counts, `meta` and skill files.
- Options: (a) a new rule alongside the existing one; (b) widen `sampled_values_in_description`, which changes results for existing users.
- Recommendation: (a), default `warning` (user decision).
- Proposed SPEC text:
  - SPEC 8, configurable rule: `measured_values_in_context` | warning | Published text contains a measured value (below).
  - Scope: the description and `meta` of each published model, the description and `meta` of each of its columns, and the full text of each skill file. `meta` is scanned as JSON with sorted keys.
  - Each line is stripped of surrounding whitespace and one leading list marker (`^\d+[.)]\s+`), then:
    1. **Count phrase** → finding, unless the line matches an `exempt` pattern. Count phrases: `\d[\d,]*\s*(<nouns>)\b` with built-in nouns `rows?|records?|tables?|columns?|events?|jobs?|users?|customers?|accounts?|orders?|transactions?` plus `count_nouns`; `\b\d[\d,]*\s+of\s+\d`; `\b\d+(\.\d+)?x\b`; `\b\d+(\.\d+)?[KMB]\b` (suffix case-sensitive). Built-in exemption: `\b1 (row|record) per\b`.
    2. Otherwise a line matching a built-in or configured `allow` pattern is skipped. Built-in: `[<>]=\s*\d`, `\b0-100\b`, `\bone (row|record) per\b`, `\b1:1\b`, `accepted values`, `percentile`, `\bp\d{2}\b`.
    3. Otherwise any number (`(?<![\w.\-])\d[\d,]*(\.\d+)?`, commas removed) with value ≥ `min_number` is a finding.
    Matching is case-insensitive except where noted. At most one finding per line. The message gives the matched text, the first 100 characters of the line, and the fix: use qualitative wording or the query that derives the value, or add a pattern to `check.measured_values.allow`/`exempt`. Skill findings carry `file` and `line`; model and column findings carry `node`.
  - SPEC 3, under `check`:
    ```yaml
      measured_values:
        min_number: 10    # numbers below this are never flagged
        allow: []         # regexes; a matching line may contain numbers
        exempt: []        # regexes; a matching line may contain count phrases
        count_nouns: []   # extra regex nouns for count phrases, e.g. "grants?", "syncs?"
    ```
    An invalid regex is `config_invalid`, naming the key and list index.
  - Tests: at least three positive and three negative examples per step, including: "(94 rows)" on a line that also matches an `allow` pattern is flagged; "10. Never name a model" is not; "one row per order" and "1 row per order" are not; "older than 2x the connection's sync_frequency" passes only with an `exempt` pattern; numbers below `min_number` are not flagged.
- Note: the governance project's script lists `1 row per` as allowed, but its count-phrase check runs first and flags it. The built-in exemption above fixes that.

### SPEC CHANGE PROPOSAL H — CI wrappers (SPEC 10, 10.1, 10.2)
- Problem: the reusable workflow installs from a package index the tool is not on yet; parses with dbt-core only, so column `meta` is published empty; cannot pass the variables a project's `env_var()` calls need at parse time (a reusable workflow does not see the caller's `env`); uses partial parsing; builds the parse profile from the publish secret; and publishes without logging the target first.
- Options: (a) the inputs and steps below; (b) document a hand-written caller workflow instead of the reusable one.
- Recommendation: (a). The engine default changes from dbt-core to Fusion for v0.1 callers that do not set `dbt-engine`; CHANGELOG 0.2.0 must say so, and `dbt-engine: core` restores the old behaviour.
- Proposed SPEC text:
  - SPEC 10: "All wrappers install a pinned dbt-agents-schema (`package-version`, or a pinned `package-spec`) and a pinned dbt engine, run `dbt deps` and `dbt parse --no-partial-parse`, then call the CLI. They contain no publishing logic."
  - SPEC 10.1, inputs added or changed:
    - `warehouse` (`snowflake | bigquery | databricks | duckdb`): selects the package extra and, with dbt-core, the adapter. Required unless `dbt-adapter` is given, in which case it is derived from it (v0.1 callers keep working).
    - `dbt-engine` (`fusion | core`, default `fusion`). Fusion is installed with the official installer, pinned to `dbt-fusion-version` (default: the version this release was tested with, recorded in CHANGELOG). `core` installs `dbt-core<dbt-version>` and `dbt-adapter` (default `dbt-<warehouse>`).
    - `package-spec` (optional): a full pip requirement, such as a git URL pinned to a commit. When set it replaces `package-version`, which is otherwise required.
    - `parse-env` (optional): lines of `KEY=VALUE` exported to every later step. Blank lines and `#` lines are ignored. Keys must match `^[A-Z_][A-Z0-9_]*$` and must not be `WAREHOUSE_CREDENTIALS`, `DBT_PROFILES_YML`, `DBT_PROFILES_DIR`, `PACKAGE_REPO_TOKEN`, or start with `GITHUB_` or `RUNNER_`. Not for secrets: values are not masked.
    - Secret `PACKAGE_REPO_TOKEN` (optional): read-only token for installing `package-spec` from a private git repository. Given only to the install step, through a git `insteadOf` rule that the same step removes; never written inside the checkout.
  - SPEC 10.1, steps: validate inputs → checkout → setup Python → install engine and package → profile (`DBT_PROFILES_YML` when set, else `write-parse-profile --env <environment>`) → `dbt deps` → `dbt parse --no-partial-parse` → check mode: `check --env <environment> --format github`; publish mode: `publish --env <environment> --dry-run`, then `publish --env <environment>`.
  - SPEC 10.1, example caller (and `init --ci github`): triggers `workflow_dispatch`, plus `pull_request` and `push` to `main` filtered by `paths` (`models/**`, `macros/**`, the skills directory, `dbt_project.yml`, `packages.yml`, `package-lock.yml`, `agents_schema.yml`, the caller file itself); one job whose `mode` is `check` on pull requests and `publish` otherwise; `concurrency` per pull request ref, and one shared queued group for publishes.
  - SPEC 10.2: the GitLab template gets `warehouse`, `dbt_engine`, `dbt_fusion_version` and `package_spec`, the same profile, parse, check and dry-run steps. No `parse_env`: included jobs already see the project's CI/CD variables.

## SPEC update — proposals E–H accepted
- Changed: docs/SPEC.md only. 3: `environments.<name>.parse_target`, `check.measured_values`, the four new rules in the example, and validation rules for both (E, G). 6: the check step runs the environment-dependent rules for `--env` (F). 7: `check --env`; `write-parse-profile --env` writes a placeholder profile from `parse_target` and does not read `WAREHOUSE_CREDENTIALS` (E). 8: `measured_values_in_context` (warning), `manifest_target_mismatch` (error), `manifest_empty` (error), `column_meta_missing` (off), and the measured-values algorithm and test cases (F, G). 10, 10.1, 10.2: pinned engine and package, `--no-partial-parse`, new inputs `warehouse`, `dbt-engine` (default `fusion`), `dbt-fusion-version`, `package-spec`, `parse-env`, secret `PACKAGE_REPO_TOKEN`, step order with dry run, new example caller; GitLab parity without `parse_env` (H). No code changes; Phase 6 of PLAN.md can start.

## T30 Config: `parse_target` and `measured_values` — done
- Changed: `schemas/agents_schema.schema.json` (`environments.<name>.parse_target`, `check.measured_values`, four new rules), `config.py` (`ParseTarget`, `MeasuredValuesConfig`, `DEFAULT_RULES`, template and regex checks), `tests/test_config.py`, regenerated config reference in `docs/partner-guide.md`.
- Evidence: `make verify` exit 0 (`671 passed, 18 deselected`). Tests: test_parse_target_all_keys, test_parse_target_name_defaults_to_environment, test_parse_target_database_required_except_duckdb, test_parse_target_duckdb_needs_no_database, test_parse_target_type_and_schema_required, test_parse_target_type_enum, test_parse_target_unknown_key, test_parse_target_refuses_templates, test_parse_target_location_is_bigquery_only, test_measured_values_defaults, test_measured_values_partial_keeps_defaults, test_measured_values_all_keys, test_measured_values_min_number_integer_ge_0, test_measured_values_unknown_key, test_measured_values_invalid_regex_names_key_and_index, test_invalid_regex_reported_as_config_invalid_by_check, test_new_rule_defaults_and_configurable, test_new_rules_in_json_schema.
- Decisions:
  - `database` is required by the JSON Schema for every `type` except duckdb (`if`/`then`). `location` on a non-BigQuery `parse_target` is a schema error, reading "BigQuery only" in SPEC 3 as a constraint.
  - Template refusal and regex compilation run after JSON Schema validation, so they report the JSON path (`$.check.measured_values.allow[1]`) and the YAML line. `check` reports them as `config_invalid` through the existing ConfigError path.
  - `test_defaults_with_no_file` now lists the four new rules in its expected defaults (extended, not weakened).

## T31 Placeholder parse profile — done
- Changed: `parse_profile.py` (`build_placeholder_profile`, `PLACEHOLDER`, `write_parse_profile(..., parse_target=)`), `cli.py` (`write-parse-profile --env`), `tests/test_parse_profile.py`.
- Evidence: `make verify` exit 0 (`683 passed, 18 deselected`). Tests: test_parse_target_snowflake, test_parse_target_bigquery, test_parse_target_databricks, test_parse_target_duckdb, test_parse_target_never_reads_warehouse_credentials[bigquery|databricks|duckdb|snowflake], test_parse_target_ignores_set_credentials, test_env_without_parse_target_uses_credentials, test_unknown_env_is_exit_2, test_parse_target_refuses_overwrite; the existing T22 tests pass unchanged.
- Decisions:
  - Placeholder value `dbt-parse-placeholder` for every connection field the adapters require: Snowflake `account`/`user`/`password`/`warehouse`; Databricks `host`/`http_path`/`token`; BigQuery `keyfile_json` with that value as `private_key_id`, inside a PEM-shaped `private_key`, and in `client_email`. Whether Fusion accepts the dummy key without connecting is confirmed by H3.
  - DuckDB writes `path: ":memory:"` and no `database`, even when `parse_target.database` is set, because dbt-duckdb rejects a `database` that disagrees with the path. Models then carry the database `memory`, so T32 must skip the database comparison for DuckDB unless the user set it.
  - `--env` with an environment that has no `parse_target` falls back to the `WAREHOUSE_CREDENTIALS` profile. An undefined environment exits 2 and lists the available ones.

## T32 Manifest target rules — done
- Changed: `check.py` (`check_project(..., env=)`, `manifest_target_mismatch`, `manifest_empty`, `column_meta_missing`), `cli.py` (`check --env`; publish's check-before-credentials path passes `--env`), `publish.py` (passes `env` to the check), `tests/test_check_target.py` (new).
- Evidence: `make verify` exit 0 (`703 passed, 18 deselected`). Tests: test_matching_target_no_finding, test_case_differences_no_finding, test_wrong_database_one_finding_per_model, test_wrong_schema_prefix, test_unrelated_schema_flags_every_model, test_duckdb_unset_database_skips_database_comparison, test_duckdb_unset_database_still_checks_schema, test_without_env_never_reports_mismatch, test_env_without_parse_target_no_finding, test_mismatch_can_be_downgraded, test_manifest_with_no_models_is_error, test_manifest_with_models_not_empty, test_column_meta_missing_off_by_default, test_column_meta_missing_at_error, test_column_meta_present_no_finding_at_error, test_check_cli_env_reports_mismatch, test_check_cli_unknown_env_exit_2, test_publish_mismatch_exit_1_without_connecting, test_publish_mismatch_exit_1_even_without_credentials, test_publish_matching_target_succeeds. Existing tests pass unchanged.
- Decisions:
  - A model with a null database (when `parse_target.database` is set) or a null schema counts as a mismatch.
  - `check --env` with an environment that is not defined exits 2 and lists the defined ones. An environment without `parse_target` adds no findings.
  - `column_meta_missing` is not reported when `manifest_empty` fires, because an empty manifest has no columns to check. Both point at the manifest file.
  - A draft test for `manifest_empty` with every package excluded was dropped before commit: root models are always published (SPEC 5.7), so that case cannot happen.

## T33 `measured_values_in_context` rule — blocked
- Status: `[!]`, following the T33 instruction "If a fixture description now produces a warning that breaks an existing assertion, stop and log it in PROGRESS.md instead of editing the assertion."
- What happened: implemented the rule as written in SPEC 8. New module `measured.py`, wired into `check.py`, 55 new tests in `tests/test_measured_values.py`, all passing; the `bad` fixture triggers the rule on "1,204,331 rows". The full suite then failed in 8 existing tests: test_check.py::test_finance_is_clean_except_skill_without_uses, ::test_marketing_is_clean_except_skill_without_uses, ::test_rule_severity_override_and_off, ::test_manifest_without_project_name_falls_back_to_dbt_project_yml; test_cli_check.py::test_clean_finance_exits_0_with_warnings, ::test_format_json, ::test_format_gitlab_output_path, ::test_output_for_json. They count warnings on the clean fixtures, and the new rule adds one per line containing "ISO 4217":
  - finance `stg_invoices.currency_code`: "ISO 4217 currency of the invoice."
  - `shared_pkg` `dim_currency.currency_code` (in finance and marketing): "ISO 4217 code."
  Step 3 flags 4217 because it is ≥ `min_number`. It is the name of a standard, not a measurement.
- Work kept: `docs/patches/T33-measured-values.patch` (applies cleanly to this commit with `git apply`). Not merged, so `make verify` stays green.

### SPEC CHANGE PROPOSAL I — standard identifiers in `measured_values_in_context` (SPEC 8)
- Problem: step 3 flags any number ≥ `min_number`, including standard identifiers ("ISO 4217", "ISO 8601", "RFC 3339", "UTF 8" is below the limit), which are common in column descriptions and never go stale. Every partner with currency or timestamp columns gets the warning. The clean fixtures already do, which breaks 8 existing test assertions.
- Options:
  - (a) Add a built-in `allow` pattern for standard identifiers, for example `\b(ISO|RFC|IEC|IEEE|EN|DIN)[ -]?\d+`. As with every allow pattern, a count phrase on the same line is still flagged at step 1.
  - (b) Keep the algorithm and add `check.measured_values.allow: ['\bISO \d+']` to the finance and marketing fixture configs. The tests stay unchanged, but every real project must learn to add the same pattern.
  - (c) Keep the algorithm and update the 8 assertions to expect the extra warnings. This conflicts with the plan's instruction for T33.
- Recommendation: (a). Proposed SPEC 8 text: add `\b(ISO|RFC|IEC|IEEE)[ -]?\d+` to the built-in allow list in step 2, and add "ISO 4217 currency code" as a required negative test case.
- On acceptance: apply the patch, add the pattern and test case, and re-run `make verify`.

## T34 GitHub reusable workflow and caller — done
- Changed: `.github/workflows/dbt-agents-schema.yml` (new inputs `warehouse`, `dbt-engine`, `dbt-fusion-version`, `package-spec`, `parse-env`; secret `PACKAGE_REPO_TOKEN`; steps validate → checkout → setup-python → install → profile → `dbt deps` → `dbt parse --no-partial-parse` → check `--env` / publish dry run then publish → cleanup), `init_templates/github.yml` and `examples/github/agents-schema.yml` (new caller pinned to v0.2.0), `init.py` (`@WAREHOUSE@` placeholder), `tests/test_ci_github.py` (rewritten for SPEC 10.1), `tests/test_init.py` (the examples test renders the GitHub example at 0.2.0; GitLab stays at 0.1.0 until T35), CHANGELOG (unreleased 0.2.0 stub recording the Fusion default).
- Evidence: `make verify` exit 0 (`737 passed, 18 deselected`; check-jsonschema `ok` for both files; actionlint not installed). Tests: test_reusable_workflow_inputs_and_defaults, test_fusion_default_recorded_in_changelog, test_reusable_workflow_secrets, test_step_order, test_step_commands, test_install_step_engines, test_inputs_never_interpolated_into_scripts, test_checkout_does_not_persist_credentials, test_secrets_reach_only_the_steps_that_need_them, test_validate_derives_requirement, test_validate_v01_inputs_derive_warehouse, test_validate_package_spec_replaces_version[2], test_validate_rejects_bad_inputs[7], test_parse_env_exports_lines, test_parse_env_rejects_reserved_keys[7], test_parse_env_rejects_malformed_lines[5], test_example_caller, test_example_caller_inputs_exist_in_workflow, test_v01_caller_still_validates, test_files_pass_github_schema[2], test_init.py::test_examples_match_init_templates.
- Test changes: the T22 assertions in `test_ci_github.py` encoded the v0.1 contract that SPEC 10.1 replaced (`package-version` and `dbt-adapter` required, the two-job caller, `WAREHOUSE_CREDENTIALS` given to the profile step). They were replaced by assertions for the new contract, and a v0.1 caller is kept as a fixture that must still validate. The Validate inputs script is executed with bash in the tests, so `parse-env` rejection and v0.1 input derivation are tested behaviour, not text matches.
- Decisions:
  - **Needs human confirmation (H3):** `dbt-fusion-version` defaults to `2.0.0`. The network was not available here to check the installer or the current Fusion version. The installer call is `curl -fsSL https://public.cdn.getdbt.com/fs/install/install.sh | sh -s -- --update --version "$DBT_FUSION_VERSION"`. Confirm both the flag and the default in H3 and update CHANGELOG to match (a test ties the two together).
  - `WAREHOUSE_CREDENTIALS` no longer reaches the profile step (plan T34). A v0.1 caller that relied on a profile built from it must now add `parse_target` or pass `DBT_PROFILES_YML`. The profile step prints exactly that fix as an `::error::`. T36 must list this in CHANGELOG as a breaking change.
  - `package-spec`: a bare URL becomes `dbt-agents-schema[<warehouse>] @ <url>`; a full requirement is used as-is.
  - `PACKAGE_REPO_TOKEN` is set as a global git `insteadOf` rule for `https://github.com/` (in `~/.gitconfig`, outside the checkout). The rule is removed after `pip install` and also by an EXIT trap if the install fails. Checkout uses `persist-credentials: false`.
  - `parse-env` values are written to `GITHUB_ENV` using the random-delimiter heredoc form, so a value cannot inject further variables.
  - Caller: workflow-level `concurrency` with group `agents-schema-pr-<ref>` (cancel in progress) for pull requests and the shared `agents-schema-publish` group (`cancel-in-progress` false) otherwise. The `paths` list is written out twice instead of using a YAML anchor. The caller passes only `WAREHOUSE_CREDENTIALS`, and shows `dbt-engine: core` and `parse-env` as comments.

## T35 GitLab template parity — done
- Changed: `templates/gitlab/dbt-agents-schema.yml` (inputs `warehouse`, `dbt_engine`, `dbt_fusion_version`, `package_spec`; `dbt_adapter` and `package_version` now optional and validated in the script; Fusion or dbt-core install; `write-parse-profile --env`; `dbt parse --no-partial-parse`; `check --env`; publish dry run before publish), `init_templates/gitlab.yml` and `examples/gitlab/.gitlab-ci.yml` (v0.2.0, `warehouse:`), `tests/test_ci_gitlab.py`, `tests/test_init.py` (GitLab example rendered at 0.2.0).
- Evidence: `make verify` exit 0 (`747 passed, 18 deselected`; the GitLab validator reports `ok` for the template and example). Tests: test_header_declares_snake_case_inputs, test_fusion_default_matches_github_workflow, test_engine_and_package_install, test_profile_parse_and_check_use_env, test_publish_runs_dry_run_first, test_input_validation_derives_requirement[3], test_input_validation_rejects[3], test_secrets_are_unset_before_dbt_runs (unchanged), test_jobs_install_pinned_package_and_parse, test_example_caller, test_validator_checks_each_document (unchanged).
- Test changes: `test_header_declares_snake_case_inputs` asserted the v0.1 contract (`package_version` and `dbt_adapter` required, adapter options); it now asserts the SPEC 10.2 inputs. The install-line assertion now expects `==$PACKAGE_VERSION`, with the input passed through job `variables`, plus a new check that inputs never appear inside the validation script. `test_example_caller` now expects v0.2.0 and `warehouse`.
- Decisions:
  - Unlike GitHub, `WAREHOUSE_CREDENTIALS` is still present when the GitLab profile is written. SPEC 10.2 only requires secrets to be unset before `dbt deps`/`dbt parse`, and this keeps v0.1 includes without `parse_target` working through the credentials-based profile. With `parse_target` it is not read (T31).
  - The validation block is executed with bash in the tests, the same way as the GitHub one.

## T36 Docs — done
- Changed: `docs/partner-guide.md`: version 0.2.0; config example with `parse_target`, `measured_values` and the new rules; CLI reference for `check --env` and `write-parse-profile --env`; GitHub and GitLab setup rewritten around the new caller and `parse_target`; new sections "Production parse target" and "Keep measurements out of published context"; the Fusion-default note; troubleshooting entries for `manifest_target_mismatch` and `manifest_empty`. `CHANGELOG.md`: unreleased 0.2.0 entry.
- Evidence: `make verify` exit 0 (`747 passed, 18 deselected`). `tests/test_partner_guide.py`: 19 passed (every flag, choice and config key appears; config reference up to date).
- Decisions:
  - T33 is blocked, so the measured-values section documents the SPEC behaviour under a "Status in 0.2.0" note: the settings are validated but the rule reports nothing yet. CHANGELOG says the same. If proposal I is accepted and T33 lands before release, remove both notes.
  - CHANGELOG lists two behaviour changes for 0.1 callers: the default engine (Fusion; `dbt-engine: core` restores dbt-core) and, on GitHub, that `WAREHOUSE_CREDENTIALS` no longer reaches the profile step. It records the default `dbt-fusion-version` `2.0.0`, still to be confirmed in H3.
  - The example `allow` pattern `\bISO \d+` in the guide shows how a project can work around the false positive itself in the meantime.

## T37 Phase 6 review and version — done
- Reviewer: Phase 6 review: 0 blockers, 1 should-fix, 3 nits
- Fixed (should-fix): `write-parse-profile --env` ignored `parse_target.database` for DuckDB, so a DuckDB project that set it could never pass `manifest_target_mismatch`. The profile path is now `<database>.duckdb`; dbt-duckdb names the catalog after the stem, and `dbt parse` does not open the file. Test: test_parse_target_duckdb_with_database. This replaces the T31 decision to always write `:memory:`.
- Fixed (nits):
  - `check --env X` with an invalid config no longer says "environment not defined"; it reports the `config_invalid` findings and exits 1. Test: test_check_cli_invalid_config_reports_findings_not_unknown_env.
  - The guide's "Status in 0.2.0" blockquote no longer swallows the next paragraph.
  - `V01_CALLER` in `test_ci_github.py` now pairs v0.1-style inputs with `package-version: "0.2.0"`, a combination that would actually run.
- Not fixed, for H3: whether dbt Fusion supports DuckDB with the default `dbt-engine: fusion`. If it does not, the guide must tell DuckDB users to set `dbt-engine: core`. H3 should also confirm the default `dbt-fusion-version` `2.0.0` and the installer's `--version` flag (see T34).
- Version: `__version__` and `pyproject.toml` set to `0.2.0`; `uv.lock` refreshed; CHANGELOG 0.2.0 dated 2026-11-18 with a Fixed section.
- Evidence: `make verify` exit 0 (`749 passed, 18 deselected`).
- Phase 6 status: T30, T31, T32, T34, T35, T36 and T37 done. T33 blocked on SPEC CHANGE PROPOSAL I; patch kept at `docs/patches/T33-measured-values.patch`. H3 and H4 are for a human. Not tagged or pushed.
- Test change for the bump: the three tests in `tests/test_cli.py` pin the expected version string; it changed from `"0.1.0"` to `"0.2.0"`, with every assertion kept.

## SPEC update — proposal I accepted
- Changed: docs/SPEC.md only. 8, step 2: built-in allow pattern for standard identifiers `\b(ISO|RFC|IEC|IEEE)[ -]?\d+`; "ISO 4217 currency code" added to the required negative test cases (I, option a, user decision 2026-11-18).

## T33 `measured_values_in_context` rule — done
- Changed: `src/dbt_agents_schema/measured.py` (new; plus the standard-identifier allow pattern), `src/dbt_agents_schema/check.py` (rule wired in), `tests/test_measured_values.py` (new), `docs/partner-guide.md` (built-in allow list names standard identifiers), `CHANGELOG.md` (0.2.0 entry: the rule is enforced). Removed `docs/patches/T33-measured-values.patch`, now merged.
- Evidence: `make verify` exit 0 (`812 passed, 18 deselected`; check-jsonschema `ok` for GitHub and GitLab; `config reference up to date`). The 8 tests that blocked T33 pass unchanged (test_check.py::test_finance_is_clean_except_skill_without_uses, ::test_marketing_is_clean_except_skill_without_uses, ::test_rule_severity_override_and_off, ::test_manifest_without_project_name_falls_back_to_dbt_project_yml; test_cli_check.py::test_clean_finance_exits_0_with_warnings, ::test_format_json, ::test_format_gitlab_output_path, ::test_output_for_json). New tests: test_step2_builtin_allow[ISO 4217 currency code|Timestamp in ISO-8601 format|Formatted per RFC 3339|Units follow IEC 80000|Floating point per IEEE 754], test_standard_identifier_allow_limits[3] (a count phrase on an ISO line is still flagged; look-alikes are not allowed), plus the 55 tests from the saved patch.
- Decisions:
  - The saved patch's `test_bad_fixture_triggers_rule` had a line over 100 characters; the node id string was split across two literals. Same assertion.
  - Folded into 0.2.0 rather than a 0.2.1: 0.2.0 was never tagged or published, so the CHANGELOG entry was corrected instead of pinning every example and test to a new version.

## T33 review — done
- Ran the `spec-reviewer` subagent on c69954d (T33 landed after the T37 Phase 6 review). Result: 0 blockers, 2 should-fix, both stale text in `docs/partner-guide.md`. Code, tests and the SPEC update for proposal I match SPEC 3 and 8.
- Changed: `docs/partner-guide.md` only. Removed the "Status in 0.2.0: the rule does not report findings yet" note, and replaced the `'\bISO \d+'` `allow` example (now built in) with a commented `'\bHTTP \d{3}\b'` example.
- Evidence: `make verify` exit 0 (`812 passed, 18 deselected`; `config reference up to date`).

## H3 Live CI run — done (reported by user, 2026-11-19)
- `dbt_fivetran_platform_governance` runs `.github/workflows/agents-schema.yml` on its `main` branch, pinned to c69954d for both `uses:` and `package-spec`, with `warehouse: bigquery` and `dbt-fusion-version: 2.0.0-preview.177`. The user reports that the CI job succeeded. Only BigQuery was exercised; no other warehouse has had a placeholder parse yet.
- Pointing the deployed Google agents at the new schema is tracked outside this repo.

## H2 / H4 Release
- `v0.1.0` tagged at 893fe77 (`release: 0.1.0`). `v0.2.0` tagged at c69954d, the commit the governance project runs, not the later docs-only fix. `v0.1` branch merged to `main` with a merge commit, so c69954d stays in `main`'s history.
