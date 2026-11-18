#!/usr/bin/env bash
# Run phases in order, each in a fresh headless session. Stops at the first phase that
# leaves blocked tasks or a failing `make verify`, so a human can step in.
# Usage: scripts/run-all.sh [first-phase] [last-phase]   (default 0 5)
set -euo pipefail
cd "$(dirname "$0")/.."

first="${1:-0}"
last="${2:-5}"

for phase in $(seq "${first}" "${last}"); do
  bash scripts/run-phase.sh "${phase}"

  if grep -q "^- \[!\]" docs/PLAN.md; then
    echo "Blocked tasks after phase ${phase}; see docs/PROGRESS.md. Stopping." >&2
    exit 1
  fi
  if grep -qE '^## SPEC CHANGE PROPOSAL \(T[0-9]{2}\)' docs/PROGRESS.md; then
    echo "SPEC change proposed during phase ${phase}; review docs/PROGRESS.md. Stopping." >&2
    exit 1
  fi
  if ! make verify >/dev/null 2>&1; then
    echo "make verify fails after phase ${phase}. Stopping." >&2
    exit 1
  fi
  echo "Phase ${phase} complete."
done
