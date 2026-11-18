# dbt-agents-schema

Publishes dbt project metadata into a warehouse agents schema, so many dbt projects can
share one schema without overwriting each other. Works with dbt v1, dbt v2 (Fusion) and
dbt platform projects, from GitHub Actions or GitLab CI.

**This repository is a build kit.** It holds the contract and plan that Claude Code
works through to build the tool. Partner-facing docs arrive in `docs/partner-guide.md`
during Phase 5.

| File | What it is |
| --- | --- |
| `docs/SPEC.md` | The contract: config, credentials, tables, publish algorithm, CLI, checks, CI wrappers |
| `docs/PLAN.md` | 29 tasks in 6 phases, each with testable **Done when** criteria |
| `docs/GOALS.md` | One `/goal` line per phase, ready to paste |
| `docs/PROGRESS.md` | Log Claude appends to after every task; read it between phases |
| `CLAUDE.md` | Operating rules Claude Code loads every session |
| `.claude/settings.json` | Pre-approved commands; blocks push, tag, curl, and reading secrets |
| `.claude/agents/spec-reviewer.md` | Fresh-context reviewer run at the end of each phase |
| `.claude/skills/next-task/` | `/next-task`: do exactly one task and commit it |
| `scripts/run-phase.sh`, `scripts/run-all.sh` | Headless runners with logs |

## Before you start

- Claude Code v2.1.259 or later (`claude --version`; `claude update`).
- Python 3.11+, [`uv`](https://docs.astral.sh/uv/), `make`, `git`, and `jq` (for the
  headless scripts).
- Network access to PyPI (dependencies and dbt for fixtures).
- Create the repo where it will live, push this folder, and work on a branch:
  `git switch -c build/v0.1`.

## Run it

1. Open the folder in VS Code and start Claude Code in the integrated terminal:
   `claude`. Accept the workspace trust prompt (it enables the project settings,
   skill and subagent).
2. Switch to auto mode with `Shift+Tab` if it is not already on.
3. Paste the Phase 0 line from `docs/GOALS.md`. Watch this phase: it sets up tooling and
   fixtures, and is where environment problems surface.
4. When it finishes, review `docs/PROGRESS.md` and `git log --oneline`. Then for each
   later phase, run `/clear` and paste the next goal, or go fully unattended:
   `bash scripts/run-all.sh 1 5`. That runs each phase in a fresh session and stops at
   the first blocked task, spec proposal, or failing `make verify`.

Stop points worth reading before continuing:

- **After Phase 2.** `tests/acceptance/test_multi_project.py` proves that many projects
  can share one schema, on DuckDB. This is the core promise to partners.
- **After Phase 3.** Run the live warehouse tests yourself (task H1): set
  `LIVE_SNOWFLAKE_CREDENTIALS` (and the BigQuery or Databricks equivalents) and run
  `make test-live`.
- **Any `[!]` task or SPEC CHANGE PROPOSAL.** Decide, edit `docs/SPEC.md` yourself,
  change the task back to `[ ]`, and rerun the phase.

To cap spend on headless runs, set `MAX_BUDGET_USD=20 bash scripts/run-phase.sh 3`.
Logs land in `logs/`.
