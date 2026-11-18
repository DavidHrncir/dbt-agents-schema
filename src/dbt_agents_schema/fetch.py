"""`fetch-manifest`: download the latest manifest.json of a dbt platform job (SPEC 7).

Endpoint (dbt platform Administrative API v2, operation "Retrieve Job Artifact"):
    GET https://<host>/api/v2/accounts/<account_id>/jobs/<job_id>/artifacts/manifest.json
    Authorization: Bearer <DBT_CLOUD_API_TOKEN>
It returns the artifact from the job's latest run that produced it. Source:
https://github.com/dbt-labs/dbt-cloud-openapi-spec/blob/main/openapi-v2.yaml
(reference: https://docs.getdbt.com/dbt-cloud/api-v2). v3 has no artifact routes.

The HTTP layer is a plain callable so tests inject a fake; the default uses urllib.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from dbt_agents_schema import __version__

TOKEN_ENV = "DBT_CLOUD_API_TOKEN"
DEFAULT_HOST = "cloud.getdbt.com"
TIMEOUT_SECONDS = 120.0
_HOST = re.compile(r"^[A-Za-z0-9]([A-Za-z0-9.-]*[A-Za-z0-9])?(:\d{1,5})?$")


class FetchError(Exception):
    """fetch-manifest failed. `exit_code` 2: input, token or not found; 3: remote error."""

    def __init__(self, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.exit_code = exit_code


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes
    content_type: str


Http = Callable[[str, Mapping[str, str], float], HttpResponse]


def build_request(url: str, headers: Mapping[str, str]) -> urllib.request.Request:
    """A GET request whose headers are not copied onto redirects (they may change host)."""
    request = urllib.request.Request(url, method="GET")
    for name, value in headers.items():
        request.add_unredirected_header(name, value)
    return request


def _urllib_http(url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse:
    request = build_request(url, headers)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return HttpResponse(
                response.status, response.read(), response.headers.get_content_type()
            )
    except urllib.error.HTTPError as exc:
        return HttpResponse(exc.code, exc.read(), exc.headers.get_content_type())


default_http: Http = _urllib_http


def artifact_url(host: str, account_id: str, job_id: str) -> str:
    """The Retrieve Job Artifact URL for manifest.json. Host may include `https://`."""
    bare = host.strip()
    if bare.startswith("https://"):
        bare = bare[len("https://") :]
    bare = bare.rstrip("/")
    if not _HOST.match(bare):
        raise FetchError(
            f"--host {host!r} is not a host name; pass the dbt platform access URL host, "
            f"for example {DEFAULT_HOST} or ab123.us1.dbt.com (https only, no path)",
            2,
        )
    for flag, value in (("--account-id", account_id), ("--job-id", job_id)):
        if not value.isdigit():
            raise FetchError(f"{flag} must be a number (the id in the dbt platform URL)", 2)
    return f"https://{bare}/api/v2/accounts/{account_id}/jobs/{job_id}/artifacts/manifest.json"


def fetch_manifest(
    *,
    account_id: str,
    job_id: str,
    host: str,
    token: str,
    out: Path,
    http: Http | None = None,
) -> Path:
    """Download the manifest and write it to `out` atomically. Returns `out`."""
    url = artifact_url(host, account_id, job_id)
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "User-Agent": f"dbt-agents-schema/{__version__}",
    }
    get = http if http is not None else default_http
    where = f"job {job_id} in account {account_id} on {url.split('/')[2]}"
    try:
        response = get(url, headers, TIMEOUT_SECONDS)
    except OSError as exc:
        raise FetchError(f"cannot reach {url.split('/')[2]}: {exc}; check --host", 3) from None

    if response.status in (401, 403):
        raise FetchError(
            f"dbt platform rejected the token (HTTP {response.status}{_detail(response)}); "
            f"check that {TOKEN_ENV} is a valid service or personal token with access to "
            f"account {account_id}, and that --host is your account's access URL",
            2,
        )
    if response.status == 404:
        raise FetchError(
            f"no manifest.json found for {where} (HTTP 404{_detail(response)}); check "
            "--account-id, --job-id and --host, and that the job has a run that generated "
            "docs or a manifest",
            2,
        )
    if response.status != 200:
        raise FetchError(
            f"dbt platform returned HTTP {response.status}{_detail(response)} for {where}; "
            "retry later",
            3,
        )
    try:
        data = json.loads(response.body)
    except (UnicodeDecodeError, ValueError):
        raise FetchError(
            f"the response for {where} is not JSON (content type {response.content_type}); "
            "check --host points at the dbt platform API",
            3,
        ) from None
    if not isinstance(data, dict) or not isinstance(data.get("metadata"), dict):
        raise FetchError(
            f"the response for {where} is JSON but not a dbt manifest (no `metadata`)", 3
        )

    out.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{out.name}.", suffix=".tmp", dir=out.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(response.body)
        os.replace(tmp, out)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return out


def _detail(response: HttpResponse) -> str:
    """`: <user_message>` from a dbt platform JSON error body, if present."""
    try:
        data = json.loads(response.body)
        message = data["status"]["user_message"]
    except (ValueError, KeyError, TypeError, UnicodeDecodeError):
        return ""
    return f": {message}" if isinstance(message, str) and message else ""
