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
