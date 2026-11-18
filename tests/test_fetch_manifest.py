"""`fetch-manifest`: latest manifest.json of a dbt platform job (SPEC 7, T25).

Every test injects a fake HTTP layer; nothing touches the network.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from dbt_agents_schema import fetch
from dbt_agents_schema.cli import main
from dbt_agents_schema.fetch import FetchError, HttpResponse, artifact_url, fetch_manifest

TOKEN = "dbtc_SECRET_token_value"
MANIFEST = {
    "metadata": {"dbt_schema_version": "https://schemas.getdbt.com/dbt/manifest/v12.json"},
    "nodes": {},
}


class FakeHttp:
    def __init__(self, response: HttpResponse | Exception) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def __call__(self, url: str, headers: Mapping[str, str], timeout: float) -> HttpResponse:
        self.calls.append((url, dict(headers), timeout))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def ok(body: bytes, content_type: str = "application/json") -> HttpResponse:
    return HttpResponse(status=200, body=body, content_type=content_type)


def run(http: FakeHttp, out: Path, host: str = "cloud.getdbt.com") -> Path:
    return fetch_manifest(account_id="12", job_id="345", host=host, token=TOKEN, out=out, http=http)


def test_success_writes_manifest(tmp_path: Path) -> None:
    body = json.dumps(MANIFEST).encode()
    http = FakeHttp(ok(body))
    out = tmp_path / "target" / "manifest.json"
    assert run(http, out) == out
    assert out.read_bytes() == body
    ((url, headers, timeout),) = http.calls
    assert url == "https://cloud.getdbt.com/api/v2/accounts/12/jobs/345/artifacts/manifest.json"
    assert headers["Authorization"] == f"Bearer {TOKEN}"
    assert headers["Accept"] == "application/json"
    assert timeout > 0
    assert not list(out.parent.glob("*.tmp*")), "temporary file left behind"


@pytest.mark.parametrize(
    "host",
    [
        "ab123.us1.dbt.com",
        "https://ab123.us1.dbt.com",
        "https://ab123.us1.dbt.com/",
        " ab123.us1.dbt.com ",
    ],
)
def test_host_forms(host: str) -> None:
    assert artifact_url(host, "1", "2") == (
        "https://ab123.us1.dbt.com/api/v2/accounts/1/jobs/2/artifacts/manifest.json"
    )


@pytest.mark.parametrize("bad", ["http://cloud.getdbt.com", "cloud.getdbt.com/path", "", "a b"])
def test_bad_host(bad: str) -> None:
    with pytest.raises(FetchError) as info:
        artifact_url(bad, "1", "2")
    assert info.value.exit_code == 2 and "--host" in str(info.value)


@pytest.mark.parametrize("ident", ["abc", "-1", "1.5", ""])
def test_ids_must_be_numeric(ident: str, tmp_path: Path) -> None:
    with pytest.raises(FetchError, match="--account-id"):
        fetch_manifest(
            account_id=ident,
            job_id="1",
            host="cloud.getdbt.com",
            token=TOKEN,
            out=tmp_path / "m.json",
            http=FakeHttp(ok(b"{}")),
        )


@pytest.mark.parametrize("status", [401, 403])
def test_unauthorized(status: int, tmp_path: Path) -> None:
    body = json.dumps({"status": {"code": status, "user_message": "Invalid token."}}).encode()
    out = tmp_path / "manifest.json"
    with pytest.raises(FetchError) as info:
        run(FakeHttp(HttpResponse(status, body, "application/json")), out)
    message = str(info.value)
    assert info.value.exit_code == 2
    assert "DBT_CLOUD_API_TOKEN" in message and str(status) in message
    assert TOKEN not in message
    assert not out.exists()


def test_not_found(tmp_path: Path) -> None:
    body = json.dumps({"status": {"code": 404, "user_message": "Resource not found."}}).encode()
    out = tmp_path / "manifest.json"
    with pytest.raises(FetchError) as info:
        run(FakeHttp(HttpResponse(404, body, "application/json")), out)
    message = str(info.value)
    assert info.value.exit_code == 2
    assert "404" in message and "345" in message and "12" in message
    assert "Resource not found." in message
    assert not out.exists()


def test_non_json_body(tmp_path: Path) -> None:
    out = tmp_path / "manifest.json"
    out.write_text("previous")
    with pytest.raises(FetchError) as info:
        run(FakeHttp(ok(b"<html>Sign in</html>", "text/html")), out)
    assert info.value.exit_code == 3
    assert "not JSON" in str(info.value) and "text/html" in str(info.value)
    assert out.read_text() == "previous", "an existing manifest is kept on failure"


def test_json_that_is_not_a_manifest(tmp_path: Path) -> None:
    with pytest.raises(FetchError, match="not a dbt manifest"):
        run(FakeHttp(ok(b'{"data": []}')), tmp_path / "manifest.json")


def test_server_error(tmp_path: Path) -> None:
    with pytest.raises(FetchError) as info:
        run(FakeHttp(HttpResponse(502, b"bad gateway", "text/plain")), tmp_path / "m.json")
    assert info.value.exit_code == 3 and "502" in str(info.value)


def test_network_error(tmp_path: Path) -> None:
    with pytest.raises(FetchError) as info:
        run(FakeHttp(OSError("Name or service not known")), tmp_path / "m.json")
    assert info.value.exit_code == 3 and "cloud.getdbt.com" in str(info.value)


def test_interrupted_download(tmp_path: Path) -> None:
    import http.client

    with pytest.raises(FetchError) as info:
        run(FakeHttp(http.client.IncompleteRead(b'{"meta', 1000)), tmp_path / "m.json")
    assert info.value.exit_code == 3 and "IncompleteRead" in str(info.value)


def test_token_is_not_forwarded_on_redirect() -> None:
    request = fetch.build_request("https://cloud.getdbt.com/x", {"Authorization": "Bearer t"})
    assert request.get_header("Authorization") == "Bearer t"
    assert "Authorization" not in request.headers, "redirects would copy it to another host"


# --- CLI -------------------------------------------------------------------------------


def test_cli_success_default_out(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    http = FakeHttp(ok(json.dumps(MANIFEST).encode()))
    monkeypatch.setattr(fetch, "default_http", http)
    monkeypatch.setenv("DBT_CLOUD_API_TOKEN", TOKEN)
    code = main(
        ["fetch-manifest", "--project-dir", str(tmp_path), "--account-id", "12", "--job-id", "345"]
    )
    assert code == 0
    assert (tmp_path / "target" / "manifest.json").is_file()
    out = capsys.readouterr()
    assert TOKEN not in out.out + out.err
    assert http.calls[0][0].startswith("https://cloud.getdbt.com/")


def test_cli_out_and_host(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    http = FakeHttp(ok(json.dumps(MANIFEST).encode()))
    monkeypatch.setattr(fetch, "default_http", http)
    monkeypatch.setenv("DBT_CLOUD_API_TOKEN", TOKEN)
    out = tmp_path / "elsewhere.json"
    code = main(
        [
            "fetch-manifest",
            "--account-id",
            "12",
            "--job-id",
            "345",
            "--host",
            "emea.dbt.com",
            "--out",
            str(out),
        ]
    )
    assert code == 0 and out.is_file()
    assert http.calls[0][0].startswith("https://emea.dbt.com/api/v2/")


def test_cli_missing_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    http = FakeHttp(ok(b"{}"))
    monkeypatch.setattr(fetch, "default_http", http)
    monkeypatch.delenv("DBT_CLOUD_API_TOKEN", raising=False)
    code = main(
        ["fetch-manifest", "--account-id", "1", "--job-id", "2", "--out", str(tmp_path / "m.json")]
    )
    assert code == 2
    assert "DBT_CLOUD_API_TOKEN" in capsys.readouterr().err
    assert http.calls == []


def test_cli_http_error_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        fetch, "default_http", FakeHttp(HttpResponse(401, b"{}", "application/json"))
    )
    monkeypatch.setenv("DBT_CLOUD_API_TOKEN", TOKEN)
    code = main(
        ["fetch-manifest", "--account-id", "1", "--job-id", "2", "--out", str(tmp_path / "m.json")]
    )
    assert code == 2
    assert TOKEN not in capsys.readouterr().err
