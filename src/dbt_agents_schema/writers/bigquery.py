"""BigQuery writer (SPEC 6). Needs the `bigquery` extra.

- "database" is the GCP project, "schema" the dataset (SPEC 4.1). Table names are
  uppercase and case-sensitive; column names are lowercase (BigQuery column names are
  case-insensitive). Identifiers are backtick-quoted by the dialect helper.
- DDL (`CREATE SCHEMA`, `CREATE TABLE`, `ALTER TABLE ... ADD COLUMN IF NOT EXISTS`) runs
  as separate jobs, outside any transaction (BigQuery forbids DDL in transactions).
- The swap is one multi-statement script:
  `BEGIN BEGIN TRANSACTION; <deletes>; <inserts>; COMMIT TRANSACTION;
  EXCEPTION WHEN ERROR THEN ROLLBACK TRANSACTION; RAISE ...; END;`
- Rows reach the script one of two ways, chosen per table by volume: small tables as a
  query parameter holding an `ARRAY<STRUCT>` read with `UNNEST`; tables above
  `staging_rows` rows or `staging_bytes` bytes are first loaded with a load job into a
  staging table (expires after one day, dropped after the swap). The swap is the atomic
  step either way. JSON values travel as text and are converted with `PARSE_JSON`.
- Values are always query parameters or load-job data, never SQL text.
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from types import ModuleType
from typing import Any, TypeVar

from dbt_agents_schema.dialects import LOGICAL_TYPES
from dbt_agents_schema.dialects.bigquery import DIALECT
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

STAGING_ROWS = 2000
STAGING_BYTES = 1_000_000
STAGE_PREFIX = "_DBT_AGENTS_SCHEMA_STAGE_"

# Parameter / load-job type per logical type (JSON travels as text).
_PARAM_TYPES = {
    "varchar": "STRING",
    "text": "STRING",
    "boolean": "BOOL",
    "integer": "INT64",
    "timestamp": "TIMESTAMP",
    "json": "STRING",
}

_SCOPES = ["https://www.googleapis.com/auth/bigquery"]


def _bq() -> ModuleType:
    return import_extra("google.cloud.bigquery", "bigquery")


def _default_errors() -> tuple[tuple[type[BaseException], ...], tuple[type[BaseException], ...]]:
    api = import_extra("google.api_core.exceptions", "bigquery")
    auth = import_extra("google.auth.exceptions", "bigquery")
    return (api.GoogleAPIError, auth.GoogleAuthError), (api.NotFound,)


class BigQueryWriter:
    """Writer for one agents schema (dataset) in BigQuery, over a `bigquery.Client`."""

    def __init__(
        self,
        client: Any,
        *,
        project: str,
        dataset: str,
        location: str | None = None,
        error_types: tuple[type[BaseException], ...] | None = None,
        not_found_types: tuple[type[BaseException], ...] | None = None,
        staging_rows: int = STAGING_ROWS,
        staging_bytes: int = STAGING_BYTES,
    ) -> None:
        self._client = client
        self._project = project
        self._dataset = dataset
        self._location = location
        self._staging_rows = staging_rows
        self._staging_bytes = staging_bytes
        self._bigquery = _bq()
        if error_types is None or not_found_types is None:
            default_errors, default_not_found = _default_errors()
            error_types = error_types if error_types is not None else default_errors
            not_found_types = not_found_types if not_found_types is not None else default_not_found
        self._error_types = error_types
        self._not_found_types = not_found_types
        self._owns_client = False
        self._closed = False

    @classmethod
    def connect(
        cls, settings: Mapping[str, Any], *, dataset: str, database: str | None = None
    ) -> BigQueryWriter:
        """Open a client from `WAREHOUSE_CREDENTIALS`-style settings (SPEC 4.1).

        Auth: `credentials_json` (service-account mapping), `keyfile` (service-account
        file path), else application default credentials. `database` (the rendered
        target database) overrides `project_id` as the dataset's project.
        """
        bigquery = _bq()
        credentials = _credentials(settings)
        location = settings.get("location")
        client = bigquery.Client(
            project=settings["project_id"], credentials=credentials, location=location
        )
        writer = cls(
            client,
            project=database or str(settings["project_id"]),
            dataset=dataset,
            location=location,
        )
        writer._owns_client = True
        return writer

    # --- Writer protocol --------------------------------------------------------------

    def ensure_structure(self) -> EnsureResult:
        def run() -> EnsureResult:
            self._run(f"CREATE SCHEMA IF NOT EXISTS {self._dataset_ref()}")
            existing = self._existing_columns()
            added: dict[str, tuple[str, ...]] = {}
            for table in TABLES:
                have = existing.get(table)
                if have is None:
                    defs = ", ".join(
                        f"{DIALECT.quote(c)} {DIALECT.column_type(table, c)}"
                        for c in TABLE_COLUMNS[table]
                    )
                    self._run(f"CREATE TABLE IF NOT EXISTS {self._table(table)} ({defs})")
                    continue
                missing = tuple(c for c in TABLE_COLUMNS[table] if c.lower() not in have)
                for column in missing:
                    self._run(
                        f"ALTER TABLE {self._table(table)} ADD COLUMN IF NOT EXISTS "
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
            rows = self._run(
                f"SELECT {', '.join(DIALECT.quote(c) for c in cols)} "
                f"FROM {self._table('DBT_PROJECT')} WHERE {DIALECT.quote('project')} = @project "
                "LIMIT 1",
                [self._scalar("project", "STRING", project)],
            )
            return dict(zip(cols, list(rows[0]), strict=True)) if rows else None

        return self._guard("read DBT_PROJECT", run)

    def max_spec_version(self) -> int | None:
        def run() -> int | None:
            if "DBT_PROJECT" not in self._existing_columns():
                return None
            rows = self._run(
                f"SELECT max({DIALECT.quote('spec_version')}) FROM {self._table('DBT_PROJECT')}"
            )
            return None if not rows or rows[0][0] is None else int(rows[0][0])

        return self._guard("read DBT_PROJECT.spec_version", run)

    def swap(self, plan: Plan) -> SwapResult:
        def run() -> SwapResult:
            legacy = self._legacy_counts()
            for table, n in legacy.items():
                if n:
                    log.info("removing %d legacy rows from %s", n, table)
            scope = RootScope.for_publish(plan.project)
            params = [self._scalar("project", "STRING", plan.project), *self._root_params(scope)]
            project = DIALECT.quote("project")
            statements = [
                f"DELETE FROM {self._table(t)} WHERE {project} = @project OR {project} IS NULL;"
                for t in PROJECT_TABLES
            ]
            statements.append(self._root_delete())
            stages: list[str] = []
            try:
                for table in TABLES:
                    rows = plan.rows(table)
                    if not rows:
                        continue
                    if self._should_stage(rows):
                        stage = self._stage(table, rows, stages)
                        source = f"{self._table(stage)} AS r"
                    else:
                        params.append(self._rows_param(table, rows))
                        source = f"UNNEST(@rows_{table}) AS r"
                    statements.append(self._insert(table, source))
                self._run(_script(statements), params)
            finally:
                self._drop_stages(stages)
            return SwapResult(rows_written=plan.counts(), legacy_rows_removed=legacy)

        return self._guard(f"replace the rows of project {plan.project!r}", run)

    def unpublish(self, project: str) -> UnpublishResult:
        def run() -> UnpublishResult:
            deleted: dict[str, int] = dict.fromkeys(TABLES, 0)
            present = [t for t in TABLES if t in self._existing_columns()]
            if not present:
                return UnpublishResult(rows_deleted=deleted)
            scope = RootScope.for_unpublish(project)
            params = [self._scalar("project", "STRING", project), *self._root_params(scope)]
            where_project = f"{DIALECT.quote('project')} = @project"
            counts = []
            statements = []
            for table in present:
                where = self._root_where() if table == "ROOT" else where_project
                counts.append(
                    f"SELECT '{table}' AS t, count(*) AS n FROM {self._table(table)} WHERE {where}"
                )
                statements.append(f"DELETE FROM {self._table(table)} WHERE {where};")
            for row in self._run(" UNION ALL ".join(counts), params):
                deleted[str(row[0])] = int(row[1])
            self._run(_script(statements), params)
            return UnpublishResult(rows_deleted=deleted)

        return self._guard(f"delete the rows of project {project!r}", run)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        if self._owns_client:
            self._client.close()

    # --- internals --------------------------------------------------------------------

    def _guard(self, action: str, fn: Callable[[], _T]) -> _T:
        try:
            return fn()
        except self._error_types as exc:
            raise WarehouseError(
                f"BigQuery: could not {action} in {self._dataset_ref()}: {exc}"
            ) from None

    def _run(self, sql: str, params: Sequence[Any] = ()) -> list[Any]:
        config = self._bigquery.QueryJobConfig(query_parameters=list(params))
        job = self._client.query(sql, job_config=config, location=self._location)
        return list(job.result())

    def _dataset_ref(self) -> str:
        return DIALECT.qualified(self._project, self._dataset)

    def _table(self, table: str) -> str:
        return DIALECT.qualified(self._project, self._dataset, table)

    def _existing_columns(self) -> dict[str, set[str]]:
        """Lowercased column names per existing table; empty when the dataset is missing."""
        try:
            rows = self._run(
                f"SELECT table_name, column_name FROM {self._dataset_ref()}"
                ".INFORMATION_SCHEMA.COLUMNS"
            )
        except self._not_found_types:
            return {}
        found: dict[str, set[str]] = {}
        for row in rows:
            found.setdefault(str(row[0]), set()).add(str(row[1]).lower())
        return found

    def _scalar(self, name: str | None, type_: str, value: Any) -> Any:
        return self._bigquery.ScalarQueryParameter(name, type_, value)

    def _root_params(self, scope: RootScope) -> list[Any]:
        return [
            self._scalar("provider", "STRING", scope.provider),
            self._bigquery.ArrayQueryParameter(
                "root_keys", "STRING", [*scope.shared_keys, scope.project_key]
            ),
            self._scalar("skill_prefix", "STRING", scope.skill_prefix),
        ]

    def _root_where(self) -> str:
        provider, key = DIALECT.quote("provider"), DIALECT.quote("key")
        return (
            f"{provider} = @provider AND ({key} IN UNNEST(@root_keys) "
            f"OR STARTS_WITH({key}, @skill_prefix))"
        )

    def _root_delete(self) -> str:
        return f"DELETE FROM {self._table('ROOT')} WHERE {self._root_where()};"

    def _legacy_counts(self) -> dict[str, int]:
        project = DIALECT.quote("project")
        sql = " UNION ALL ".join(
            f"SELECT '{t}' AS t, COUNTIF({project} IS NULL) AS n FROM {self._table(t)}"
            for t in PROJECT_TABLES
        )
        counts = dict.fromkeys(PROJECT_TABLES, 0)
        for row in self._run(sql):
            counts[str(row[0])] = int(row[1] or 0)
        return counts

    def _should_stage(self, rows: Sequence[Row]) -> bool:
        if len(rows) > self._staging_rows:
            return True
        size = len(json.dumps([dict(r) for r in rows], default=str))
        return size > self._staging_bytes

    def _insert(self, table: str, source: str) -> str:
        cols = TABLE_COLUMNS[table]
        select = ", ".join(
            f"PARSE_JSON(r.{DIALECT.quote(c)})"
            if LOGICAL_TYPES[table][c] == "json"
            else f"r.{DIALECT.quote(c)}"
            for c in cols
        )
        names = ", ".join(DIALECT.quote(c) for c in cols)
        return f"INSERT INTO {self._table(table)} ({names}) SELECT {select} FROM {source};"

    def _rows_param(self, table: str, rows: Sequence[Row]) -> Any:
        bq = self._bigquery
        cols = TABLE_COLUMNS[table]
        kinds = [LOGICAL_TYPES[table][c] for c in cols]
        values = [
            bq.StructQueryParameter(
                None,
                *(
                    bq.ScalarQueryParameter(c, _PARAM_TYPES[k], _param_value(k, row[c]))
                    for c, k in zip(cols, kinds, strict=True)
                ),
            )
            for row in rows
        ]
        return bq.ArrayQueryParameter(f"rows_{table}", "STRUCT", values)

    def _stage(self, table: str, rows: Sequence[Row], stages: list[str]) -> str:
        bq = self._bigquery
        name = f"{STAGE_PREFIX}{table}_{secrets.token_hex(4)}"
        cols = TABLE_COLUMNS[table]
        kinds = [LOGICAL_TYPES[table][c] for c in cols]
        defs = ", ".join(
            f"{DIALECT.quote(c)} {_PARAM_TYPES[k]}" for c, k in zip(cols, kinds, strict=True)
        )
        stages.append(name)
        self._run(
            f"CREATE TABLE {self._table(name)} ({defs}) OPTIONS(expiration_timestamp = "
            "TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL 1 DAY))"
        )
        config = bq.LoadJobConfig(
            schema=[
                bq.SchemaField(c, _PARAM_TYPES[k], mode="NULLABLE")
                for c, k in zip(cols, kinds, strict=True)
            ],
            write_disposition="WRITE_APPEND",
        )
        data = [
            {c: _load_value(k, row[c]) for c, k in zip(cols, kinds, strict=True)} for row in rows
        ]
        job = self._client.load_table_from_json(
            data,
            f"{self._project}.{self._dataset}.{name}",
            job_config=config,
            location=self._location,
        )
        job.result()
        return name

    def _drop_stages(self, stages: Sequence[str]) -> None:
        for name in stages:
            try:
                self._run(f"DROP TABLE IF EXISTS {self._table(name)}")
            except self._error_types as exc:
                log.warning("could not drop staging table %s (it expires in a day): %s", name, exc)


def _script(statements: Sequence[str]) -> str:
    body = "\n".join(f"  {s}" for s in statements)
    return (
        "BEGIN\n"
        "  BEGIN TRANSACTION;\n"
        f"{body}\n"
        "  COMMIT TRANSACTION;\n"
        "EXCEPTION WHEN ERROR THEN\n"
        "  ROLLBACK TRANSACTION;\n"
        "  RAISE USING MESSAGE = @@error.message;\n"
        "END;"
    )


def _param_value(kind: str, value: Any) -> Any:
    if value is None:
        return None
    if kind == "json":
        return json.dumps(value)
    return value


def _load_value(kind: str, value: Any) -> Any:
    if value is None:
        return None
    if kind == "json":
        return json.dumps(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value


def _credentials(settings: Mapping[str, Any]) -> Any:
    if settings.get("credentials_json") is not None:
        service_account = import_extra("google.oauth2.service_account", "bigquery")
        try:
            return service_account.Credentials.from_service_account_info(
                dict(settings["credentials_json"]), scopes=_SCOPES
            )
        except (ValueError, KeyError, TypeError):
            raise WarehouseError(
                "BigQuery: credentials_json is not a valid service-account key (it needs "
                "client_email, private_key and token_uri); paste the whole key file contents"
            ) from None
    if settings.get("keyfile"):
        service_account = import_extra("google.oauth2.service_account", "bigquery")
        try:
            return service_account.Credentials.from_service_account_file(
                str(settings["keyfile"]), scopes=_SCOPES
            )
        except (OSError, ValueError, KeyError, TypeError):
            raise WarehouseError(
                f"BigQuery: cannot load the service-account key file {settings['keyfile']} "
                "(keyfile); fix the path or the file contents"
            ) from None
    google_auth = import_extra("google.auth", "bigquery")
    auth_errors = import_extra("google.auth.exceptions", "bigquery")
    try:
        credentials, _ = google_auth.default(scopes=_SCOPES)
    except auth_errors.DefaultCredentialsError:
        raise WarehouseError(
            "BigQuery: no application default credentials found (auth_method: adc); run "
            "`gcloud auth application-default login` or set credentials_json"
        ) from None
    return credentials
