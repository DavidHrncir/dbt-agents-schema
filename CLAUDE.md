# dbt-agents-schema

Publishes dbt project metadata into a warehouse "agents schema" so many dbt projects can
share one schema without overwriting each other.

- Contract: docs/SPEC.md — the source of truth. Read the sections a task cites.
- Work list: docs/PLAN.md
- Log: docs/PROGRESS.md (append only)

## Commands

- `make install` — create the env (`uv sync --all-extras --group dev`)
- `make verify` — lint, typecheck, tests, CI-file validation. Must exit 0 after every task.
- `make test` — unit and acceptance tests, no live warehouses
- `uv run pytest tests/path::test_name -x` — run one test while iterating
- `make fixtures` — rebuild fixture manifests (needs network for pip; run only in T02 or when fixtures change)

## Work loop (one task at a time)

1. Open docs/PLAN.md and take the first `[ ]` task in the earliest unfinished phase.
   Skip `[H]` tasks and tasks that depend on a `[!]` task.
2. Read the SPEC sections it cites. Write or extend the tests from its **Done when**
   first, run them, and see them fail.
3. Implement until those tests pass, then run `make verify`.
4. Show evidence: paste the `make verify` summary line and the specific test names.
5. Tick the box `[x]`, append a PROGRESS.md entry (task, what changed, evidence, any
   decisions), and commit: `T<NN>: <task title>`. One task per commit.
6. Start each task fresh: re-read its SPEC sections rather than relying on memory of
   earlier tasks.

## Rules

- IMPORTANT: never weaken, skip, or delete a test to make it pass, and never edit
  `tests/acceptance/` expectations except to add scenarios.
- Do not change SPEC.md. If it is wrong, ambiguous or impossible, append a
  `SPEC CHANGE PROPOSAL` to PROGRESS.md (problem, options, your recommendation), mark the
  task `[!]`, and move to the next independent task.
- After 3 failed attempts at the same task, mark it `[!]` with what you tried and move on.
- No network in tests except `@pytest.mark.live`. No credentials in the repo. Never read
  `.env` files or real `~/.dbt/profiles.yml`.
- Never `git push`, tag, or rewrite history. Commit locally on the current branch.
- Keep each diff to its task. No drive-by refactors.

## Code conventions

- Python 3.11+, full type hints, `mypy --strict` on `src/`, ruff for lint and format.
- Pure logic (config, manifest, skills, plan, check) never imports warehouse clients.
- One dialect module per warehouse; SQL values are always bound parameters; identifiers go
  through that dialect's quoting helper.
- Warehouse client libraries are optional extras: import them inside the writer, and give a
  clear error naming the extra (`pip install "dbt-agents-schema[snowflake]"`) when missing.
- Errors that reach users name the file, key or object and the fix.
