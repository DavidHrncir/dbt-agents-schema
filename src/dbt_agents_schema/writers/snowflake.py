"""Snowflake writer (SPEC 6). Needs the `snowflake` extra.

- Identifiers: table and column names are uppercased before quoting, so they match
  Snowflake's unquoted-identifier convention (`select project from dbt_model` works,
  and tables created by the dbt Labs tool with unquoted names are found). Database and
  schema are quoted as given (config already uppercases rendered names on Snowflake).
- Values are bound with the `qmark` paramstyle. Rows are inserted in batches of
  `INSERT INTO t (...) SELECT $1, PARSE_JSON($2), ... FROM VALUES (?, ?), ...`, because
  Snowflake does not allow `PARSE_JSON` inside a `VALUES` clause. JSON values are bound
  as JSON text; timestamps as ISO-8601 text through `TO_TIMESTAMP_TZ`.
- The swap runs between `BEGIN` and `COMMIT`; any failure issues `ROLLBACK`.
- Primary keys are not declared: the writer guarantees uniqueness (SPEC 5).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, TypeVar

from dbt_agents_schema.dialects import LOGICAL_TYPES
from dbt_agents_schema.dialects.snowflake import DIALECT
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

# Rows per INSERT statement, further capped so one statement binds at most _MAX_PARAMS.
BATCH_ROWS = 1000
_MAX_PARAMS = 10_000

_SECRET_KEYS = ("password", "private_key_pem", "private_key_passphrase")


def _connector() -> ModuleType:
    return import_extra("snowflake.connector", "snowflake")


def _ident(name: str) -> str:
    """Quoted, uppercased table or column name."""
    return DIALECT.quote(name.upper())


class SnowflakeWriter:
    """Writer for one agents schema in Snowflake, over a DB-API connection (qmark)."""

    def __init__(
        self,
        connection: Any,
        *,
        database: str | None,
        schema: str,
        error_types: tuple[type[BaseException], ...] | None = None,
        batch_rows: int = BATCH_ROWS,
    ) -> None:
        self._con = connection
        self._database = database
        self._schema = schema
        self._batch_rows = batch_rows
        self._owns_connection = False
        self._closed = False
        self._error_types = error_types if error_types is not None else (_connector().Error,)

    @classmethod
    def connect(
        cls, settings: Mapping[str, Any], *, schema: str, database: str | None = None
    ) -> SnowflakeWriter:
        """Connect with `WAREHOUSE_CREDENTIALS`-style settings (SPEC 4.1).

        `database` (the rendered target database) overrides `settings["database"]`.
        """
        connector = _connector()
        db = database or settings.get("database")
        kwargs: dict[str, Any] = {
            "account": settings["account"],
            "user": settings["user"],
            "paramstyle": "qmark",
            "session_parameters": {"TIMEZONE": "UTC", "QUERY_TAG": "dbt-agents-schema"},
            "application": "dbt-agents-schema",
        }
        for key in ("warehouse", "role", "authenticator"):
            if settings.get(key):
                kwargs[key] = settings[key]
        if db:
            kwargs["database"] = db
        if settings.get("private_key_pem") or settings.get("private_key_path"):
            kwargs["private_key"] = _load_private_key(settings)
        elif settings.get("password"):
            kwargs["password"] = settings["password"]
        try:
            con = connector.connect(**kwargs)
        except connector.Error as exc:
            raise WarehouseError(
                f"Snowflake: cannot connect to account {settings['account']!r} as user "
                f"{settings['user']!r}: {_scrub(str(exc), settings)}. Fix the account, user, "
                "role, warehouse and authentication keys in the credentials"
            ) from None
        writer = cls(con, database=db, schema=schema, error_types=(connector.Error,))
        writer._owns_connection = True
        return writer

    # --- Writer protocol --------------------------------------------------------------

    def ensure_structure(self) -> EnsureResult:
        def run() -> EnsureResult:
            self._exec(
                f"CREATE SCHEMA IF NOT EXISTS {DIALECT.qualified(self._database, self._schema)}"
            )
            added: dict[str, tuple[str, ...]] = {}
            for table in TABLES:
                existing = self._existing_columns(table)
                if existing is None:
                    defs = ", ".join(
                        f"{_ident(c)} {DIALECT.column_type(table, c)}" for c in TABLE_COLUMNS[table]
                    )
                    self._exec(f"CREATE TABLE IF NOT EXISTS {self._table(table)} ({defs})")
                    continue
                missing = tuple(c for c in TABLE_COLUMNS[table] if c.lower() not in existing)
                for column in missing:
                    self._exec(
                        f"ALTER TABLE {self._table(table)} ADD COLUMN "
                        f"{_ident(column)} {DIALECT.column_type(table, column)}"
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
            found = self._fetchone(
                f"SELECT {', '.join(_ident(c) for c in cols)} "
                f"FROM {self._table('DBT_PROJECT')} WHERE {_ident('project')} = ? LIMIT 1",
                [project],
            )
            return None if found is None else dict(zip(cols, found, strict=True))

        return self._guard("read DBT_PROJECT", run)

    def max_spec_version(self) -> int | None:
        def run() -> int | None:
            if self._existing_columns("DBT_PROJECT") is None:
                return None
            found = self._fetchone(
                f"SELECT max({_ident('spec_version')}) FROM {self._table('DBT_PROJECT')}"
            )
            return None if found is None or found[0] is None else int(found[0])

        return self._guard("read DBT_PROJECT.spec_version", run)

    def swap(self, plan: Plan) -> SwapResult:
        def run() -> SwapResult:
            legacy: dict[str, int] = {}
            with self._transaction():
                project = _ident("project")
                for table in PROJECT_TABLES:
                    legacy[table] = self._count(f"{project} IS NULL", table, [])
                    self._exec(
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
            present = {t for t in TABLES if self._existing_columns(t) is not None}
            with self._transaction():
                for table in PROJECT_TABLES:
                    if table not in present:
                        continue
                    where = f"{_ident('project')} = ?"
                    deleted[table] = self._count(where, table, [project])
                    self._exec(f"DELETE FROM {self._table(table)} WHERE {where}", [project])
                if "ROOT" in present:
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
            raise WarehouseError(f"Snowflake: could not {action} in {where}: {exc}") from None

    def _exec(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        cur = self._con.cursor()
        if params is None:
            cur.execute(sql)
        else:
            cur.execute(sql, list(params))
        return cur

    def _fetchone(self, sql: str, params: Sequence[Any] | None = None) -> Any:
        return self._exec(sql, params).fetchone()

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        self._exec("BEGIN")
        try:
            yield
        except BaseException:
            self._exec("ROLLBACK")
            raise
        self._exec("COMMIT")

    def _table(self, table: str) -> str:
        return f"{DIALECT.qualified(self._database, self._schema)}.{_ident(table)}"

    def _existing_columns(self, table: str) -> set[str] | None:
        """Lowercased column names of `table`, or None when it does not exist."""
        info = DIALECT.qualified(self._database, "INFORMATION_SCHEMA", "COLUMNS")
        rows = (
            self._exec(
                f'SELECT "COLUMN_NAME" FROM {info} WHERE "TABLE_SCHEMA" = ? AND "TABLE_NAME" = ?',
                [self._schema, table.upper()],
            ).fetchall()
            or []
        )
        return {str(r[0]).lower() for r in rows} or None

    def _count(self, where: str, table: str, params: Sequence[Any]) -> int:
        found = self._fetchone(f"SELECT count(*) FROM {self._table(table)} WHERE {where}", params)
        return int(found[0]) if found and found[0] is not None else 0

    def _delete_root(self, scope: RootScope) -> int:
        provider, key = _ident("provider"), _ident("key")
        exact = (*scope.shared_keys, scope.project_key)
        where = (
            f"{provider} = ? AND ({key} IN ({', '.join('?' for _ in exact)}) "
            f"OR STARTSWITH({key}, ?))"
        )
        params = [scope.provider, *exact, scope.skill_prefix]
        count = self._count(where, "ROOT", params)
        self._exec(f"DELETE FROM {self._table('ROOT')} WHERE {where}", params)
        return count

    def _insert(self, table: str, rows: Sequence[Row]) -> None:
        if not rows:
            return
        cols = TABLE_COLUMNS[table]
        kinds = [LOGICAL_TYPES[table][c] for c in cols]
        select = ", ".join(_select_expr(kind, i) for i, kind in enumerate(kinds, start=1))
        head = (
            f"INSERT INTO {self._table(table)} ({', '.join(_ident(c) for c in cols)}) "
            f"SELECT {select} FROM VALUES "
        )
        tuple_sql = "(" + ", ".join("?" for _ in cols) + ")"
        per_batch = max(1, min(self._batch_rows, _MAX_PARAMS // len(cols)))
        for start in range(0, len(rows), per_batch):
            batch = rows[start : start + per_batch]
            params = [
                _bind(kind, row[c]) for row in batch for c, kind in zip(cols, kinds, strict=True)
            ]
            self._exec(head + ", ".join(tuple_sql for _ in batch), params)


def _select_expr(kind: str, position: int) -> str:
    if kind == "json":
        return f"PARSE_JSON(${position})"
    if kind == "timestamp":
        return f"TO_TIMESTAMP_TZ(${position})"
    return f"${position}"


def _bind(kind: str, value: Any) -> Any:
    if value is None:
        return None
    if kind == "json":
        return json.dumps(value)
    if kind == "timestamp" and isinstance(value, datetime):
        return value.isoformat()
    return value


def _load_private_key(settings: Mapping[str, Any]) -> bytes:
    """Return the PKCS#8 DER bytes the connector expects for key-pair auth."""
    serialization = import_extra("cryptography.hazmat.primitives.serialization", "snowflake")
    if settings.get("private_key_pem"):
        source = "private_key_pem"
        pem = str(settings["private_key_pem"]).encode()
    else:
        source = f"private_key_path ({settings['private_key_path']})"
        try:
            pem = Path(str(settings["private_key_path"])).expanduser().read_bytes()
        except OSError as exc:
            raise WarehouseError(
                f"Snowflake: cannot read the key file named by {source}: {exc.strerror}. "
                "Fix the path"
            ) from None
    passphrase = settings.get("private_key_passphrase")
    try:
        key = serialization.load_pem_private_key(
            pem, password=str(passphrase).encode() if passphrase else None
        )
    except (ValueError, TypeError):
        raise WarehouseError(
            f"Snowflake: cannot load the private key from {source}; it must be a PEM "
            "(PKCS#8) private key, and private_key_passphrase must be set (and correct) "
            "exactly when the key is encrypted"
        ) from None
    der: bytes = key.private_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    return der


def _scrub(text: str, settings: Mapping[str, Any]) -> str:
    for key in _SECRET_KEYS:
        value = settings.get(key)
        if value:
            text = text.replace(str(value), "<redacted>")
    return text
