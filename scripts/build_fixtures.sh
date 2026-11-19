#!/usr/bin/env bash
# Rebuild tests/fixtures/manifests/*.json from the fixture dbt projects.
# Installs dbt-core + dbt-duckdb into a throwaway venv (needs network), runs `dbt deps`
# and `dbt parse` for each project, and copies the manifests with local paths scrubbed.
# Tests read only the committed manifests and never need dbt.
# Optional env: DBT_CORE_SPEC (default "dbt-core>=1.10,<2"),
#               DBT_DUCKDB_SPEC (default "dbt-duckdb>=1.9,<2"), FIXTURE_PYTHON (default 3.11)
set -euo pipefail
cd "$(dirname "$0")/.."

root="$(pwd)"
fixtures="${root}/tests/fixtures"
projects=(shared_pkg finance finance_v2 marketing bad)

work="$(mktemp -d)"
trap 'rm -rf "${work}"' EXIT

uv venv --quiet --python "${FIXTURE_PYTHON:-3.11}" "${work}/venv"
uv pip install --quiet --python "${work}/venv/bin/python" \
  "${DBT_CORE_SPEC:-dbt-core>=1.10,<2}" "${DBT_DUCKDB_SPEC:-dbt-duckdb>=1.9,<2}"
dbt="${work}/venv/bin/dbt"
"${dbt}" --version

export DBT_SEND_ANONYMOUS_USAGE_STATS=false
export DBT_PROFILES_DIR="${fixtures}"

mkdir -p "${fixtures}/manifests"
for name in "${projects[@]}"; do
  # Parse a copy so the fixture tree never gets target/, dbt_packages/ or lock files.
  src="${fixtures}/projects/${name}"
  dst="${work}/projects/${name}"
  mkdir -p "${work}/projects"
  rm -rf "${dst}"
  cp -R "${src}" "${dst}"
  echo "== ${name}"
  if [[ -f "${dst}/packages.yml" ]]; then
    # packages.yml uses `local: ../shared_pkg`, so the sibling must exist in the copy too.
    [[ -d "${work}/projects/shared_pkg" ]] || cp -R "${fixtures}/projects/shared_pkg" "${work}/projects/shared_pkg"
    (cd "${dst}" && "${dbt}" deps --project-dir . --quiet)
  fi
  (cd "${dst}" && "${dbt}" parse --project-dir . --no-partial-parse --quiet)
  "${work}/venv/bin/python" "${root}/scripts/scrub_manifest.py" \
    "${dst}/target/manifest.json" "${work}/projects" \
    "${fixtures}/manifests/${name}.json"
done
echo "Wrote ${#projects[@]} manifests to tests/fixtures/manifests/"
