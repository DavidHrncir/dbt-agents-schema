#!/usr/bin/env bash
# Run one build phase headless with Claude Code, logging every message.
# Usage: dev/scripts/run-phase.sh <phase 0-5>
# Optional env: MAX_BUDGET_USD (passed as --max-budget-usd), PERMISSION_MODE (default auto)
set -euo pipefail

phase="${1:?usage: dev/scripts/run-phase.sh <phase 0-5>}"
cd "$(dirname "$0")/../.."

# Pull the /goal line for this phase out of dev/GOALS.md.
goal="$(awk -v p="Phase ${phase}:" '
  $0 == p {found=1; next}
  found && /^\/goal / {print; exit}
' dev/GOALS.md)"
if [[ -z "${goal}" ]]; then
  echo "No goal found for phase ${phase} in dev/GOALS.md" >&2
  exit 2
fi

mkdir -p logs
log="logs/phase-${phase}-$(date +%Y%m%d-%H%M%S).jsonl"

args=(-p "${goal}"
      --permission-mode "${PERMISSION_MODE:-auto}"
      --permission-prompts none
      --output-format stream-json --verbose)
if [[ -n "${MAX_BUDGET_USD:-}" ]]; then
  args+=(--max-budget-usd "${MAX_BUDGET_USD}")
fi

echo "Phase ${phase}: logging to ${log}"
claude "${args[@]}" | tee "${log}" | jq -rj '
  select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text + "\n"
' || true

echo
echo "Phase ${phase} finished. Status:"
grep -nE '^- \[( |!)\] \*\*T' dev/PLAN.md || echo "  no open or blocked tasks"
