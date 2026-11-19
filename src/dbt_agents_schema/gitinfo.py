"""Git metadata for `DBT_PROJECT` (SPEC 7): CI variables, else local git, else None."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from dbt_agents_schema.publish import GitInfo

Runner = Callable[..., "subprocess.CompletedProcess[str]"]

_SCP_LIKE = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>(?!//).+)$")


def detect_git(
    project_dir: Path, environ: Mapping[str, str], run: Runner = subprocess.run
) -> GitInfo:
    if environ.get("GITHUB_REPOSITORY"):
        server = environ.get("GITHUB_SERVER_URL") or "https://github.com"
        return GitInfo(
            repo_url=clean_repo_url(f"{server.rstrip('/')}/{environ['GITHUB_REPOSITORY']}"),
            sha=environ.get("GITHUB_SHA") or None,
            branch=environ.get("GITHUB_REF_NAME") or None,
        )
    if environ.get("CI_PROJECT_URL"):
        return GitInfo(
            repo_url=clean_repo_url(environ["CI_PROJECT_URL"]),
            sha=environ.get("CI_COMMIT_SHA") or None,
            branch=environ.get("CI_COMMIT_REF_NAME") or None,
        )
    remote = _git(run, project_dir, "remote", "get-url", "origin")
    branch = _git(run, project_dir, "rev-parse", "--abbrev-ref", "HEAD")
    return GitInfo(
        repo_url=clean_repo_url(remote) if remote else None,
        sha=_git(run, project_dir, "rev-parse", "HEAD"),
        branch=None if branch == "HEAD" else branch,
    )


def clean_repo_url(url: str) -> str | None:
    """Strip credentials and normalize a remote URL so CI and local runs agree.

    `https://user:token@host/a/b.git` and `git@host:a/b.git` both become
    `https://host/a/b`. Userinfo is always dropped, so tokens never reach the warehouse.
    """
    url = url.strip()
    if not url:
        return None
    parts = urlsplit(url)
    if parts.scheme in ("http", "https", "ssh", "git", "git+ssh"):
        host = parts.hostname or ""
        port = parts.port
        if parts.scheme in ("http", "https"):
            netloc = host + (f":{port}" if port else "")
            scheme = parts.scheme
        else:
            netloc, scheme = host, "https"
        return urlunsplit((scheme, netloc, _strip_path(parts.path), "", ""))
    scp = _SCP_LIKE.match(url)
    if scp and "://" not in url and not url.startswith("/"):
        return f"https://{scp.group('host')}/{_strip_path(scp.group('path')).lstrip('/')}"
    return _strip_path(url)


def _strip_path(path: str) -> str:
    path = path.rstrip("/")
    return path[: -len(".git")] if path.endswith(".git") else path


def _git(run: Runner, cwd: Path, *args: str) -> str | None:
    try:
        done: Any = run(
            ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if done.returncode != 0:
        return None
    out = str(done.stdout).strip()
    return out or None
