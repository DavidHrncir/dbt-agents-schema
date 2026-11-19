# dbt-agents-schema — Build Specification (spec v1)

This is the contract. Code, tests and docs must match it. If something here is wrong or
impossible, do not silently diverge: record a **SPEC CHANGE PROPOSAL** in
`dev/PROGRESS.md` and stop that task (see `CLAUDE.md`).

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
    parse_target:                # optional; production shape for `dbt parse` in CI (no default)
      type: snowflake            # snowflake | bigquery | databricks | duckdb; required
      name: prod                 # profile target name; default: the environment name
      database: ANALYTICS        # Snowflake database, BigQuery project, Databricks catalog; required except duckdb
      schema: FINANCE            # dbt target schema (BigQuery dataset); required
      location: US               # BigQuery only; optional
  dev:
    credentials: dbt_profile
    schema: "{{ target_schema }}_AGENTS"
check:
  description_scope: public      # public | all — which models the description rules apply to
  external_schemas: []           # schemas skills may reference that dbt does not build
  measured_values:               # settings for measured_values_in_context (section 8)
    min_number: 10               # numbers below this are never flagged
    allow: []                    # regexes; a matching line may contain numbers
    exempt: []                   # regexes; a matching line may contain count phrases
    count_nouns: []              # extra regex nouns for count phrases, e.g. "grants?"
  rules:                         # severity per rule: error | warning | off
    config_missing: warning
    missing_model_description: warning
    missing_column_description: warning
    sampled_values_in_description: error
    measured_values_in_context: warning
    manifest_target_mismatch: error
    manifest_empty: error
    column_meta_missing: off
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
- `parse_target`: plain strings, no templates; unknown keys are a config error. It does
  not change where the agents schema is written. `write-parse-profile --env` uses it to
  build a profile for `dbt parse` (section 7), and `manifest_target_mismatch` checks the
  parsed models against it (section 8).
- `measured_values`: `min_number` is an integer ≥ 0. Every entry of `allow`, `exempt`
  and `count_nouns` must compile as a Python regular expression; an invalid one is
  `config_invalid`, naming the key and list index.

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
2. **Check.** Run the check engine (section 8), with the environment-dependent rules for
   `--env`. Any error-severity finding exits 1 without connecting.
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
| `check [--env NAME] [--parse] [--format text\|json\|github\|gitlab] [--output PATH]` | Validate config, manifest and skills. `--env` turns on the environment-dependent rules (`manifest_target_mismatch`, section 8) for that environment; `publish --env` turns them on too. `--parse` runs `dbt parse` first via subprocess. `github` prints `::error file=…,line=…::` / `::warning …` annotations. `gitlab` writes a GitLab Code Quality JSON report to `--output` (default `gl-code-quality-report.json`) and also prints text. Exit 1 on any error finding. Needs no warehouse access. |
| `publish --env NAME [--target T] [--profiles-dir P] [--dry-run] [--force-takeover]` | Section 6. |
| `unpublish --env NAME [--project P] [--yes]` | Delete one project's rows from the environment's agents schema, including its ROOT rows. Refuses without `--yes` when stdin is not a TTY. |
| `init [--ci github\|gitlab\|none] [--force]` | Write `agents_schema.yml`, `agents/skills/example.md`, and the CI caller file for the chosen platform, plus a `.pre-commit-config.yaml` snippet printed to stdout. Never overwrites without `--force`. |
| `fetch-manifest --account-id ID --job-id ID [--host HOST] [--out PATH]` | Download the latest `manifest.json` from a dbt platform job. Token from `DBT_CLOUD_API_TOKEN`. Default host `cloud.getdbt.com`, default out `target/manifest.json`. Verify the endpoint against current dbt platform Admin API docs before implementing; record the URL used in PROGRESS. |
| `write-parse-profile --out DIR [--env NAME]` | Write a minimal `profiles.yml` so CI can run `dbt parse` without a separate profiles secret. Profile name = `profile` in `dbt_project.yml`. File mode 0600. **With `--env` naming an environment that has `parse_target`:** target = `parse_target.name`, database/schema/location from `parse_target`, and connection fields filled with obvious placeholders (BigQuery `method: service-account-json` with a dummy key, Snowflake `password`, Databricks `token`, DuckDB `path: ":memory:"`); `WAREHOUSE_CREDENTIALS` is not read. `dbt parse` never connects, so no real credential is needed. **Otherwise:** the profile is built from `WAREHOUSE_CREDENTIALS`, and the output's schema comes from the optional `schema`/`dataset` key in it (section 4.1); exit 2 when it is required and missing. |
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
| `measured_values_in_context` | warning | Published text contains a measured value (below). |
| `manifest_target_mismatch` | error | Only with `--env` and that environment's `parse_target`. A published model's `database` differs from `parse_target.database`, or its `schema` does not start with `parse_target.schema` (both case-insensitive; the database comparison is skipped when `parse_target.database` is unset). One finding per model, naming the model, its resolved `database.schema` and the expected values. |
| `manifest_empty` | error | The manifest has no published models. |
| `column_meta_missing` | off | No column of any published model has a non-empty `meta`. Projects that rely on dbt Fusion copying model `meta` to columns (dbt-core does not) set it to `error`. |
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

**`measured_values_in_context`.** Published text must describe method, not
measurements: a count written into a description goes stale, and an agent quotes it as
current instead of querying. This rule is separate from `sampled_values_in_description`;
both may report the same line.

- Scope: the description and `meta` of each published model, the description and `meta`
  of each of its columns, and the full text of each skill file. `meta` is scanned as JSON
  with sorted keys.
- Each line is stripped of surrounding whitespace and one leading list marker
  (`^\d+[.)]\s+`), then checked in order:
  1. **Count phrase** → finding, unless the line matches an `exempt` pattern. Count
     phrases: `\d[\d,]*\s*(<nouns>)\b` with built-in nouns
     `rows?|records?|tables?|columns?|events?|jobs?|users?|customers?|accounts?|orders?|transactions?`
     plus `count_nouns`; `\b\d[\d,]*\s+of\s+\d`; `\b\d+(\.\d+)?x\b`;
     `\b\d+(\.\d+)?[KMB]\b` (suffix case-sensitive). Built-in exemption:
     `\b1 (row|record) per\b`.
  2. Otherwise a line matching a built-in or configured `allow` pattern is skipped.
     Built-in: `[<>]=\s*\d`, `\b0-100\b`, `\bone (row|record) per\b`, `\b1:1\b`,
     `accepted values`, `percentile`, `\bp\d{2}\b`, and standard identifiers
     `\b(ISO|RFC|IEC|IEEE)[ -]?\d+`.
  3. Otherwise any number (`(?<![\w.\-])\d[\d,]*(\.\d+)?`, commas removed) with value
     ≥ `min_number` is a finding.
- Matching is case-insensitive except where noted. At most one finding per line. The
  message gives the matched text, the first 100 characters of the line, and the fix:
  use qualitative wording or the query that derives the value, or add a pattern to
  `check.measured_values.allow` / `exempt`. Skill findings carry `file` and `line`; model
  and column findings carry `node`.
- Tests must include at least three positive and three negative examples per step, and
  these cases: "(94 rows)" on a line that also matches an `allow` pattern is flagged;
  "10. Never name a model" is not; "one row per order" and "1 row per order" are not;
  "older than 2x the connection's sync_frequency" passes only with an `exempt` pattern;
  numbers below `min_number` are not flagged; "ISO 4217 currency code" is not flagged.

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

All wrappers install a pinned dbt-agents-schema (`package-version`, or a pinned
`package-spec`) and a pinned dbt engine, run `dbt deps` and `dbt parse
--no-partial-parse`, then call the CLI. They contain no publishing logic.

### 10.1 GitHub

- Reusable workflow at `.github/workflows/dbt-agents-schema.yml` (`on: workflow_call`).
  Inputs:
  - `project-dir` (default `.`), `python-version` (default `3.12`), `mode` (`check` |
    `publish`), `environment` (default `prod`).
  - `warehouse` (`snowflake` | `bigquery` | `databricks` | `duckdb`): selects the
    package extra and, with dbt-core, the adapter. Required unless `dbt-adapter` is
    given, in which case it is derived from it (v0.1 callers keep working).
  - `dbt-engine` (`fusion` | `core`, default `fusion`). Fusion is installed with the
    official installer, pinned to `dbt-fusion-version` (default: the version this
    release was tested with, recorded in CHANGELOG). `core` installs
    `dbt-core<dbt-version>` (default `>=1.8,<2`) and `dbt-adapter` (default
    `dbt-<warehouse>`).
  - `package-version`: required unless `package-spec` is set. `package-spec`
    (optional): a full pip requirement, such as a git URL pinned to a commit; when set it
    replaces `package-version`.
  - `parse-env` (optional): lines of `KEY=VALUE` exported to every later step. Blank
    lines and `#` lines are ignored. Keys must match `^[A-Z_][A-Z0-9_]*$` and must not
    be `WAREHOUSE_CREDENTIALS`, `DBT_PROFILES_YML`, `DBT_PROFILES_DIR`,
    `PACKAGE_REPO_TOKEN`, or start with `GITHUB_` or `RUNNER_`. Not for secrets: values
    are not masked.
- Secrets: `WAREHOUSE_CREDENTIALS` (required for publish), `DBT_PROFILES_YML`
  (optional), `PACKAGE_REPO_TOKEN` (optional: read-only token for installing
  `package-spec` from a private git repository; given only to the install step, through
  a git `insteadOf` rule that the same step removes, never written inside the checkout).
- Steps, in order: validate inputs → checkout → set up Python → install the dbt engine
  and the package → profile (`DBT_PROFILES_YML` when set, else `write-parse-profile
  --env <environment>`) → `dbt deps` → `dbt parse --no-partial-parse` → check mode:
  `check --env <environment> --format github`, so findings annotate the PR; publish
  mode: `publish --env <environment> --dry-run`, then `publish --env <environment>`.
- Example caller at `examples/github/agents-schema.yml` (`init --ci github` writes the
  same file): triggers `workflow_dispatch`, plus `pull_request` and `push` to `main`
  filtered by `paths` (`models/**`, `macros/**`, the skills directory,
  `dbt_project.yml`, `packages.yml`, `package-lock.yml`, `agents_schema.yml`, and the
  caller file itself); one job whose `mode` is `check` on pull requests and `publish`
  otherwise; `concurrency` per pull request ref, and one shared, queued
  (`cancel-in-progress: false`) group for publishes, so two publishes of the same
  project never overlap.

### 10.2 GitLab

- Template at `templates/gitlab/dbt-agents-schema.yml` using `spec: inputs` with the
  same inputs as GitHub (snake_case names), except that `mode` is `auto` | `check` |
  `publish` (default `auto`: check on merge requests, publish on the default branch), so
  a single `include` creates both jobs. Usable through `include: remote:` (raw URL
  pinned to a tag), `include: project:`, or copied into the consumer repo. Do not
  depend on the GitLab CI/CD Catalog.
- Includes `warehouse`, `dbt_engine`, `dbt_fusion_version` and `package_spec` with the
  same meaning and defaults as 10.1, and the same profile, parse, check (`--env`) and
  dry-run steps. No `parse_env` input: included jobs already see the project's CI/CD
  variables.
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
