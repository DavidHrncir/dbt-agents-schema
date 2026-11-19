#!/usr/bin/env bash
# End-to-end demo on DuckDB (PLAN T28). No warehouse, no network, no dbt run needed.
#
#   make install && bash scripts/demo_duckdb.sh
#
# 1. check the finance and marketing fixture projects
# 2. publish finance and marketing to `prod` (credentials: env) and finance to `dev`
#    (credentials: dbt_profile) in one DuckDB file
# 3. read the built-in analyst skill back out of ROOT and run three of its queries
#
# The DuckDB file goes to $DEMO_DIR (default: a new temporary directory) as demo.duckdb.
# Nothing is written inside the checkout. The fixture manifests are checked in, so the
# demo passes --manifest instead of running `dbt parse`.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURES="$REPO/tests/fixtures"
DEMO_DIR="${DEMO_DIR:-$(TMP="${TMPDIR:-/tmp}"; mktemp -d "${TMP%/}/dbt-agents-schema-demo.XXXXXX")}"
mkdir -p "$DEMO_DIR"
DB="$DEMO_DIR/demo.duckdb"
rm -f "$DB" "$DB.wal"

cli() { uv run --project "$REPO" --quiet dbt-agents-schema "$@"; }

echo "dbt-agents-schema $(cli --version)"
echo "DuckDB file: $DB"

for project in finance marketing; do
  echo
  echo "== check $project"
  cli check \
    --project-dir "$FIXTURES/projects/$project" \
    --manifest "$FIXTURES/manifests/$project.json"
done

# prod: credentials: env reads WAREHOUSE_CREDENTIALS (SPEC 4.1).
export WAREHOUSE_CREDENTIALS="{\"type\": \"duckdb\", \"path\": \"$DB\"}"
for project in finance marketing; do
  echo
  echo "== publish $project -> prod"
  cli publish --env prod \
    --project-dir "$FIXTURES/projects/$project" \
    --manifest "$FIXTURES/manifests/$project.json"
done
unset WAREHOUSE_CREDENTIALS

# dev: credentials: dbt_profile reads the fixture profiles.yml, whose DuckDB path comes
# from FIXTURE_DUCKDB_PATH; the schema renders to "{{ target_schema }}_AGENTS".
echo
echo "== publish finance -> dev"
FIXTURE_DUCKDB_PATH="$DB" cli publish --env dev \
  --project-dir "$FIXTURES/projects/finance" \
  --manifest "$FIXTURES/manifests/finance.json" \
  --profiles-dir "$FIXTURES"

echo
uv run --project "$REPO" --quiet python - "$DB" <<'PY'
"""Act as an agent: read the analyst skill from ROOT and run three of its queries."""

import re
import sys

import duckdb

con = duckdb.connect(sys.argv[1], read_only=True)


def projects(schema: str) -> str:
    rows = con.execute(f'SELECT project FROM "{schema}"."DBT_PROJECT" ORDER BY project')
    return ", ".join(r[0] for r in rows.fetchall())


print(f"prod projects: {projects('AGENTS')}")
print(f"dev projects: {projects('finance_AGENTS')}")

con.execute("SET search_path = 'AGENTS'")
(skill,) = con.execute(
    "SELECT content FROM ROOT WHERE provider = 'dbt' AND key = ?",
    ["skill/dbt-agents-schema-analyst"],
).fetchone()
blocks = re.findall(r"```sql\n(.*?)```", skill, re.S)

wanted = [
    ("which projects share this schema", "FROM DBT_PROJECT"),
    ("downstream of model.shared_pkg.dim_calendar, across projects", "WITH RECURSIVE downstream"),
    ("skills published by projects", "key LIKE 'skill/%'"),
]
for number, (title, marker) in enumerate(wanted, start=1):
    sql = next(b for b in blocks if marker in b)
    cursor = con.execute(sql)
    names = [d[0] for d in cursor.description]
    rows = cursor.fetchall()
    print(f"\n== analyst query {number}: {title}")
    print(sql.strip())
    print("--")
    print(" | ".join(names))
    for row in rows:
        print(" | ".join("" if v is None else str(v) for v in row))
    print(f"({len(rows)} rows)")
    if not rows:
        sys.exit(f"analyst query {number} returned no rows")
PY

echo
echo "Demo complete. Explore it with: duckdb $DB"
