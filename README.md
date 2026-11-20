# dbt-agents-schema

> **Personal project, temporary.** This is David Hrncir's personal repository. It is not
> an official Fivetran or dbt Labs product, and neither company supports or maintains it.
> It is a stopgap: it may be archived or removed from public view at any time, for
> example once an official replacement is available. If you depend on it, pin a release
> tag (such as `@v0.2.2`) and keep your own fork. Provided "as is" under the
> [MIT License](LICENSE), with no warranty.

Publishes dbt project metadata (models, columns, lineage and markdown skills) into an
**agents schema** in your warehouse, so AI agents can find and query your data with
plain SQL.

You add it to your existing dbt project. It sets up the config and the CI job, checks
your project's metadata on every pull request, and publishes it to the agents schema on
every merge. You choose the warehouse and the schema name.

- **Warehouses:** Snowflake, BigQuery or Databricks.
- **dbt:** dbt v1 (dbt-core), dbt v2 (Fusion) and dbt platform projects. It reads only
  `target/manifest.json`.
- **CI:** a reusable GitHub Actions workflow and a GitLab CI template. Pull requests run
  checks that annotate the diff; merges to `main` publish.
- **Checks:** missing descriptions, sample data or row counts written into descriptions,
  skills that reference tables that don't exist, and a manifest parsed against the wrong
  target. Each rule's severity is configurable.
- **Many projects, one schema:** each publish replaces only its own project's rows, so
  several dbt projects can share one agents schema.

## Quick start

**1. Install.** One package for every warehouse. The name in brackets adds the driver
for the warehouse you publish to:

```bash
pip install "dbt-agents-schema[snowflake] @ git+https://github.com/DavidHrncir/dbt-agents-schema@v0.2.2"
# or [bigquery] or [databricks]
```

**2. Set up your dbt project.** In the project root (next to `dbt_project.yml`):

```bash
dbt-agents-schema init --ci github    # or --ci gitlab; writes agents_schema.yml, an example skill and the CI job
dbt parse
dbt-agents-schema check               # validate config, manifest and skills
```

**3. Try a publish to your own development schema** (optional):

```bash
dbt-agents-schema publish --env dev --dry-run   # shows what would be written; connects to nothing
dbt-agents-schema publish --env dev
```

**4. Turn on CI.** In `agents_schema.yml`, pick the `prod` schema name (default `AGENTS`), add
a `parse_target` (your production database and schema), add a `WAREHOUSE_CREDENTIALS`
secret to the repository, and commit. Pull requests then run the checks, and merges to
`main` publish. CI installs the package by itself.

## Documentation

The **[implementation guide](docs/implementation-guide.md)** covers:

- install and configuration reference (`agents_schema.yml`)
- GitHub and GitLab setup, and the production parse target
- the developer workflow and dbt platform projects
- what gets published: every table and column, with example queries
- warehouse permissions
- troubleshooting by exit code

Release notes are in [CHANGELOG.md](CHANGELOG.md).

## Try it without a warehouse

Optional. Two demos publish sample projects to a local DuckDB file, so you can see what
the tool does before wiring it into your own project:

```bash
git clone https://github.com/DavidHrncir/dbt-agents-schema && cd dbt-agents-schema
make install
bash scripts/demo_duckdb.sh          # publish two projects, then query them as an agent would
bash scripts/demo_multi_project.sh   # check the sharing guarantees step by step
```

`demo_duckdb.sh` publishes a finance and a marketing project to `prod`, and finance to a
developer's own schema. It then reads the built-in analyst skill from the schema and runs
three of its queries, including lineage that crosses both projects.

`demo_multi_project.sh` walks through what makes sharing safe, and fails if any of it
does not hold:

1. A dry run connects to nothing.
2. Republishing one project leaves the other's rows byte-identical.
3. A second repository publishing under the same project key is refused (exit 4).
4. Unpublishing one project leaves the rest untouched.

## Development

```bash
make install   # create the environment (uv)
make verify    # lint, typecheck, tests and CI-file validation
```

The [`dev/`](dev/README.md) folder, `CLAUDE.md` and `.claude/` hold the specification,
plan and build log used to develop this project with Claude Code. They are not needed
to use it.

## License

[MIT](LICENSE)
