"""Warehouse writers (SPEC 6 steps 4-7).

`Writer` is the protocol every warehouse writer implements. The publish command drives
it in this order: `ensure_structure`, `max_spec_version` (spec version guard),
`read_project_row` (ownership guard), `swap`, then `close`. `unpublish` replaces `swap`
for the `unpublish` command.

Writers import their warehouse client library lazily through `import_extra`, so the
pure modules never depend on one.
"""

from __future__ import annotations

import importlib
import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import ModuleType
from typing import Any, Protocol, TypeVar, runtime_checkable

from dbt_agents_schema.plan import ANALYST_SKILL_KEY, PROVIDER, SHARED_ROOT_KEYS, Plan

__all__ = [
    "EnsureResult",
    "MissingExtraError",
    "RootScope",
    "SwapResult",
    "UnpublishResult",
    "WarehouseError",
    "Writer",
    "import_extra",
    "retry_on_conflict",
]

_log = logging.getLogger(__name__)
_R = TypeVar("_R")

CONFLICT_RETRIES = 3


class WarehouseError(Exception):
    """A warehouse operation failed (publish exit code 3)."""


class MissingExtraError(ImportError):
    """The warehouse client library for a writer is not installed (exit code 2)."""


def import_extra(module: str, extra: str) -> ModuleType:
    """Import an optional client library, or explain which extra installs it."""
    try:
        return importlib.import_module(module)
    except ImportError as exc:
        raise MissingExtraError(
            f"the {extra} writer needs the Python module {module!r}, which is not "
            f'installed; fix: pip install "dbt-agents-schema[{extra}]"'
        ) from exc


@dataclass(frozen=True)
class EnsureResult:
    """Columns added to existing tables, per table (migration of older tables)."""

    columns_added: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class SwapResult:
    """Rows inserted per table and legacy `project IS NULL` rows removed per table."""

    rows_written: Mapping[str, int]
    legacy_rows_removed: Mapping[str, int]

    @property
    def total_written(self) -> int:
        return sum(self.rows_written.values())

    @property
    def total_legacy_removed(self) -> int:
        return sum(self.legacy_rows_removed.values())


@dataclass(frozen=True)
class UnpublishResult:
    """Rows deleted per table."""

    rows_deleted: Mapping[str, int]


@dataclass(frozen=True)
class RootScope:
    """Which `provider = 'dbt'` ROOT keys one operation owns (SPEC 5.6, 6 step 7).

    `project` is validated by config as `^[a-z][a-z0-9_]*$`; `_` is still a LIKE
    wildcard, so SQL writers must use `skill_like_pattern` (escaped) or a prefix
    function, never a raw `LIKE 'skill/<project>/%'`.
    """

    provider: str
    shared_keys: tuple[str, ...]
    project_key: str
    skill_prefix: str

    @classmethod
    def for_publish(cls, project: str) -> RootScope:
        return cls(
            provider=PROVIDER,
            shared_keys=(*SHARED_ROOT_KEYS, ANALYST_SKILL_KEY),
            project_key=f"project/{project}",
            skill_prefix=f"skill/{project}/",
        )

    @classmethod
    def for_unpublish(cls, project: str) -> RootScope:
        return cls(
            provider=PROVIDER,
            shared_keys=(),
            project_key=f"project/{project}",
            skill_prefix=f"skill/{project}/",
        )

    def owns(self, key: str) -> bool:
        return (
            key in self.shared_keys or key == self.project_key or key.startswith(self.skill_prefix)
        )

    def skill_like_pattern(self, escape: str) -> str:
        """`LIKE` pattern for the skill prefix, escaping `%`, `_` and `escape` itself."""
        escaped = (
            self.skill_prefix.replace(escape, escape + escape)
            .replace("%", escape + "%")
            .replace("_", escape + "_")
        )
        return escaped + "%"


@runtime_checkable
class Writer(Protocol):
    """One connection to one agents schema (database + schema already resolved).

    Implementations quote identifiers only through their dialect's helper and bind
    every value as a parameter. Failures raise `WarehouseError`.
    """

    def ensure_structure(self) -> EnsureResult:
        """Create the schema and the six tables if missing, then add missing columns.

        Never drops a table or column. Returns the columns added to tables that
        already existed (this migrates tables created by the dbt Labs tool).
        """
        ...

    def read_project_row(self, project: str) -> Mapping[str, Any] | None:
        """Return this project's `DBT_PROJECT` row as a column->value mapping, or None."""
        ...

    def max_spec_version(self) -> int | None:
        """Return the highest `DBT_PROJECT.spec_version`, or None when there is none."""
        ...

    def swap(self, plan: Plan) -> SwapResult:
        """Replace `plan.project`'s rows with the plan's rows (SPEC 6 step 7).

        Deletes this project's rows and legacy `project IS NULL` rows from the
        project-scoped tables, and the ROOT rows of `RootScope.for_publish`, then
        inserts every plan row. Atomic: on failure the previous rows survive.
        """
        ...

    def unpublish(self, project: str) -> UnpublishResult:
        """Delete one project's rows, including its ROOT rows (`RootScope.for_unpublish`).

        Shared ROOT rows and other projects' rows are untouched. Atomic like `swap`.
        """
        ...

    def close(self) -> None:
        """Release the connection. Safe to call more than once."""
        ...


def retry_on_conflict(
    fn: Callable[[], _R],
    *,
    errors: tuple[type[BaseException], ...],
    conflict: re.Pattern[str],
    retries: int = CONFLICT_RETRIES,
    sleep: Callable[[float], None] = time.sleep,
) -> _R:
    """Run `fn`, re-running it up to `retries` times when it fails with one of `errors`
    whose message matches `conflict` (a concurrent write by another publish).

    Only for operations that are safe to repeat from the start (a whole swap).
    """
    attempt = 0
    while True:
        try:
            return fn()
        except errors as exc:
            if attempt >= retries or not conflict.search(str(exc)):
                raise
            attempt += 1
            delay = float(2 ** (attempt - 1))
            _log.warning(
                "concurrent write by another publish; retrying in %.0fs (attempt %d of %d)",
                delay,
                attempt,
                retries,
            )
            sleep(delay)
