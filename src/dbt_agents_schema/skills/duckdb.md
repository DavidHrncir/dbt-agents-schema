# dbt agents schema analyst (duckdb)

<!-- Placeholder body from T07. T21 replaces it with the full analyst skill (SPEC 9). -->

Read-only: run SELECT statements only. Start discovery at `ROOT`, then `DBT_PROJECT`
to see which dbt projects are present. Every `DBT_*` row carries `project`.

```sql
SELECT key, content FROM ROOT WHERE provider = 'dbt' ORDER BY key;
```
