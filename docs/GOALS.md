# Phase goals

Paste one line into a fresh Claude Code session (auto mode on), one phase per session.
`scripts/run-phase.sh <N>` sends the same text headless.

Phase 0:

```text
/goal Every task in Phase 0 of docs/PLAN.md is marked [x], [!] or [H]; the Phase 0 spec-reviewer summary line is in docs/PROGRESS.md; and the final turn shows `make verify` exiting 0. Use the /next-task skill for each task, one commit per task, and do not start Phase 1. Or stop after 60 turns.
```

Phase 1:

```text
/goal Every task in Phase 1 of docs/PLAN.md is marked [x], [!] or [H]; the Phase 1 spec-reviewer summary line is in docs/PROGRESS.md; and the final turn shows `make verify` exiting 0. Use the /next-task skill for each task, one commit per task, and do not start Phase 2. Or stop after 80 turns.
```

Phase 2:

```text
/goal Every task in Phase 2 of docs/PLAN.md is marked [x], [!] or [H]; tests/acceptance/test_multi_project.py passes all six scenarios with output shown; the Phase 2 spec-reviewer summary line is in docs/PROGRESS.md; and the final turn shows `make verify` exiting 0. Use the /next-task skill for each task, one commit per task, and do not start Phase 3. Or stop after 80 turns.
```

Phase 3:

```text
/goal Every task in Phase 3 of docs/PLAN.md is marked [x], [!] or [H]; live tests skip cleanly without credentials; the Phase 3 spec-reviewer summary line is in docs/PROGRESS.md; and the final turn shows `make verify` exiting 0. Use the /next-task skill for each task, one commit per task, and do not start Phase 4. Or stop after 80 turns.
```

Phase 4:

```text
/goal Every task in Phase 4 of docs/PLAN.md is marked [x], [!] or [H]; the Phase 4 spec-reviewer summary line is in docs/PROGRESS.md; and the final turn shows `make verify` exiting 0. Use the /next-task skill for each task, one commit per task, and do not start Phase 5. Or stop after 80 turns.
```

Phase 5:

```text
/goal Every task in Phase 5 of docs/PLAN.md is marked [x], [!] or [H]; `bash scripts/demo_duckdb.sh` exits 0 with output shown; the final spec-reviewer summary line is in docs/PROGRESS.md; and the final turn shows `make verify` exiting 0 and a local commit `release: 0.1.0`. Use the /next-task skill for each task. Do not tag or push. Or stop after 60 turns.
```

Phase 6:

```text
/goal Every task in Phase 6 of docs/PLAN.md is marked [x], [!] or [H]; the Phase 6 spec-reviewer summary line is in docs/PROGRESS.md; and the final turn shows `make verify` exiting 0 and a local commit `release: 0.2.0`. Use the /next-task skill for each task, one commit per task. Do not tag or push. Or stop after 80 turns.
```
