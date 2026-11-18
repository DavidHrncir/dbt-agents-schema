# dbt-agents-schema — Build Specification (spec v1)

This is the contract. Code, tests and docs must match it. If something here is wrong or
impossible, do not silently diverge: record a **SPEC CHANGE PROPOSAL** in
`docs/PROGRESS.md` and stop that task (see `CLAUDE.md`).

---

## 1. Purpose and scope

`dbt-agents-schema` publishes metadata about dbt projects into an **agents schema** in the
warehouse, so AI agents can discover models, columns, lineage and skills with plain SQL.

It fixes one defect in the dbt Labs `agents_schema` tool: that tool recreates the `DBT_*`
and `SKILL_USE` tables on every publish, so only the last project published survives.
Here, every row carries a `project` key and each publish replaces **only its own
project's rows**. Any number of dbt projects can share one agents schema.

**In scope**

- Sources: dbt projects only (`target/manifest.json` + markdown skill files).
- Warehouses: Snowflake, BigQuery, Databricks. DuckDB is a first-class destination used
  for tests and local demos.
- CI: GitHub Actions and GitLab CI, both thin wrappers around one CLI.
- dbt runtimes: dbt v1 (dbt-core), dbt v2 (Fusion) and dbt platform. All of them produce
  a `manifest.json`; that file is the only dbt interface this tool depends on.

**Out of scope (do not build)**

- ClickHouse, Redshift or any other warehouse.
- dbt Mesh-specific behavior beyond what falls out naturally (deferred).
- Fivetran Context Layer integration. It writes to its own schema.
- Non-dbt providers (LookML, OSI, Sigma, …). Do not preclude them, do not build them.
- Publishing to PyPI or pushing git tags. Releases are cut by a human.

**Compatibility promise**

The physical table and column names used by the dbt Labs tool are kept. New columns and
one new table are added. Existing agents and the dbt Labs analyst skill keep working;
they simply see every project. Running the dbt Labs tool against the same schema after
adopting this tool is unsupported (it recreates the tables and drops the new columns).

---

## 2. Terms

| Term | Meaning |
| --- | --- |
| project | The key that owns a set of rows. Declared in `agents_schema.yml`; defaults to the dbt project name. |
| environment | A named publish target in `agents_schema.yml`, such as `prod` or `dev`. Each resolves to a database + schema + credential source. |
| agents schema | The warehouse schema that holds the tables in section 5. An organization may have any number. |
| publish | One run of `dbt-agents-schema publish`: replace one project's rows in one agents schema. |
| plan | The in-memory set of rows a publish will write. Built by a pure function from config + manifest + skills. |

---

## 3. Project config: `agents_schema.yml`

Lives at the dbt project root (next to `dbt_project.yml`). Validated with a JSON Schema
shipped inside the package at `src/dbt_agents_schema/schemas/agents_schema.schema.json`.
Unknown keys are an error. If the file is absent, every field takes its default, except
that `check` reports `config_missing` (warning by default) so teams notice.

```yaml
version: 1                       # required; only 1 is valid
project: finance                 # default: dbt project name from the manifest
owner: data-platform@acme.com    # optional, free text
description: Finance marts       # optional, free text
include_packages: true           # default true: also publish models from installed packages
exclude_packages: []             # package names never published, even when include_packages is true
skills_dir: agents/skills        # default agents/skills; missing directory = no skills
environments:                    # default: the prod and dev entries shown here
  prod:
    credentials: env             # env | dbt_profile
    database: ANALYTICS          # optional; default from the credentials
    schema: AGENTS               # required; template allowed
  dev:
    credentials: dbt_profile
    schema: "{{ target_schema }}_AGENTS"
check:
  description_scope: public      # public | all — which models the description rules apply to
  external_schemas: []           # schemas skills may reference that dbt does not build
  rules:                         # severity per rule: error | warning | off
    config_missing: warning
    missing_model_description: warning
    missing_column_description: warning
    sampled_values_in_description: error
    skill_without_uses: warning
    skill_unresolved_uses: error
```

**Validation rules**

- `project`: `^[a-z][a-z0-9_]{0,62}$`. The default is the manifest's
  `metadata.project_name` lowercased with `-` replaced by `_`; if that is absent, the
  `name` in `dbt_project.yml`.
- `environments.<name>`: name matches `^[a-z][a-z0-9_]{0,30}$`.
- `schema` and `database` templates support exactly these variables, written
  `{{ name }}` with optional spaces: `target_schema`, `target_database`, `project`,
  `env` (the environment name). Any other `{{ … }}` content is a config error. Rendered
  values are uppercased on Snowflake, left as-is elsewhere.
- `target_schema` / `target_database` are only available when `credentials:
  dbt_profile`; using them with `credentials: env` is a config error.
- `description_scope: public` means models whose `access` is `public`. If a project
  sets no `access` anywhere, `public` covers nothing and `check` emits one info line
  suggesting `all`.

---

## 4. Credentials

Each environment names one credential source.

### 4.1 `credentials: env`

Read `WAREHOUSE_CREDENTIALS` from the environment: a YAML or JSON mapping. The shapes
for Snowflake, BigQuery and Databricks are **identical to the dbt Labs tool**, so
existing CI secrets carry over:

- Snowflake: `type: snowflake`, `account`, `user`, `warehouse`, `database`, optional
  `role`, and one of `password`, `private_key_pem` (+ optional
  `private_key_passphrase`), `private_key_path`.
- BigQuery: `type: bigquery` (also accept `big_query`), `project_id` (aliases
  `projectId`, `project`), optional `location`, and either `credentials_json`
  (service-account mapping, aliases `credentialsJson`) or `auth_method: adc`.
- Databricks: `type: databricks`, `host` (aliases `server_host_name`,
  `serverHostName`), `http_path` (alias `httpPath`), `catalog`, `token` (aliases
  `access_token`, `personal_access_token`, `personalAccessToken`).
- DuckDB: `type: duckdb`, `path` (file path; `:memory:` allowed).
- Every warehouse also accepts an optional `schema` key (BigQuery: `dataset`, falling
  back to `schema`): the dbt target schema the models build into. Only
  `write-parse-profile` reads it (section 7); `publish` ignores it. DuckDB defaults to
  `main`; on the other warehouses `write-parse-profile` exits 2 without it, naming the
  key and the `DBT_PROFILES_YML` alternative.

Mapping of "database" and "schema" per warehouse:

| Warehouse | database means | schema means |
| --- | --- | --- |
| Snowflake | database | schema |
| BigQuery | project | dataset |
| Databricks | catalog | schema |
| DuckDB | catalog (the attached file) | schema |

### 4.2 `credentials: dbt_profile`

For developers. Read the dbt profile the project already uses:

- `profiles.yml` location: `--profiles-dir`, else `DBT_PROFILES_DIR`, else the project
  directory if it contains `profiles.yml`, else `~/.dbt/`.
- Profile name: `profile` in `dbt_project.yml`. Target: `--target`, else the profile's
  `target`.
- Supported Jinja inside `profiles.yml`: `{{ env_var('NAME') }}` and
  `{{ env_var('NAME', 'default') }}` only. Anything else is a config error naming the
  key.
- `target_schema` = the output's `schema` (BigQuery: `dataset`, falling back to
  `schema`). `target_database` = `database` (BigQuery: `project`; Databricks: `catalog`).

Profile → connection mapping:

| Adapter | Fields read |
| --- | --- |
| snowflake | `account`, `user`, `role`, `warehouse`, `database`, `schema`; auth: `password`, `private_key_path` (+ `private_key_passphrase`), `private_key`, or `authenticator: externalbrowser` |
| bigquery | `project`, `dataset`/`schema`, `location`; `method`: `oauth` → application default credentials, `service-account` → `keyfile`, `service-account-json` → `keyfile_json` |
| databricks | `host`, `http_path`, `token`, `catalog`, `schema` |
| duckdb | `path`, `schema` (default `main`) |

Any other adapter type is a config error listing the supported ones. Never log secrets.

---

## 5. Data model

All tables live in the resolved agents schema. Logical types map to warehouse types in
one place per writer.

| Logical | Snowflake | BigQuery | Databricks | DuckDB |
| --- | --- | --- | --- | --- |
| varchar / text | VARCHAR | STRING | STRING | VARCHAR |
| boolean | BOOLEAN | BOOL | BOOLEAN | BOOLEAN |
| integer | NUMBER(38,0) | INT64 | BIGINT | BIGINT |
| timestamp | TIMESTAMP_TZ | TIMESTAMP | TIMESTAMP | TIMESTAMPTZ |
| json | VARIANT | JSON | STRING (JSON text) | JSON |

Table names are uppercase: `ROOT`, `DBT_PROJECT`, `DBT_MODEL`, `DBT_COLUMN`,
`DBT_DEPENDENCY`, `SKILL_USE`. Column names are lowercase, except on Snowflake, where
table and column names are stored uppercase (quoted) so unquoted SQL such as
`SELECT project FROM DBT_MODEL` resolves on every warehouse. Primary keys are declared
where the warehouse supports informational constraints; uniqueness is guaranteed by the
writer, not the database.

### 5.1 `DBT_PROJECT` (new)

One row per project published into this schema.

| Column | Type | Notes |
| --- | --- | --- |
| project | varchar | **PK** |
| dbt_project_name | varchar | `metadata.project_name` |
| owner | varchar | from config |
| description | text | from config |
| environment | varchar | environment name used for this publish |
| repo_url | varchar | from CI env or git remote; null if unknown |
| git_sha | varchar | |
| git_branch | varchar | |
| dbt_version | varchar | `metadata.dbt_version` |
| manifest_generated_at | timestamp | `metadata.generated_at` |
| publisher_version | varchar | this package's version |
| spec_version | integer | `1` |
| published_at | timestamp | UTC, set by the writer |
| model_count | integer | |
| column_count | integer | |
| dependency_count | integer | |
| skill_count | integer | |

### 5.2 `DBT_MODEL`

| Column | Type | Notes |
| --- | --- | --- |
| project | varchar | **PK part**, new |
| unique_id | varchar | **PK part** |
| name | varchar | |
| package_name | varchar | new |
| database_name | varchar | |
| schema_name | varchar | |
| alias | varchar | new; `node.alias` or `name` |
| relation_name | varchar | new; `node.relation_name` (null for ephemeral) |
| materialization | varchar | `config.materialized` |
| access | varchar | new; `node.access` |
| description | text | empty string when missing |
| file_path | varchar | `original_file_path` |
| tags | json | array |
| meta | json | `config.meta`, else `meta`, else `{}` |

### 5.3 `DBT_COLUMN`

| Column | Type | Notes |
| --- | --- | --- |
| project | varchar | **PK part**, new |
| model_id | varchar | **PK part**; parent `unique_id` |
| column_name | varchar | **PK part** |
| data_type | varchar | empty string when missing |
| description | text | empty string when missing |
| meta | json | `config.meta`, else `meta`, else `{}` |

### 5.4 `DBT_DEPENDENCY`

| Column | Type | Notes |
| --- | --- | --- |
| project | varchar | **PK part**, new |
| upstream_id | varchar | **PK part** |
| downstream_id | varchar | **PK part** |
| upstream_type | varchar | prefix before the first `.`, else `unknown` |
| downstream_type | varchar | always `model` |
| upstream_project | varchar | new; this project when `upstream_id` is a node or source in this manifest, else null |

### 5.5 `SKILL_USE`

| Column | Type | Notes |
| --- | --- | --- |
| project | varchar | **PK part**, new |
| provider | varchar | always `dbt` (kept for compatibility) |
| skill_key | varchar | **PK part**; the ROOT key |
| use_kind | varchar | **PK part**; `schema` or `table` |
| object_ref | varchar | **PK part** |

### 5.6 `ROOT` (unchanged shape)

| Column | Type | Notes |
| --- | --- | --- |
| provider | varchar | **PK part** |
| key | varchar | **PK part** |
| content | text | |

Rows this tool owns, all with `provider = 'dbt'`:

| key | Owner | Content |
| --- | --- | --- |
| `overview`, `project`, `model`, `column`, `dependency`, `skill_use` | shared | Fixed text from code describing each table and that rows carry `project`. Every publish rewrites these identically. |
| `skill/dbt-agents-schema-analyst` | shared | The built-in analyst skill for the destination warehouse (section 9). |
| `project/<project>` | one project | Markdown overview: project, dbt project name, owner, description, environment, repo, git SHA, published time, counts, and an example filter query. |
| `skill/<project>/<path>` | one project | One per skill file; `<path>` is the file path under `skills_dir` without `.md`, using `/`. |

### 5.7 Which manifest content is published

- Models: `nodes` entries with `resource_type == "model"` whose `package_name` is the
  root project, plus — when `include_packages` is true — models from other packages not
  in `exclude_packages`. Ignore the `disabled` section.
- Columns: every entry in each published model's `columns`.
- Dependencies: every entry in each published model's `depends_on.nodes`, including
  edges to sources, seeds, snapshots and unpublished models.
- Output order is deterministic: sort every table's rows by primary key.

---

## 6. Publish algorithm

1. **Load.** Config (section 3), manifest (`--manifest` or
   `<project-dir>/target/manifest.json`), skills. A missing manifest exits 2 with
   "run `dbt parse` first".
2. **Check.** Run the check engine (section 8). Any error-severity finding exits 1
   without connecting.
3. **Plan.** Build every row in memory with a pure function. No I/O.
4. **Connect** with the environment's credentials. Resolve database and schema.
5. **Ensure structure.** `CREATE SCHEMA IF NOT EXISTS`. For each table:
   `CREATE TABLE IF NOT EXISTS` with all columns; then add any missing columns with
   `ALTER TABLE … ADD COLUMN` (this migrates tables created by the dbt Labs tool). Never
   drop a table or column.
6. **Ownership guard.** If `DBT_PROJECT` already holds this `project` with a non-null
   `repo_url` that differs from this run's non-null `repo_url`, exit 4 unless
   `--force-takeover`. This stops two repositories from silently overwriting each other.
7. **Swap.** Replace this project's rows:
   - Project-scoped tables (`DBT_PROJECT`, `DBT_MODEL`, `DBT_COLUMN`,
     `DBT_DEPENDENCY`, `SKILL_USE`): delete rows where `project = :project`, and rows
     where `project IS NULL` (legacy rows from the dbt Labs tool; log how many), then
     insert the plan's rows.
   - `ROOT`: delete `provider = 'dbt'` rows whose key is one of the shared keys,
     `project/<project>`, or starts with `skill/<project>/`; then insert the plan's rows.
   - **Atomicity.** Snowflake, BigQuery and DuckDB: all deletes and inserts run in one
     transaction; any failure rolls back and leaves the previous state intact.
     Databricks (no multi-table transactions): each table is replaced in one atomic
     statement, `INSERT INTO <t> REPLACE WHERE <predicate> SELECT …`, from a staged
     temporary view or table. Writers may stage rows into a temporary table first; the
     swap is the atomic step.
   - Values are always bound as parameters or loaded through the client library, never
     interpolated into SQL text. Identifiers are quoted by one helper per writer.
8. **Report.** Print rows written per table, legacy rows removed, schema, elapsed time.

`--dry-run` stops after step 3 and prints the plan summary and target schema without
connecting.

**Exit codes:** 0 success · 1 check errors · 2 config, manifest or credential error ·
3 warehouse error · 4 ownership conflict.

**Spec version guard.** If `DBT_PROJECT` contains any row with `spec_version` greater
than this tool's spec version, exit 2: an older tool must not write into a newer schema.

---

## 7. CLI contract

Package `dbt-agents-schema`, import name `dbt_agents_schema`, console script
`dbt-agents-schema`. The name differs from the dbt Labs `agents-schema` command on
purpose, so both can be installed side by side. Python 3.11+. Use `argparse` (no CLI
framework dependency).

Common options: `--project-dir PATH` (default `.`), `--config PATH` (default
`<project-dir>/agents_schema.yml`), `--manifest PATH`.

| Command | Purpose |
| --- | --- |
| `check [--parse] [--format text\|json\|github\|gitlab] [--output PATH]` | Validate config, manifest and skills. `--parse` runs `dbt parse` first via subprocess. `github` prints `::error file=…,line=…::` / `::warning …` annotations. `gitlab` writes a GitLab Code Quality JSON report to `--output` (default `gl-code-quality-report.json`) and also prints text. Exit 1 on any error finding. Needs no warehouse access. |
| `publish --env NAME [--target T] [--profiles-dir P] [--dry-run] [--force-takeover]` | Section 6. |
| `unpublish --env NAME [--project P] [--yes]` | Delete one project's rows from the environment's agents schema, including its ROOT rows. Refuses without `--yes` when stdin is not a TTY. |
| `init [--ci github\|gitlab\|none] [--force]` | Write `agents_schema.yml`, `agents/skills/example.md`, and the CI caller file for the chosen platform, plus a `.pre-commit-config.yaml` snippet printed to stdout. Never overwrites without `--force`. |
| `fetch-manifest --account-id ID --job-id ID [--host HOST] [--out PATH]` | Download the latest `manifest.json` from a dbt platform job. Token from `DBT_CLOUD_API_TOKEN`. Default host `cloud.getdbt.com`, default out `target/manifest.json`. Verify the endpoint against current dbt platform Admin API docs before implementing; record the URL used in PROGRESS. |
| `write-parse-profile --out DIR` | Write a minimal `profiles.yml` from `WAREHOUSE_CREDENTIALS` so CI can run `dbt parse` without a separate profiles secret. Profile name = `profile` in `dbt_project.yml`. The output's schema comes from the optional `schema`/`dataset` key in `WAREHOUSE_CREDENTIALS` (section 4.1); exit 2 when it is required and missing. |
| `--version` | Print the package version. |

**Git metadata** for `DBT_PROJECT`: GitHub (`GITHUB_SERVER_URL`/`GITHUB_REPOSITORY`,
`GITHUB_SHA`, `GITHUB_REF_NAME`), GitLab (`CI_PROJECT_URL`, `CI_COMMIT_SHA`,
`CI_COMMIT_REF_NAME`), else `git remote get-url origin` / `git rev-parse` in the
project dir, else null. Strip credentials from remote URLs.

---

## 8. Check engine

A finding has: `rule`, `severity` (error | warning | info), `message`, optional `file`
and `line`, optional `node` (unique_id or skill key). Severity comes from
`check.rules`; `off` drops the finding. Hard errors below cannot be turned off.

**Hard errors (always error)**

| id | Condition |
| --- | --- |
| `config_invalid` | YAML parse error or JSON Schema violation (report the JSON path). |
| `manifest_missing` | No manifest at the resolved path. |
| `project_key_invalid` | Resolved `project` fails the pattern. |
| `skill_key_duplicate` | Two skill files map to the same key, compared case-insensitively. |
| `skill_frontmatter_invalid` | Frontmatter is present but not valid YAML. |

**Configurable rules**

| id | Default | Condition |
| --- | --- | --- |
| `config_missing` | warning | No `agents_schema.yml`. |
| `missing_model_description` | warning | Model in `description_scope` has an empty description. |
| `missing_column_description` | warning | Column of a model in `description_scope` has an empty description. |
| `sampled_values_in_description` | error | A model or column description looks like it contains data profiling output (below). |
| `skill_without_uses` | warning | Skill file has no `uses` frontmatter. |
| `skill_unresolved_uses` | error | A `uses.schemas` entry is not the schema of any published model and not in `external_schemas`; or a `uses.tables` entry does not match any published model's `schema.alias` or `database.schema.alias` (case-insensitive) and its schema is not in `external_schemas`. |

**`sampled_values_in_description` heuristics** (case-insensitive; match any):

1. A statistic name followed by a number: `\b(min|max|mean|avg|average|median|std(dev)?|p\d{2})\s*[:=]\s*-?\d`
2. A percentage tied to nulls or rows: `\d{1,3}(\.\d+)?\s*%\s*(null|nulls|of rows|non-null|distinct|populated)`
3. Distinct or sample value lists: `\b(distinct|unique|sample|example|top)\s+values?\s*[:=(]`
4. Row counts: `\b\d[\d,]{2,}\s+(rows|records)\b`
5. Null statistics: `\bnull\s+(rate|percentage|pct|count)\b`

Tests must include at least three positive and three negative examples per heuristic.
Negatives must include ordinary prose such as "Maximum order value allowed by policy"
and "Percent of revenue recognized".

**Skill frontmatter** (same convention as the dbt Labs tool):

```markdown
---
uses:
  schemas: [FINANCE_MARTS]
  tables: [FINANCE_MARTS.FCT_REVENUE]
---
# Revenue skill
```

---

## 9. Built-in analyst skill

Ship one markdown body per dialect (`snowflake`, `bigquery`, `databricks`, `duckdb`)
under `src/dbt_agents_schema/skills/`. Publish the one matching the destination as
`dbt` / `skill/dbt-agents-schema-analyst`. Each body must:

- Say it is read-only (SELECT only).
- Start discovery at `ROOT`, then `DBT_PROJECT` to see which projects are present.
- Explain that every `DBT_*` row has `project`, and show filtering to one project.
- Show finding a model by name or description, its columns, and recursive upstream and
  downstream lineage over `DBT_DEPENDENCY`, joining back to `DBT_MODEL` on `unique_id`
  across projects.
- Show listing skills (`key LIKE 'skill/%'`) and reading `SKILL_USE`.
- Use only valid SQL for its dialect. A test parses every SQL block with `sqlglot` in
  the matching dialect.

---

## 10. CI wrappers

All wrappers pin a package version, install it, run `dbt deps` and `dbt parse`, then call
the CLI. They contain no publishing logic.

### 10.1 GitHub

- Reusable workflow at `.github/workflows/dbt-agents-schema.yml` (`on: workflow_call`).
  Inputs: `project-dir` (default `.`), `package-version` (required), `python-version`
  (default `3.12`), `dbt-adapter` (required, for example `dbt-snowflake`),
  `dbt-version` (default `>=1.8,<2`), `mode` (`check` | `publish`), `environment`
  (default `prod`). Secrets: `WAREHOUSE_CREDENTIALS` (required for publish),
  `DBT_PROFILES_YML` (optional; when absent, run `write-parse-profile`).
- `check` mode runs `check --format github` so findings annotate the PR.
- Example caller at `examples/github/agents-schema.yml`: `check` on `pull_request`,
  `publish` on `push` to `main`, with `concurrency` set so two publishes of the same
  project never overlap.

### 10.2 GitLab

- Template at `templates/gitlab/dbt-agents-schema.yml` using `spec: inputs` with the
  same inputs as GitHub (snake_case names), except that `mode` is `auto` | `check` |
  `publish` (default `auto`: check on merge requests, publish on the default branch), so
  a single `include` creates both jobs. Usable through `include: remote:` (raw URL
  pinned to a tag), `include: project:`, or copied into the consumer repo. Do not
  depend on the GitLab CI/CD Catalog.
- Jobs: `dbt-agents-schema-check` on merge request pipelines, writing the Code Quality
  report as `artifacts:reports:codequality`; `dbt-agents-schema-publish` on the default
  branch with `resource_group: dbt-agents-schema-$CI_PROJECT_PATH` to serialize
  publishes.
- Credentials from a protected CI/CD variable `WAREHOUSE_CREDENTIALS`, type File
  recommended; the wrapper reads the file and passes its contents to the CLI. GitLab
  cannot mask a multi-line JSON/YAML document, so the template does not rely on
  masking.
- Example caller at `examples/gitlab/.gitlab-ci.yml`.

### 10.3 Pre-commit

`.pre-commit-hooks.yaml` at the repo root defines hook id `dbt-agents-schema-check`:
`language: python`, `entry: dbt-agents-schema check --parse`, `pass_filenames: false`,
`files: ^(models/|agents/|agents_schema\.yml|dbt_project\.yml)`.

### 10.4 Validation

Workflow and template files are validated in `make verify` with `check-jsonschema`
(`--builtin-schema vendor.github-workflows` and `vendor.gitlab-ci`), plus `actionlint`
when it is installed.

---

## 11. Versioning

- Package: SemVer, starting at `0.1.0`.
- Spec: integer `spec_version`, currently `1`, written to every `DBT_PROJECT` row.
- Adding a nullable column is a minor change. Renaming or removing anything requires a
  spec version bump and a migration note.
