---
name: next-task
description: Do the next unchecked task in docs/PLAN.md for dbt-agents-schema, following the work loop in CLAUDE.md, and stop after committing it.
---

Do exactly one task:

1. In `docs/PLAN.md`, find the first `[ ]` task in the earliest unfinished phase. Skip
   `[H]` tasks and anything that depends on a `[!]` task. Say which task you picked.
2. Read the SPEC sections it cites. Write the tests for its **Done when** first and show
   them failing.
3. Implement. Iterate with single tests (`uv run pytest <path>::<name> -x`), then run
   `make verify`.
4. If the task is a phase review, delegate to the `spec-reviewer` subagent with the
   phase number, fix blockers and should-fix items that affect correctness, and paste the
   reviewer's summary line into PROGRESS.md.
5. Tick the task, append to `docs/PROGRESS.md`, and commit `T<NN>: <title>`.
6. End with: the task id, the `make verify` summary line, and the next task id.

If you cannot finish after 3 attempts, or the SPEC is wrong, follow the blocked and
SPEC CHANGE PROPOSAL rules in CLAUDE.md instead of forcing it.
