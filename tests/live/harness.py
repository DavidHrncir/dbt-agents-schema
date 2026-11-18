"""T13 acceptance scenarios against a real warehouse (Phase 3 live tests).

Each warehouse's live module builds a `LiveTarget` for a throwaway schema named
`DBT_AGENTS_SCHEMA_TEST_<random>` and calls the `scenario_*` functions. Scenario 4
(dbt Labs table migration) needs warehouse-specific DDL and is covered by each writer's
fake-connection tests instead.
"""

from __future__ import annotations

import io
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fakes import MANIFESTS, PROJECTS

from dbt_agents_schema.config import ResolvedTarget
from dbt_agents_schema.plan import PROJECT_TABLES, TABLES
from dbt_agents_schema.publish import GitInfo, PublishOutcome, publish, unpublish
from dbt_agents_schema.writers import Writer

REPOS = {
    "finance": "https://github.com/example/finance",
    "finance_v2": "https://github.com/example/finance",
    "marketing": "https://github.com/example/marketing",
}


def live_schema_name() -> str:
    return f"DBT_AGENTS_SCHEMA_TEST_{secrets.token_hex(4).upper()}"


@dataclass
class LiveTarget:
    """One live warehouse schema plus the dialect bits the scenarios need.

    `query` takes SQL with `?` placeholders. `table` returns the qualified, quoted table
    name; `col` a quoted column name; `starts_with` a prefix predicate with one `?`.
    """

    warehouse: str
    open_writer: Callable[[], Writer]
    query: Callable[[str, Sequence[Any]], list[tuple[Any, ...]]]
    table: Callable[[str], str]
    col: Callable[[str], str]
    starts_with: Callable[[str], str]

    def factory(self, _target: ResolvedTarget) -> Writer:
        return self.open_writer()

    def publish(
        self, name: str, *, repo_url: str | None = None, force_takeover: bool = False
    ) -> tuple[PublishOutcome, str]:
        out, err = io.StringIO(), io.StringIO()
        outcome = publish(
            PROJECTS / name,
            env="prod",
            manifest_path=MANIFESTS / f"{name}.json",
            warehouse=self.warehouse,
            writer_factory=self.factory,
            git=GitInfo(repo_url=repo_url or REPOS[name], sha="a" * 40, branch="main"),
            now=lambda: datetime(2026, 11, 17, 12, 0, tzinfo=UTC),
            force_takeover=force_takeover,
            out=out,
            err=err,
        )
        text = out.getvalue() + err.getvalue()
        print(f"--- publish {name} -> exit {outcome.exit_code}\n{text}")
        return outcome, text

    def snapshot(self, project: str | None = None) -> dict[str, list[tuple[Any, ...]]]:
        result: dict[str, list[tuple[Any, ...]]] = {}
        for t in TABLES:
            sql = f"SELECT * FROM {self.table(t)}"
            params: list[Any] = []
            if project is not None:
                if t == "ROOT":
                    sql += f" WHERE {self.col('key')} = ? OR {self.starts_with(self.col('key'))}"
                    params = [f"project/{project}", f"skill/{project}/"]
                else:
                    sql += f" WHERE {self.col('project')} = ?"
                    params = [project]
            result[t] = sorted(self.query(sql, params), key=repr)
        return result

    def projects_in(self, table: str) -> set[Any]:
        sql = f"SELECT DISTINCT {self.col('project')} FROM {self.table(table)}"
        return {r[0] for r in self.query(sql, [])}

    def root_keys(self) -> set[str]:
        return {r[0] for r in self.query(f"SELECT {self.col('key')} FROM {self.table('ROOT')}", [])}

    def column_set(self, table: str, column: str, project: str) -> set[Any]:
        sql = f"SELECT {self.col(column)} FROM {self.table(table)} WHERE {self.col('project')} = ?"
        return {r[0] for r in self.query(sql, [project])}


def scenario_1_two_projects_share_one_schema(lt: LiveTarget) -> None:
    assert lt.publish("finance")[0].exit_code == 0
    assert lt.publish("marketing")[0].exit_code == 0
    for t in PROJECT_TABLES:
        assert lt.projects_in(t) == {"finance", "marketing"}, t
    keys = lt.root_keys()
    assert {"project/finance", "project/marketing"} <= keys
    assert {"skill/finance/revenue_overview", "skill/marketing/attribution"} <= keys
    counts = lt.query(
        f"SELECT {lt.col('key')}, count(*) FROM {lt.table('ROOT')} GROUP BY {lt.col('key')}", []
    )
    assert all(n == 1 for _, n in counts)


def scenario_2_republish_leaves_other_untouched(lt: LiveTarget) -> None:
    lt.publish("finance")
    lt.publish("marketing")
    marketing_before = lt.snapshot("marketing")
    models_before = lt.column_set("DBT_MODEL", "unique_id", "finance")
    assert lt.publish("finance_v2")[0].exit_code == 0
    assert lt.snapshot("marketing") == marketing_before
    removed = models_before - lt.column_set("DBT_MODEL", "unique_id", "finance")
    assert len(removed) == 1
    assert not lt.column_set("DBT_COLUMN", "model_id", "finance") & removed
    keys = lt.root_keys()
    assert "skill/finance/payments/reconciliation" not in keys
    assert "skill/finance/revenue_overview" in keys


def scenario_3_ownership_guard(lt: LiveTarget) -> None:
    lt.publish("finance")
    before = lt.snapshot()
    other = "https://gitlab.example.com/someone-else/finance"
    outcome, text = lt.publish("finance", repo_url=other)
    assert outcome.exit_code == 4
    assert "--force-takeover" in text
    assert lt.snapshot() == before
    assert lt.publish("finance", repo_url=other, force_takeover=True)[0].exit_code == 0
    assert lt.column_set("DBT_PROJECT", "repo_url", "finance") == {other}


def scenario_5_newer_spec_version_refuses(lt: LiveTarget) -> None:
    lt.publish("marketing")
    lt.query(f"UPDATE {lt.table('DBT_PROJECT')} SET {lt.col('spec_version')} = 2", [])
    before = lt.snapshot()
    outcome, text = lt.publish("finance")
    assert outcome.exit_code == 2
    assert "spec_version 2" in text
    assert lt.snapshot() == before


def scenario_6_unpublish_removes_only_that_project(lt: LiveTarget) -> None:
    lt.publish("finance")
    finance_alone = lt.snapshot()
    lt.publish("marketing")
    out, err = io.StringIO(), io.StringIO()
    code = unpublish(
        PROJECTS / "marketing",
        env="prod",
        warehouse=lt.warehouse,
        writer_factory=lt.factory,
        out=out,
        err=err,
    )
    print(out.getvalue() + err.getvalue())
    assert code == 0
    for t in PROJECT_TABLES:
        assert lt.projects_in(t) == {"finance"}
    assert lt.snapshot() == finance_alone


SCENARIOS: dict[str, Callable[[LiveTarget], None]] = {
    "1_two_projects_share_one_schema": scenario_1_two_projects_share_one_schema,
    "2_republish_leaves_other_untouched": scenario_2_republish_leaves_other_untouched,
    "3_ownership_guard": scenario_3_ownership_guard,
    "5_newer_spec_version_refuses": scenario_5_newer_spec_version_refuses,
    "6_unpublish_removes_only_that_project": scenario_6_unpublish_removes_only_that_project,
}
