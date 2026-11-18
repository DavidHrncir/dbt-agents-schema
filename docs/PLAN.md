# Build plan

Work top to bottom. Each task lists what to do and **Done when**: checks that must be
true and demonstrated (command + output) before the box is ticked. `make verify` must
exit 0 at the end of every task.

Status marks: `[ ]` todo · `[x]` done · `[!]` blocked (reason in PROGRESS.md) ·
`[H]` human-only, skip it.

A phase is complete when every task in it is `[x]`, `[!]` or `[H]`, and the
`spec-reviewer` subagent has reviewed the phase (last task of each phase).

---

## Phase 0 — Foundation

- [x] **T01 Scaffold the package.**
  Create `pyproject.toml` (hatchling build, `uv` for envs), `src/dbt_agents_schema/`
  with `__init__.py` (`__version__ = "0.1.0"`) and `cli.py`, `tests/`, a `Makefile`, and
  `.github/workflows/ci.yml` that runs `make verify`. Runtime deps: `pyyaml`,
  `jsonschema`. Optional extras: `snowflake` (`snowflake-connector-python`,
  `cryptography`), `bigquery` (`google-cloud-bigquery`, `google-auth`), `databricks`
  (`databricks-sql-connector`), `duckdb` (`duckdb`). Dev group: `pytest`, `ruff`,
  `mypy`, `types-PyYAML`, `duckdb`, `sqlglot`, `check-jsonschema`.
  Makefile targets: `install` (`uv sync --all-extras --group dev`), `lint`
  (`ruff check` + `ruff format --check`), `typecheck` (`mypy src`, strict), `test`
  (`pytest -m "not live"`), `test-live` (`pytest -m live`), `validate-ci` (SPEC
  section 10.4; tolerate missing files until T22/T23 create them), `verify` (lint,
  typecheck, test, validate-ci), `fixtures` (T02).
  **Done when:** `make install && make verify` exits 0; `uv run dbt-agents-schema
  --version` prints `0.1.0`; a test covers `--version`.

- [x] **T02 Fixture dbt projects.**
  Under `tests/fixtures/projects/` create three dbt projects using `dbt-duckdb`:
  `shared_pkg` (a package with 2 models), `finance` and `marketing` (each installs
  `shared_pkg` with `packages: [{local: ../shared_pkg}]`, 4–6 models, sources, a seed,
  model and column descriptions, `access: public` on at least two models, and an
  `agents/skills/` folder with 2 skills, one with `uses` and one without). Add a
  `finance_v2` variant: finance with one model removed, one column added and one skill
  removed. Add a `bad` project that triggers every check rule (section 8 of SPEC).
  Add `tests/fixtures/profiles.yml` (duckdb, `path: ":memory:"`). Write
  `scripts/build_fixtures.sh` that installs `dbt-core` and `dbt-duckdb` into a temporary
  venv, runs `dbt deps` and `dbt parse` for each project, and copies each manifest to
  `tests/fixtures/manifests/<name>.json`. Commit the manifests; tests must never need dbt
  installed.
  **Done when:** `make fixtures` regenerates all five manifests; each manifest contains
  the expected models (assert in a test); the test suite passes without dbt installed.

- [x] **T03 Phase 0 review.** Run the `spec-reviewer` subagent on Phase 0. Fix findings
  that affect correctness or the spec. Log the result.

## Phase 1 — Core logic (pure, no warehouse)

- [x] **T04 Config loader.** Ship `schemas/agents_schema.schema.json`; load and validate
  `agents_schema.yml`, apply defaults, resolve the default `project`, render
  `schema`/`database` templates (SPEC section 3).
  **Done when:** tests cover defaults with no file, every validation rule, each template
  variable, unknown template variables, `target_*` with `credentials: env`, and
  Snowflake uppercasing.

- [x] **T05 Manifest reader.** Load a manifest and produce typed model, column and
  dependency records per SPEC 5.7, including `upstream_project` and package
  selection.
  **Done when:** tests on the fixtures prove: root models always included; package
  models included by default and excluded with `include_packages: false` or
  `exclude_packages`; disabled nodes ignored; ephemeral models have null
  `relation_name`; dependency edges to sources and seeds carry this project as
  `upstream_project`.

- [x] **T06 Skills loader.** Walk `skills_dir`, compute keys `skill/<project>/<path>`,
  parse `uses` frontmatter, keep malformed `uses` as a finding rather than a crash.
  **Done when:** tests cover nested folders, Windows-style path separators, no
  frontmatter, invalid YAML frontmatter, and duplicate keys differing only by case.

- [x] **T07 Plan builder.** Pure function `(config, manifest, skills, run_metadata) ->
  Plan` producing every row for the six tables, including all ROOT rows of SPEC 5.6 and
  the `project/<project>` overview text. Rows sorted by primary key.
  **Done when:** golden-file tests (`tests/golden/*.json`, regenerated only with
  `UPDATE_GOLDEN=1`) for finance and marketing; a test proves the plan is identical
  across two runs; counts in the `DBT_PROJECT` row match the other tables.

- [x] **T08 Check engine.** Implement every rule in SPEC section 8 with severities and
  the four output formats.
  **Done when:** each rule has positive and negative tests; the `bad` fixture triggers
  every rule; the sampled-values heuristics have the required examples; the `gitlab`
  output validates as a Code Quality report (list of objects with `description`,
  `check_name`, `fingerprint`, `severity`, `location.path`, `location.lines.begin`).

- [x] **T09 `check` command.** Wire `check` into the CLI with `--parse`, `--format`,
  `--output` and exit codes.
  **Done when:** CLI tests (in-process, using `main(argv)`) cover: clean finance exits
  0 with warnings printed; `bad` exits 1; missing manifest exits 2 with the
  "run `dbt parse` first" message; `--parse` calls the subprocess (mocked).

- [x] **T10 Phase 1 review.** Run the `spec-reviewer` subagent on Phase 1. Fix
  correctness findings. Log the result.

## Phase 2 — Writer and the multi-project guarantee (DuckDB)

- [x] **T11 Writer interface.** Define the writer protocol: `ensure_structure`,
  `read_project_row`, `max_spec_version`, `swap(plan)`, `unpublish(project)`, `close`.
  One SQL/dialect module per warehouse; identifier quoting in one helper per dialect.
  **Done when:** the protocol is typed, documented, and used by a no-op fake in tests.

- [x] **T12 DuckDB writer.** Implement it fully per SPEC section 6, including
  migration of dbt Labs-shaped tables (missing columns added, `project IS NULL` rows
  removed) and transactional swap.
  **Done when:** unit tests pass for structure creation, column migration, swap,
  unpublish and rollback (inject a failure between delete and insert; previous rows
  survive).

- [x] **T13 Acceptance test: many projects, one schema.** In
  `tests/acceptance/test_multi_project.py`, against DuckDB:
  1. Publish finance, then marketing. Both projects' rows exist in every table; ROOT has
     both `project/` rows and both projects' skills.
  2. Publish `finance_v2`. Finance reflects v2 (removed model, removed skill and its
     `SKILL_USE` rows gone; new column present). Marketing rows are byte-identical to
     before.
  3. Publish finance from a different `repo_url`: exit 4; with `--force-takeover`:
     succeeds.
  4. Pre-create dbt Labs-shaped tables with legacy rows, publish: columns added, legacy
     rows removed, count logged.
  5. A schema with a `DBT_PROJECT.spec_version` of 2: publish exits 2.
  6. Unpublish marketing: only marketing rows are gone.
  **Done when:** all six scenarios pass. This test is the project's definition of done;
  never weaken it.

- [x] **T14 Credentials and `publish` command.** Resolve credentials for
  `credentials: env` and `credentials: dbt_profile` (DuckDB first), git metadata, spec
  version guard, `--dry-run`, `unpublish`.
  **Done when:** CLI tests publish the finance fixture to a DuckDB file through both
  credential sources; `--dry-run` makes no connection (assert the factory is never
  called); dev environment renders `<target_schema>_AGENTS` from the fixture profile;
  secrets never appear in output (assert on captured logs).

- [x] **T15 Phase 2 review.** Run the `spec-reviewer` subagent on Phases 1–2 together.
  Fix correctness findings. Log the result.

## Phase 3 — Warehouses

For each warehouse, test SQL generation and call sequence against a fake DB-API
connection that records statements and parameters. Add a live test marked
`@pytest.mark.live` that runs the T13 acceptance scenarios against the real warehouse
only when its env var is set (`LIVE_SNOWFLAKE_CREDENTIALS`, `LIVE_BIGQUERY_CREDENTIALS`,
`LIVE_DATABRICKS_CREDENTIALS`, each holding a `WAREHOUSE_CREDENTIALS`-style document),
using a schema named `DBT_AGENTS_SCHEMA_TEST_<random>` that the test drops afterwards.

- [x] **T16 Snowflake writer.** Transactional swap (`BEGIN` … `COMMIT`, `ROLLBACK` on
  error), batched parameterized inserts, `VARIANT` via `PARSE_JSON`, uppercase
  identifiers, key-pair auth.
  **Done when:** fake-connection tests assert statement order, binding (no values in SQL
  text), rollback on error, and quoting of odd identifiers; live test exists and skips
  cleanly.

- [x] **T17 BigQuery writer.** DDL outside the transaction; swap inside one
  multi-statement transaction script; rows staged through a load job into a temporary
  table or passed as `UNNEST` of a STRUCT array parameter, chosen by row volume; JSON
  columns as `JSON`.
  **Done when:** fake-client tests assert the script shape and parameters; a test
  covers a plan large enough to force the staging path; live test exists and skips.

- [x] **T18 Databricks writer.** Per-table atomic `INSERT INTO … REPLACE WHERE …`
  from a staged temporary view; ROOT replaced with a predicate covering the shared keys,
  `project/<p>` and `skill/<p>/%`.
  **Done when:** fake-connection tests assert one replace statement per table and the
  exact predicates; live test exists and skips.

- [x] **T19 dbt profile mapping for all warehouses.** Snowflake, BigQuery, Databricks
  profile outputs to connection settings per SPEC 4.2, including `env_var` handling.
  **Done when:** tests cover each auth method in the SPEC table, `env_var` with and
  without default, a missing env var, unsupported Jinja and an unsupported adapter.

- [x] **T20 Phase 3 review.** Run the `spec-reviewer` subagent on Phase 3. Fix
  correctness findings. Log the result.

- [H] **H1 Live warehouse run.** Human sets the `LIVE_*` variables and runs
  `make test-live` for each warehouse the partners use.

## Phase 4 — Distribution

- [x] **T21 Built-in analyst skills.** Four dialect bodies per SPEC section 9;
  published by every publish.
  **Done when:** a test extracts every fenced SQL block and parses it with `sqlglot` in
  the matching dialect; the DuckDB acceptance test runs every DuckDB example query
  against a schema holding finance and marketing and each returns rows.

- [x] **T22 GitHub reusable workflow.** Per SPEC 10.1, plus `write-parse-profile`.
  **Done when:** the workflow and example caller pass `check-jsonschema --builtin-schema
  vendor.github-workflows` (and `actionlint` if installed); `write-parse-profile` has
  tests for each warehouse type.

- [ ] **T23 GitLab template.** Per SPEC 10.2.
  **Done when:** the template and example caller pass `check-jsonschema
  --builtin-schema vendor.gitlab-ci`; a test loads the template YAML and asserts the
  job names, rules, `resource_group` and the codequality artifact.

- [ ] **T24 Pre-commit hook and `init`.** Per SPEC 7 and 10.3.
  **Done when:** `init --ci github` and `init --ci gitlab` in a temp dir produce files
  that pass `check` and the CI validation above; `init` refuses to overwrite without
  `--force`.

- [ ] **T25 `fetch-manifest`.** Per SPEC 7. Look up the current dbt platform Admin API
  endpoint for job artifacts first and record the URL and doc link in PROGRESS.md.
  **Done when:** tests with a mocked HTTP layer cover success, 401, 404 and a non-JSON
  body; no real network call in tests.

- [ ] **T26 Phase 4 review.** Run the `spec-reviewer` subagent on Phase 4. Fix
  correctness findings. Log the result.

## Phase 5 — Partner-ready

- [ ] **T27 Partner guide.** `docs/partner-guide.md`: what it does, install, config
  reference (generated from the JSON Schema by a script, checked in `make verify` for
  drift), GitHub setup, GitLab setup, developer workflow (`publish --env dev`),
  dbt platform projects (`fetch-manifest`), migrating from the dbt Labs tool, warehouse
  permissions needed, troubleshooting by exit code. Update `README.md` to point to it.
  **Done when:** every CLI flag and config key appears in the guide (a test checks this
  against `--help` output and the schema).

- [ ] **T28 End-to-end demo.** `scripts/demo_duckdb.sh`: check both fixtures, publish
  finance and marketing to `prod` and finance to `dev` in a DuckDB file, then run three
  analyst-skill queries and print the results.
  **Done when:** the script exits 0 from a clean checkout after `make install`.

- [ ] **T29 Final review.** Run the `spec-reviewer` subagent over the whole repo against
  SPEC.md. Fix correctness findings, update CHANGELOG.md with `0.1.0`, and make a local
  commit `release: 0.1.0`. Do not tag or push.

- [H] **H2 Release.** Human reviews, tags `v0.1.0`, pushes, and decides where partners
  install from.
