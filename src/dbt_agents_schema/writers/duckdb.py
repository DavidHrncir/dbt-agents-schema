"""DuckDB writer (SPEC 6). Needs the `duckdb` extra.

"database" is the DuckDB catalog (the attached file); `None` means the connection's
current catalog. Primary keys are not declared: DuckDB enforces them rather than
treating them as informational, and the writer guarantees uniqueness (SPEC 5).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, TypeVar

from dbt_agents_schema.dialects import LOGICAL_TYPES
from dbt_agents_schema.dialects.duckdb import DIALECT
from dbt_agents_schema.plan import PROJECT_TABLES, TABLE_COLUMNS, TABLES, Plan, Row
from dbt_agents_schema.writers import (
    EnsureResult,
    RootScope,
    SwapResult,
    UnpublishResult,
    WarehouseError,
    import_extra,
)

log = logging.getLogger(__name__)

_T = TypeVar("_T")


class DuckDBWriter:
    """Writer for one agents schema in a DuckDB database."""

    def __init__(self, connection: Any, *, database: str | None, schema: str) -> None:
        self._con = connection
        self._database = database
        self._schema = schema
        self._owns_connection = False
        self._closed = False
        self._error_types: tuple[type[BaseException], ...] = _duckdb_errors()

    @classmethod
    def connect(cls, path: str, *, database: str | None, schema: str) -> DuckDBWriter:
        """Open `path` (a file, or `:memory:`) and return a writer that owns it."""
        duckdb = import_extra("duckdb", "duckdb")
        try:
            con = duckdb.connect(path)
        except duckdb.Error as exc:
            raise WarehouseError(
                f"DuckDB: cannot open database file {path!r}: {exc}. Fix the `path` in "
                "the credentials, and make sure its directory exists"
            ) from exc
        con.execute("SET TimeZone = 'UTC'")
        writer = cls(con, database=database, schema=schema)
        writer._owns_connection = True
        return writer

    # --- Writer protocol --------------------------------------------------------------

    def ensure_structure(self) -> EnsureResult:
        def run() -> EnsureResult:
            self._con.execute(
                f"CREATE SCHEMA IF NOT EXISTS {DIALECT.qualified(self._database, self._schema)}"
            )
            added: dict[str, tuple[str, ...]] = {}
            for table in TABLES:
                existing = self._existing_columns(table)
                if existing is None:
                    defs = ", ".join(
                        f"{DIALECT.quote(c)} {DIALECT.column_type(table, c)}"
                        for c in TABLE_COLUMNS[table]
                    )
                    self._con.execute(f"CREATE TABLE IF NOT EXISTS {self._table(table)} ({defs})")
                    continue
                missing = tuple(c for c in TABLE_COLUMNS[table] if c.lower() not in existing)
                for column in missing:
                    self._con.execute(
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
            if self._existing_columns("DBT_PROJECT") is None:
                return None
            cols = TABLE_COLUMNS["DBT_PROJECT"]
            cur = self._con.execute(
                f"SELECT {', '.join(DIALECT.quote(c) for c in cols)} "
                f"FROM {self._table('DBT_PROJECT')} WHERE {DIALECT.quote('project')} = ? "
                "LIMIT 1",
                [project],
            )
            found = cur.fetchone()
            return None if found is None else dict(zip(cols, found, strict=True))

        return self._guard("read DBT_PROJECT", run)

    def max_spec_version(self) -> int | None:
        def run() -> int | None:
            if self._existing_columns("DBT_PROJECT") is None:
                return None
            found = self._con.execute(
                f"SELECT max({DIALECT.quote('spec_version')}) FROM {self._table('DBT_PROJECT')}"
            ).fetchone()
            return None if found is None or found[0] is None else int(found[0])

        return self._guard("read DBT_PROJECT.spec_version", run)

    def swap(self, plan: Plan) -> SwapResult:
        def run() -> SwapResult:
            legacy: dict[str, int] = {}
            with self._transaction():
                project = DIALECT.quote("project")
                for table in PROJECT_TABLES:
                    legacy[table] = self._count(f"{project} IS NULL", table, [])
                    self._con.execute(
                        f"DELETE FROM {self._table(table)} "
                        f"WHERE {project} = ? OR {project} IS NULL",
                        [plan.project],
                    )
                    if legacy[table]:
                        log.info("removed %d legacy rows from %s", legacy[table], table)
                self._delete_root(RootScope.for_publish(plan.project))
                for table in TABLES:
                    self._insert(table, plan.rows(table))
            return SwapResult(rows_written=plan.counts(), legacy_rows_removed=legacy)

        return self._guard(f"replace the rows of project {plan.project!r}", run)

    def unpublish(self, project: str) -> UnpublishResult:
        def run() -> UnpublishResult:
            deleted: dict[str, int] = dict.fromkeys(TABLES, 0)
            with self._transaction():
                for table in PROJECT_TABLES:
                    if self._existing_columns(table) is None:
                        continue
                    where = f"{DIALECT.quote('project')} = ?"
                    deleted[table] = self._count(where, table, [project])
                    self._con.execute(f"DELETE FROM {self._table(table)} WHERE {where}", [project])
                if self._existing_columns("ROOT") is not None:
                    deleted["ROOT"] = self._delete_root(RootScope.for_unpublish(project))
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
            where = DIALECT.qualified(self._database, self._schema)
            raise WarehouseError(f"DuckDB: could not {action} in {where}: {exc}") from exc

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._con.execute("BEGIN TRANSACTION")
        try:
            yield
        except BaseException:
            self._con.execute("ROLLBACK")
            raise
        self._con.execute("COMMIT")

    def _table(self, table: str) -> str:
        return DIALECT.qualified(self._database, self._schema, table)

    def _catalog(self) -> str:
        if self._database is not None:
            return self._database
        found = self._con.execute("SELECT current_database()").fetchone()
        return str(found[0])

    def _existing_columns(self, table: str) -> set[str] | None:
        """Lowercased column names of `table`, or None when it does not exist."""
        found = self._con.execute(
            "SELECT lower(column_name) FROM information_schema.columns "
            "WHERE lower(table_catalog) = lower(?) AND lower(table_schema) = lower(?) "
            "AND lower(table_name) = lower(?)",
            [self._catalog(), self._schema, table],
        ).fetchall()
        return {r[0] for r in found} or None

    def _count(self, where: str, table: str, params: Sequence[Any]) -> int:
        found = self._con.execute(
            f"SELECT count(*) FROM {self._table(table)} WHERE {where}", list(params)
        ).fetchone()
        return int(found[0])

    def _delete_root(self, scope: RootScope) -> int:
        provider, key = DIALECT.quote("provider"), DIALECT.quote("key")
        exact = (*scope.shared_keys, scope.project_key)
        where = (
            f"{provider} = ? AND ({key} IN ({', '.join('?' for _ in exact)}) "
            f"OR starts_with({key}, ?))"
        )
        params = [scope.provider, *exact, scope.skill_prefix]
        count = self._count(where, "ROOT", params)
        self._con.execute(f"DELETE FROM {self._table('ROOT')} WHERE {where}", params)
        return count

    def _insert(self, table: str, rows: Sequence[Row]) -> None:
        if not rows:
            return
        cols = TABLE_COLUMNS[table]
        json_cols = {c for c in cols if LOGICAL_TYPES[table][c] == "json"}
        sql = (
            f"INSERT INTO {self._table(table)} ({', '.join(DIALECT.quote(c) for c in cols)}) "
            f"VALUES ({', '.join('?' for _ in cols)})"
        )
        self._con.executemany(
            sql,
            [[json.dumps(row[c]) if c in json_cols else row[c] for c in cols] for row in rows],
        )


def _duckdb_errors() -> tuple[type[BaseException], ...]:
    duckdb = import_extra("duckdb", "duckdb")
    return (duckdb.Error,)
