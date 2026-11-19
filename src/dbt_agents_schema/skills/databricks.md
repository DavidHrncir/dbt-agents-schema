# dbt agents schema analyst (databricks)

Use this skill to answer questions about the dbt projects published into this agents
schema: which projects exist, what their models and columns mean, how models depend on
each other (also across projects), and which project skills apply.

**Read-only.** Run `SELECT` statements only. Never `INSERT`, `UPDATE`, `DELETE`,
`MERGE`, `CREATE`, `ALTER` or `DROP` anything in this schema; publishing is done by CI.

Set the agents schema as current schema first: `USE CATALOG <catalog>;` then
`USE SCHEMA <agents schema>;`. Recursive CTEs need Databricks Runtime 17.0 or later or a
current SQL warehouse.

The example values (`'finance'`, `'revenue'`, `'model.finance.fct_revenue'`,
`'model.shared_pkg.dim_calendar'`, `'skill/finance/revenue_overview'`) are placeholders.
Replace them with values you found in the earlier steps.

## 1. Start at ROOT

`ROOT` holds the description of every table and one overview per project
(`project/<project>`). Read it first.

```sql
SELECT key, content
FROM ROOT
WHERE provider = 'dbt'
ORDER BY key;
```

## 2. See which projects are present

Many dbt projects share this schema. `DBT_PROJECT` has one row per project.

```sql
SELECT project, dbt_project_name, owner, description, environment, repo_url,
       git_sha, published_at, model_count, column_count
FROM DBT_PROJECT
ORDER BY project;
```

Read one project's overview:

```sql
SELECT content
FROM ROOT
WHERE provider = 'dbt' AND key = 'project/' || 'finance';
```

## 3. Filter to one project

Every row in every `DBT_*` table carries `project`. Filter on it whenever the question is
about one project; leave it out to search across all of them.

```sql
SELECT name, materialization, access, relation_name, description
FROM DBT_MODEL
WHERE project = 'finance'
ORDER BY name;
```

## 4. Find a model by name or description

```sql
SELECT project, unique_id, name, relation_name, description
FROM DBT_MODEL
WHERE LOWER(name) LIKE '%revenue%'
   OR LOWER(description) LIKE '%revenue%'
ORDER BY project, name;
```

Use `relation_name` (the fully qualified table or view) when you query the model's data.
`tags` and `meta` hold extra context: `tags` and `meta` are JSON text in `STRING` columns. Read a key with `get_json_object(meta, '$.<key>')` and expand an array with `explode(from_json(tags, 'ARRAY<STRING>'))`.

```sql
SELECT name, tags, get_json_object(meta, '$.owner') AS meta_owner
FROM DBT_MODEL
WHERE project = 'finance'
ORDER BY name;
```

## 5. Read a model's columns

`DBT_COLUMN.model_id` is the model's `unique_id`. Join on `project` too.

```sql
SELECT c.column_name, c.data_type, c.description
FROM DBT_COLUMN AS c
JOIN DBT_MODEL AS m
  ON m.project = c.project AND m.unique_id = c.model_id
WHERE m.project = 'finance' AND m.name = 'fct_revenue'
ORDER BY c.column_name;
```

## 6. Lineage

`DBT_DEPENDENCY` has one row per edge, `upstream_id` → `downstream_id`. Upstream ids can
be models, sources, seeds or snapshots (`upstream_type`). Walk edges by `unique_id`
without filtering on `project`, so lineage crosses projects; then join back to
`DBT_MODEL` on `unique_id` to see which project publishes each model. Nodes that are not
published models (sources, seeds) have no `DBT_MODEL` row.

Everything upstream of a model:

```sql
WITH RECURSIVE upstream AS (
  SELECT d.upstream_id AS unique_id, d.upstream_type AS node_type, 1 AS depth
  FROM DBT_DEPENDENCY AS d
  WHERE d.downstream_id = 'model.finance.fct_revenue'
  UNION ALL
  SELECT d.upstream_id AS unique_id, d.upstream_type AS node_type, u.depth + 1 AS depth
  FROM DBT_DEPENDENCY AS d
  JOIN upstream AS u ON d.downstream_id = u.unique_id
  WHERE u.depth < 20
)
SELECT DISTINCT u.unique_id, u.node_type, u.depth, m.project, m.name, m.description
FROM upstream AS u
LEFT JOIN DBT_MODEL AS m ON m.unique_id = u.unique_id
ORDER BY u.depth, u.unique_id;
```

Everything downstream of a model, in any project:

```sql
WITH RECURSIVE downstream AS (
  SELECT d.downstream_id AS unique_id, 1 AS depth
  FROM DBT_DEPENDENCY AS d
  WHERE d.upstream_id = 'model.shared_pkg.dim_calendar'
  UNION ALL
  SELECT d.downstream_id AS unique_id, w.depth + 1 AS depth
  FROM DBT_DEPENDENCY AS d
  JOIN downstream AS w ON d.upstream_id = w.unique_id
  WHERE w.depth < 20
)
SELECT DISTINCT w.unique_id, w.depth, m.project, m.name, m.description
FROM downstream AS w
JOIN DBT_MODEL AS m ON m.unique_id = w.unique_id
ORDER BY w.depth, m.project, w.unique_id;
```

## 7. Skills

Projects publish their own skills as `ROOT` rows keyed `skill/<project>/<path>`. List
them, then read the ones that match the question:

```sql
SELECT key
FROM ROOT
WHERE provider = 'dbt' AND key LIKE 'skill/%'
ORDER BY key;
```

```sql
SELECT content
FROM ROOT
WHERE provider = 'dbt' AND key = 'skill/finance/revenue_overview';
```

`SKILL_USE` records which schemas and tables each skill is about (`use_kind` is `schema`
or `table`). Use it to find the skills that apply to the objects you are working with:

```sql
SELECT project, skill_key, use_kind, object_ref
FROM SKILL_USE
ORDER BY project, skill_key, use_kind, object_ref;
```
