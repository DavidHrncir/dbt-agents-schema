"""Git metadata for DBT_PROJECT (SPEC 7; T14)."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest

from dbt_agents_schema.gitinfo import clean_repo_url, detect_git


def no_git(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
    raise AssertionError("git must not run when CI variables are present")


def test_github_env() -> None:
    info = detect_git(
        Path("."),
        {
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_REPOSITORY": "acme/finance",
            "GITHUB_SHA": "abc",
            "GITHUB_REF_NAME": "main",
        },
        run=no_git,
    )
    assert (info.repo_url, info.sha, info.branch) == (
        "https://github.com/acme/finance",
        "abc",
        "main",
    )


def test_gitlab_env() -> None:
    info = detect_git(
        Path("."),
        {
            "CI_PROJECT_URL": "https://gitlab.example.com/acme/finance",
            "CI_COMMIT_SHA": "def",
            "CI_COMMIT_REF_NAME": "release",
        },
        run=no_git,
    )
    assert (info.repo_url, info.sha, info.branch) == (
        "https://gitlab.example.com/acme/finance",
        "def",
        "release",
    )


def fake_git(outputs: dict[str, str | None]) -> Any:
    def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        key = " ".join(cmd[1:])
        out = outputs.get(key)
        if out is None:
            return subprocess.CompletedProcess(cmd, 128, "", "fatal")
        return subprocess.CompletedProcess(cmd, 0, out + "\n", "")

    return run


def test_local_git() -> None:
    info = detect_git(
        Path("."),
        {},
        run=fake_git(
            {
                "remote get-url origin": "https://user:tok3n@github.com/acme/finance.git",
                "rev-parse HEAD": "0123",
                "rev-parse --abbrev-ref HEAD": "feature/x",
            }
        ),
    )
    assert info.repo_url == "https://github.com/acme/finance"
    assert info.sha == "0123"
    assert info.branch == "feature/x"


def test_local_git_detached_head_and_no_remote() -> None:
    info = detect_git(
        Path("."),
        {},
        run=fake_git({"rev-parse HEAD": "0123", "rev-parse --abbrev-ref HEAD": "HEAD"}),
    )
    assert (info.repo_url, info.sha, info.branch) == (None, "0123", None)


def test_no_git_at_all() -> None:
    def missing(*args: Any, **kwargs: Any) -> Any:
        raise FileNotFoundError("git")

    info = detect_git(Path("."), {}, run=missing)
    assert (info.repo_url, info.sha, info.branch) == (None, None, None)


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("https://github.com/acme/finance", "https://github.com/acme/finance"),
        ("https://github.com/acme/finance.git", "https://github.com/acme/finance"),
        (
            "https://x-access-token:ghs_SECRET@github.com/acme/finance.git",
            "https://github.com/acme/finance",
        ),
        ("https://ghp_SECRET@github.com/acme/finance", "https://github.com/acme/finance"),
        ("https://gitlab-ci-token:SECRET@gitlab.com:8443/a/b.git/", "https://gitlab.com:8443/a/b"),
        ("git@github.com:acme/finance.git", "https://github.com/acme/finance"),
        ("ssh://git@github.com/acme/finance.git", "https://github.com/acme/finance"),
        ("ssh://git:pw@host:2222/acme/finance", "https://host/acme/finance"),
        ("/srv/repos/finance.git", "/srv/repos/finance"),
        ("", None),
    ],
)
def test_clean_repo_url(raw: str, clean: str | None) -> None:
    assert clean_repo_url(raw) == clean
