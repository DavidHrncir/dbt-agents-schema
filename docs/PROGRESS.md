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
