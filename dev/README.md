# Project development files

**You do not need anything in this folder to use dbt-agents-schema.** To install and
run it, see the [README](../README.md) and the [partner guide](../docs/partner-guide.md).

This folder holds the material used to build the project with Claude Code. It is kept
for the maintainer and as a record of how the tool was specified and built.

| File | What it is |
| --- | --- |
| `SPEC.md` | The build contract: config, credentials, tables, publish algorithm, CLI, checks, CI wrappers. User-facing parts are in the partner guide. |
| `PLAN.md` | The task list, in phases, each task with testable **Done when** criteria |
| `PROGRESS.md` | The append-only build log: what each task changed, test evidence, decisions, and spec change proposals |
| `GOALS.md` | One Claude Code `/goal` line per phase |
| `scripts/run-phase.sh`, `scripts/run-all.sh` | Run one phase, or a range of phases, headless with Claude Code, logging to `logs/` |

Two related items stay at the repository root, because Claude Code only loads them
from there:

| Path | What it is |
| --- | --- |
| `CLAUDE.md` | The operating rules Claude Code follows in this repository |
| `.claude/` | Project settings (pre-approved commands), the `spec-reviewer` subagent, and the `/next-task` skill |

## Working on the code

```bash
make install    # create the environment (uv)
make verify     # lint, typecheck, tests, CI-file validation; must exit 0
make test-live  # tests against real warehouses; needs LIVE_* credentials
make fixtures   # rebuild the fixture manifests (needs network)
```

With Claude Code, run `/next-task` to do the next open task in `PLAN.md`, or
`bash dev/scripts/run-phase.sh <N>` to run a whole phase headless. Read `CLAUDE.md`
first.
