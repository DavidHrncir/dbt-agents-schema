"""Databricks writer (SPEC 6). Needs the `databricks` extra.

Databricks has no multi-table transactions, so each table is replaced by one atomic
statement (SPEC 6 step 7):

    INSERT INTO <t> REPLACE WHERE <predicate> SELECT ... FROM <stage>

- Rows are first staged, per table, into a Delta table
  `_DBT_AGENTS_SCHEMA_STAGE_<TABLE>_<hex>` in the agents schema with batched,
  parameterized `INSERT ... VALUES`. Parameter markers are not allowed in view
  definitions, so a temporary view cannot carry bound values. Staging finishes for every
  table before the first replace, and the stage tables are dropped afterwards (also on
  failure).
- Predicates: project tables `project = :project OR project IS NULL`; ROOT
  `provider = :provider AND (key IN (:root_key_0, ...) OR startswith(key, :skill_prefix))`.
  Every plan row satisfies its table's predicate, as `REPLACE WHERE` requires.
- `REPLACE WHERE` takes no column list, so the SELECT follows the target table's
  actual column order from `information_schema` (migrated tables have `project` last)
  and fills columns this tool does not know with NULL.
- A Delta concurrent-modification conflict (another publish writing the same table)
  re-runs the whole swap, a bounded number of times.
- Values use native named parameters (`:name`). JSON columns are STRING holding JSON text.
- New tables declare an informational `CONSTRAINT pk_<table> PRIMARY KEY (...)` with
  `NOT NULL` key columns (Unity Catalog, SPEC 5). Migrated tables are left as they are.
"""

from __future__ import annotations

import json
import logging
import re
import secrets
import time
from collections.abc import Callable, Mapping, Sequence
from types import ModuleType
from typing import Any, TypeVar

from dbt_agents_schema.dialects import LOGICAL_TYPES
from dbt_agents_schema.dialects.databricks import DIALECT
from dbt_agents_schema.plan import PRIMARY_KEYS, PROJECT_TABLES, TABLE_COLUMNS, TABLES, Plan, Row
from dbt_agents_schema.writers import (
    CONFLICT_RETRIES,
    EnsureResult,
    RootScope,
    SwapResult,
    UnpublishResult,
    WarehouseError,
    import_extra,
    retry_on_conflict,
)

log = logging.getLogger(__name__)

_T = TypeVar("_T")

BATCH_ROWS = 500
_MAX_PARAMS = 2000
STAGE_PREFIX = "_DBT_AGENTS_SCHEMA_STAGE_"
# Delta optimistic-concurrency conflicts (another publish wrote the same table).
_CONFLICT = re.compile(r"Concurrent\w*Exception|DELTA_CONCURRENT")


def _sql() -> ModuleType:
    return import_extra("databricks.sql", "databricks")


class DatabricksWriter:
    """Writer for one agents schema in a Databricks catalog, over a DB-API connection."""

    def __init__(
        self,
        connection: Any,
        *,
        catalog: str | None,
        schema: str,
        error_types: tuple[type[BaseException], ...] | None = None,
        batch_rows: int = BATCH_ROWS,
        conflict_retries: int = CONFLICT_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._con = connection
        self._catalog = catalog
        self._schema = schema
        self._batch_rows = batch_rows
        self._conflict_retries = conflict_retries
        self._sleep = sleep
        self._owns_connection = False
        self._closed = False
        self._error_types = error_types if error_types is not None else (_sql().exc.Error,)

    @classmethod
    def connect(
        cls, settings: Mapping[str, Any], *, schema: str, database: str | None = None
    ) -> DatabricksWriter:
        """Connect with `WAREHOUSE_CREDENTIALS`-style settings (SPEC 4.1).

        `database` (the rendered target database) overrides `settings["catalog"]`.
        """
        sql = _sql()
        host = str(settings["host"]).removeprefix("https://").removeprefix("http://").rstrip("/")
        catalog = database or settings.get("catalog")
        try:
            con = sql.connect(
                server_hostname=host,
                http_path=settings["http_path"],
                access_token=settings["token"],
                catalog=catalog,
                user_agent_entry="dbt-agents-schema",
            )
        except sql.exc.Error as exc:
            message = str(exc).replace(str(settings["token"]), "<redacted>")
            raise WarehouseError(
                f"Databricks: cannot connect to {host!r} ({settings['http_path']}): {message}. "
                "Fix host, http_path and token in the credentials"
            ) from None
        writer = cls(con, catalog=catalog, schema=schema, error_types=(sql.exc.Error,))
        writer._owns_connection = True
        return writer

    # --- Writer protocol --------------------------------------------------------------

    def ensure_structure(self) -> EnsureResult:
        def run() -> EnsureResult:
            self._exec(f"CREATE SCHEMA IF NOT EXISTS {self._schema_ref()}")
            existing = self._existing_columns()
            added: dict[str, tuple[str, ...]] = {}
            for table in TABLES:
                have = existing.get(table)
                if have is None:
                    keys = PRIMARY_KEYS[table]
                    defs = ", ".join(
                        f"{DIALECT.quote(c)} {DIALECT.column_type(table, c)}"
                        + (" NOT NULL" if c in keys else "")
                        for c in TABLE_COLUMNS[table]
                    )
                    pk = ", ".join(DIALECT.quote(c) for c in keys)
                    constraint = DIALECT.quote(f"pk_{table.lower()}")
                    self._exec(
                        f"CREATE TABLE IF NOT EXISTS {self._table(table)} "
                        f"({defs}, CONSTRAINT {constraint} PRIMARY KEY ({pk}))"
                    )
                    continue
                missing = tuple(c for c in TABLE_COLUMNS[table] if c.lower() not in have)
                for column in missing:
                    self._exec(
                        f"ALTER TABLE {self._table(table)} ADD COLUMN "
                        f"{DIALECT.quote(column)} {DIALECT.column_type(table, column)}"
                    )
                if missing:
                    log.info("added columns to %s: %s", table, ", ".join(missing))
                    added[table] = missing
            return EnsureResult(columns_added=added)

        return self._guard("create the agents schema tables", run)

    def read_project_row(self, project: str) -> Mapping[str, Any] | None:
        def run() -> Mapping[str, Any] | None:
            if "DBT_PROJECT" not in self._existing_columns():
                return None
            cols = TABLE_COLUMNS["DBT_PROJECT"]
            found = self._exec(
                f"SELECT {', '.join(DIALECT.quote(c) for c in cols)} "
                f"FROM {self._table('DBT_PROJECT')} WHERE {DIALECT.quote('project')} = :project "
                "LIMIT 1",
                {"project": project},
            ).fetchone()
            return None if found is None else dict(zip(cols, tuple(found), strict=True))

        return self._guard("read DBT_PROJECT", run)

    def max_spec_version(self) -> int | None:
        def run() -> int | None:
            if "DBT_PROJECT" not in self._existing_columns():
                return None
            found = self._exec(
                f"SELECT max({DIALECT.quote('spec_version')}) FROM {self._table('DBT_PROJECT')}"
            ).fetchone()
            return None if found is None or found[0] is None else int(found[0])

        return self._guard("read DBT_PROJECT.spec_version", run)

    def swap(self, plan: Plan) -> SwapResult:
        def run() -> SwapResult:
            existing = self._existing_columns(ordered=True)
            project = DIALECT.quote("project")
            legacy: dict[str, int] = {}
            for table in PROJECT_TABLES:
                legacy[table] = self._count(table, f"{project} IS NULL", None)
                if legacy[table]:
                    log.info("removing %d legacy rows from %s", legacy[table], table)
            scope = RootScope.for_publish(plan.project)
            root_where, root_params = self._root_predicate(scope)
            stages: dict[str, str] = {}
            try:
                for table in (*PROJECT_TABLES, "ROOT"):
                    self._stage(table, plan.rows(table), stages)
                for table in (*PROJECT_TABLES, "ROOT"):
                    if table == "ROOT":
                        where, params = root_where, root_params
                    else:
                        where = f"{project} = :project OR {project} IS NULL"
                        params = {"project": plan.project}
                    target_cols = existing.get(table) or list(TABLE_COLUMNS[table])
                    select = ", ".join(
                        DIALECT.quote(c)
                        if c in TABLE_COLUMNS[table]
                        else f"NULL AS {DIALECT.quote(c)}"
                        for c in target_cols
                    )
                    self._exec(
                        f"INSERT INTO {self._table(table)} REPLACE WHERE {where} "
                        f"SELECT {select} FROM {self._table(stages[table])}",
                        params,
                    )
            finally:
                self._drop_stages(stages.values())
            return SwapResult(rows_written=plan.counts(), legacy_rows_removed=legacy)

        def retried() -> SwapResult:
            # Re-running the whole swap is safe: every replace is idempotent for the
            # same plan, and stages are rebuilt.
            return retry_on_conflict(
                run,
                errors=self._error_types,
                conflict=_CONFLICT,
                retries=self._conflict_retries,
                sleep=self._sleep,
            )

        return self._guard(f"replace the rows of project {plan.project!r}", retried)

    def unpublish(self, project: str) -> UnpublishResult:
        def run() -> UnpublishResult:
            deleted: dict[str, int] = dict.fromkeys(TABLES, 0)
            present = self._existing_columns()
            for table in (*PROJECT_TABLES, "ROOT"):
                if table not in present:
                    continue
                if table == "ROOT":
                    where, params = self._root_predicate(RootScope.for_unpublish(project))
                else:
                    where, params = f"{DIALECT.quote('project')} = :project", {"project": project}
                deleted[table] = self._count(table, where, params)
                self._exec(f"DELETE FROM {self._table(table)} WHERE {where}", params)
            return UnpublishResult(rows_deleted=deleted)

        return self._guard(f"delete the rows of project {project!r}", run)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_connection:
            self._con.close()

    # --- internals --------------------------------------------------------------------

    def _guard(self, action: str, fn: Callable[[], _T]) -> _T:
        try:
            return fn()
        except self._error_types as exc:
            raise WarehouseError(
                f"Databricks: could not {action} in {self._schema_ref()}: {exc}"
            ) from None

    def _exec(self, sql: str, params: Mapping[str, Any] | None = None) -> Any:
        cur = self._con.cursor()
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(sql, dict(params))
        return cur

    def _schema_ref(self) -> str:
        return DIALECT.qualified(self._catalog, self._schema)

    def _table(self, table: str) -> str:
        return DIALECT.qualified(self._catalog, self._schema, table)

    def _existing_columns(self, *, ordered: bool = False) -> dict[str, list[str]]:
        """Lowercased column names per existing table (uppercase key), in table order."""
        info = (
            f"{DIALECT.quote(self._catalog)}.information_schema.columns"
            if self._catalog
            else "information_schema.columns"
        )
        rows = (
            self._exec(
                f"SELECT lower(table_name), lower(column_name) FROM {info} "
                "WHERE lower(table_schema) = lower(:schema) "
                "ORDER BY table_name, ordinal_position",
                {"schema": self._schema},
            ).fetchall()
            or []
        )
        found: dict[str, list[str]] = {}
        for row in rows:
            found.setdefault(str(row[0]).upper(), []).append(str(row[1]))
        return found

    def _count(self, table: str, where: str, params: Mapping[str, Any] | None) -> int:
        found = self._exec(
            f"SELECT count(*) FROM {self._table(table)} WHERE {where}", params
        ).fetchone()
        return int(found[0]) if found and found[0] is not None else 0

    def _root_predicate(self, scope: RootScope) -> tuple[str, dict[str, Any]]:
        provider, key = DIALECT.quote("provider"), DIALECT.quote("key")
        exact = (*scope.shared_keys, scope.project_key)
        params: dict[str, Any] = {"provider": scope.provider}
        params.update({f"root_key_{i}": k for i, k in enumerate(exact)})
        params["skill_prefix"] = scope.skill_prefix
        markers = ", ".join(f":root_key_{i}" for i in range(len(exact)))
        where = (
            f"{provider} = :provider AND ({key} IN ({markers}) OR startswith({key}, :skill_prefix))"
        )
        return where, params

    def _stage(self, table: str, rows: Sequence[Row], stages: dict[str, str]) -> None:
        """Create and fill a stage table; registers it in `stages` before creating it."""
        name = f"{STAGE_PREFIX}{table}_{secrets.token_hex(4)}"
        stages[table] = name
        cols = TABLE_COLUMNS[table]
        defs = ", ".join(f"{DIALECT.quote(c)} {DIALECT.column_type(table, c)}" for c in cols)
        self._exec(f"CREATE TABLE {self._table(name)} ({defs})")
        json_cols = {c for c in cols if LOGICAL_TYPES[table][c] == "json"}
        per_batch = max(1, min(self._batch_rows, _MAX_PARAMS // len(cols)))
        for start in range(0, len(rows), per_batch):
            batch = rows[start : start + per_batch]
            params: dict[str, Any] = {}
            tuples = []
            for r, row in enumerate(batch):
                markers = []
                for c_index, c in enumerate(cols):
                    marker = f"v{r}_{c_index}"
                    value = row[c]
                    params[marker] = (
                        json.dumps(value) if c in json_cols and value is not None else value
                    )
                    markers.append(f":{marker}")
                tuples.append("(" + ", ".join(markers) + ")")
            self._exec(f"INSERT INTO {self._table(name)} VALUES {', '.join(tuples)}", params)

    def _drop_stages(self, stages: Any) -> None:
        for name in stages:
            try:
                self._exec(f"DROP TABLE IF EXISTS {self._table(name)}")
            except self._error_types as exc:
                log.warning("could not drop staging table %s; drop it by hand: %s", name, exc)
