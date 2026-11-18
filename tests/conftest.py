"""Suite-wide guards: no network outside `@pytest.mark.live` tests."""

from __future__ import annotations

import importlib
from typing import Any

import pytest


def _offline(error: type[Exception]) -> Any:
    def connect(*args: Any, **kwargs: Any) -> None:
        # Echo every argument back, as a careless driver might, so tests that assert
        # secrets are never printed exercise the writer's scrubbing.
        raise error(f"network disabled in unit tests; connect({args!r}, {kwargs!r})")

    return connect


@pytest.fixture(autouse=True)
def _no_warehouse_network(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    if request.node.get_closest_marker("live"):
        return
    try:
        snowflake = importlib.import_module("snowflake.connector")
        monkeypatch.setattr(snowflake, "connect", _offline(snowflake.errors.DatabaseError))
    except ImportError:
        pass
    try:
        bigquery = importlib.import_module("google.cloud.bigquery")
        google_auth = importlib.import_module("google.auth")
        auth_errors = importlib.import_module("google.auth.exceptions")
        monkeypatch.setattr(bigquery, "Client", _offline(auth_errors.GoogleAuthError))
        monkeypatch.setattr(google_auth, "default", _offline(auth_errors.DefaultCredentialsError))
    except ImportError:
        pass
