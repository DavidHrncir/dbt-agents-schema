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
