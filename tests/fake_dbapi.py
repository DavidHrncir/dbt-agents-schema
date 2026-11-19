"""A fake DB-API 2.0 connection that records every statement and its parameters.

Used by the warehouse writer tests (Phase 3). Results are scripted by regular expression:
the first rule whose pattern matches the SQL text (case-insensitive search) supplies the
rows, either as a list or as a callable taking the bound parameters.
`fail_on` makes matching statements raise `FakeDBError` (only the first `fail_times`
matches when set, with `fail_message` as the error text when set).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any


class FakeDBError(Exception):
    """Stands in for the driver's error base class."""


Rows = list[tuple[Any, ...]] | Callable[[Any], list[tuple[Any, ...]]]


@dataclass
class Statement:
    sql: str
    params: Any = None
    many: bool = False


@dataclass
class FakeConnection:
    results: list[tuple[str, Rows]] = field(default_factory=list)
    fail_on: str | None = None
    fail_times: int | None = None
    fail_message: str | None = None
    statements: list[Statement] = field(default_factory=list)
    closed: bool = False
    rowcount: int = 0

    def cursor(self) -> FakeCursor:
        return FakeCursor(self)

    def close(self) -> None:
        self.closed = True

    def commit(self) -> None:
        self.statements.append(Statement("<commit()>"))

    def rollback(self) -> None:
        self.statements.append(Statement("<rollback()>"))

    # --- helpers for assertions -------------------------------------------------------

    def sql(self) -> list[str]:
        return [s.sql for s in self.statements]

    def matching(self, pattern: str) -> list[Statement]:
        return [s for s in self.statements if re.search(pattern, s.sql, re.IGNORECASE)]


class FakeCursor:
    def __init__(self, con: FakeConnection) -> None:
        self._con = con
        self._rows: list[tuple[Any, ...]] = []
        self.rowcount = -1
        self.description: Sequence[Any] | None = None

    def _run(self, sql: str, params: Any, many: bool) -> None:
        self._con.statements.append(Statement(sql, params, many))
        con = self._con
        failing = con.fail_on and re.search(con.fail_on, sql, re.IGNORECASE)
        if failing and (con.fail_times is None or con.fail_times > 0):
            if con.fail_times is not None:
                con.fail_times -= 1
            raise FakeDBError(con.fail_message or f"scripted failure for: {sql[:60]}")
        self._rows = []
        for pattern, rows in self._con.results:
            if re.search(pattern, sql, re.IGNORECASE):
                self._rows = list(rows(params) if callable(rows) else rows)
                break
        self.rowcount = self._con.rowcount

    def execute(self, sql: str, params: Any = None) -> FakeCursor:
        self._run(sql, params, False)
        return self

    def executemany(self, sql: str, seq: Any) -> FakeCursor:
        self._run(sql, list(seq), True)
        return self

    def fetchone(self) -> tuple[Any, ...] | None:
        return self._rows.pop(0) if self._rows else None

    def fetchall(self) -> list[tuple[Any, ...]]:
        rows, self._rows = self._rows, []
        return rows

    def close(self) -> None:
        pass
