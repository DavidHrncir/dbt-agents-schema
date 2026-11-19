#!/usr/bin/env bash
# Multi-project demo on DuckDB: the guarantees that let many dbt projects share one
# agents schema, shown with the real CLI and checked as it goes. No warehouse, no network.
#
#   make install && bash scripts/demo_multi_project.sh
#
# 1. publish --dry-run plans every row and connects to nothing
# 2. finance and marketing publish into one AGENTS schema
# 3. finance republishes a new version; marketing's rows are byte-identical
# 4. another repository publishing as `finance` is refused (exit 4)
# 5. unpublishing marketing leaves finance's rows byte-identical
#
# "Byte-identical" is measured with a fingerprint: one MD5 over every row a project
# owns in all six tables. The script exits non-zero if any guarantee does not hold.
#
# The DuckDB file goes to $DEMO_DIR (default: a new temporary directory) as
# multi_project.duckdb. Nothing is written inside the checkout.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FIXTURES="$REPO/tests/fixtures"
DEMO_DIR="${DEMO_DIR:-$(TMP="${TMPDIR:-/tmp}"; mktemp -d "${TMP%/}/dbt-agents-schema-demo.XXXXXX")}"
mkdir -p "$DEMO_DIR"
DB="$DEMO_DIR/multi_project.duckdb"
rm -f "$DB" "$DB.wal"

export WAREHOUSE_CREDENTIALS="{\"type\": \"duckdb\", \"path\": \"$DB\"}"
# The ownership guard compares the repository each publish comes from. Set it
# explicitly so the demo behaves the same in any checkout, fork or CI runner.
export GITHUB_REPOSITORY="example/analytics-dbt"
unset GITHUB_SERVER_URL CI_PROJECT_URL

cli() { uv run --project "$REPO" --quiet dbt-agents-schema "$@"; }
publish() {
  cli publish --env prod \
    --project-dir "$FIXTURES/projects/$1" \
    --manifest "$FIXTURES/manifests/$1.json" "${@:2}"
}
fail() { echo "FAILED: $*" >&2; exit 1; }

# Run one SQL statement against the agents schema and print the result as a table.
sql() {
  uv run --project "$REPO" --quiet python - "$DB" "$1" <<'PY'
import sys

import duckdb

con = duckdb.connect(sys.argv[1], read_only=True)
con.execute('SET search_path = "AGENTS"')
cursor = con.execute(sys.argv[2])
rows = cursor.fetchall()
print("  " + " | ".join(d[0] for d in cursor.description))
for row in rows:
    print("  " + " | ".join("" if v is None else str(v) for v in row))
PY
}

# One MD5 over every row a project owns, in all six tables. Any change changes it.
fingerprint() {
  sql "SELECT md5(string_agg(r, '|' ORDER BY r)) AS fingerprint FROM (
    SELECT 'P' || CAST(p AS VARCHAR) AS r FROM DBT_PROJECT p WHERE project = '$1'
    UNION ALL SELECT 'M' || CAST(m AS VARCHAR) FROM DBT_MODEL m WHERE project = '$1'
    UNION ALL SELECT 'C' || CAST(c AS VARCHAR) FROM DBT_COLUMN c WHERE project = '$1'
    UNION ALL SELECT 'D' || CAST(d AS VARCHAR) FROM DBT_DEPENDENCY d WHERE project = '$1'
    UNION ALL SELECT 'S' || CAST(s AS VARCHAR) FROM SKILL_USE s WHERE project = '$1'
    UNION ALL SELECT 'R' || CAST(x AS VARCHAR) FROM ROOT x
      WHERE key = 'project/$1' OR key LIKE 'skill/$1/%'
  )" | tail -n 1 | tr -d ' '
}

projects() {
  sql "SELECT project, model_count, column_count, dependency_count, skill_count
       FROM DBT_PROJECT ORDER BY project"
}

echo "dbt-agents-schema $(cli --version)"
echo "DuckDB file: $DB"

echo
echo "== 1. dry run: plan every row, connect to nothing"
publish finance --dry-run
[[ ! -e "$DB" ]] || fail "the dry run created $DB"
echo "  ok: no DuckDB file was created"

echo
echo "== 2. publish finance and marketing into one AGENTS schema"
publish finance
publish marketing
projects
marketing_before="$(fingerprint marketing)"
echo "  marketing fingerprint: $marketing_before"

echo
echo "== 3. finance publishes a new version (finance_v2: a model removed, a column added, a skill deleted)"
publish finance_v2
projects
sql "SELECT key FROM ROOT WHERE key LIKE 'skill/%' ORDER BY key"
marketing_after="$(fingerprint marketing)"
echo "  marketing fingerprint: $marketing_after"
[[ "$marketing_after" == "$marketing_before" ]] || fail "marketing's rows changed"
echo "  ok: marketing's rows are byte-identical"

echo
echo "== 4. another repository tries to publish as 'finance'"
status=0
GITHUB_REPOSITORY="example/other-repo" publish finance || status=$?
echo "  exit code: $status"
[[ "$status" -eq 4 ]] || fail "expected exit 4 (ownership conflict), got $status"
sql "SELECT project, repo_url FROM DBT_PROJECT ORDER BY project"
echo "  ok: refused, and finance still belongs to example/analytics-dbt"

echo
echo "== 5. unpublish marketing; finance stays untouched"
finance_before="$(fingerprint finance)"
echo "  finance fingerprint: $finance_before"
cli unpublish --env prod --project-dir "$FIXTURES/projects/marketing" --yes
projects
sql "SELECT key FROM ROOT ORDER BY key"
finance_after="$(fingerprint finance)"
echo "  finance fingerprint: $finance_after"
[[ "$finance_after" == "$finance_before" ]] || fail "finance's rows changed"
echo "  ok: finance's rows are byte-identical"

echo
echo "Demo complete: every guarantee held. Explore it with: duckdb $DB"
