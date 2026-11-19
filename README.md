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

Many dbt projects can share one agents schema. Every row carries a `project` key, and
each publish replaces only its own project's rows, so finance, marketing and any other
project publish side by side without overwriting each other. The table and column names
match the dbt Labs `agents_schema` tool, so agents built for it keep working and now see
every project.

- **Warehouses:** Snowflake, BigQuery, Databricks; DuckDB for local use and demos.
- **dbt:** dbt v1 (dbt-core), dbt v2 (Fusion) and dbt platform projects. It reads only
  `target/manifest.json`.
- **CI:** a reusable GitHub Actions workflow and a GitLab CI template. Pull requests run
  checks that annotate the diff; merges to `main` publish.
- **Checks:** missing descriptions, sample data or row counts written into descriptions,
  skills that reference tables that don't exist, and a manifest parsed against the wrong
  target. Each rule's severity is configurable.

## Quick start

Install from a release tag, with the extra for your warehouse:

```bash
pip install "dbt-agents-schema[snowflake] @ git+https://github.com/DavidHrncir/dbt-agents-schema@v0.2.2"
# or [bigquery], [databricks], [duckdb]
```

In your dbt project root (next to `dbt_project.yml`):

```bash
dbt-agents-schema init --ci github    # writes agents_schema.yml, an example skill and the CI caller
dbt parse
dbt-agents-schema check               # validate config, manifest and skills
dbt-agents-schema publish --env dev --dry-run
dbt-agents-schema publish --env dev   # writes to your own <target schema>_AGENTS schema
```

For CI, add a `parse_target` (your production database and schema) to the `prod`
environment in `agents_schema.yml`, set the `WAREHOUSE_CREDENTIALS` secret, and commit.
Pull requests then run the checks, and merges publish to the shared `prod` agents schema.
The partner guide's GitHub and GitLab setup sections walk through both steps.

## Documentation

The **[partner guide](docs/partner-guide.md)** covers everything needed to run it:

- install and configuration reference (`agents_schema.yml`)
- GitHub and GitLab setup, and the production parse target
- the developer workflow and dbt platform projects
- what gets published: every table and column, with example queries
- migrating from the dbt Labs tool, and warehouse permissions
- troubleshooting by exit code

Release notes are in [CHANGELOG.md](CHANGELOG.md).

## Try it locally

No warehouse needed. This publishes two example projects into a DuckDB file and queries
them:

```bash
git clone https://github.com/DavidHrncir/dbt-agents-schema && cd dbt-agents-schema
make install
bash scripts/demo_duckdb.sh
```

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
