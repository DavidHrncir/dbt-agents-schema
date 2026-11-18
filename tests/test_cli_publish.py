"""`publish` and `unpublish` commands (SPEC 6, 7; T14). In-process through `main(argv)`."""

from __future__ import annotations

import io
import json
import logging
import sys
from pathlib import Path
from typing import Any

import duckdb
import pytest
from fakes import MANIFESTS, PROJECTS

from dbt_agents_schema import cli
from dbt_agents_schema.cli import main

HERE = Path(__file__).parent
PROFILES_DIR = HERE / "fixtures"
FINANCE = PROJECTS / "finance"
SECRET = "hunter2-very-secret"
CI_VARS = (
    "GITHUB_SERVER_URL",
    "GITHUB_REPOSITORY",
    "GITHUB_SHA",
    "GITHUB_REF_NAME",
    "CI_PROJECT_URL",
    "CI_COMMIT_SHA",
    "CI_COMMIT_REF_NAME",
    "WAREHOUSE_CREDENTIALS",
    "DBT_PROFILES_DIR",
    "FIXTURE_DUCKDB_PATH",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for var in CI_VARS:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/finance")
    monkeypatch.setenv("GITHUB_SHA", "f" * 40)
    monkeypatch.setenv("GITHUB_REF_NAME", "main")


def finance_args(*extra: str) -> list[str]:
    return [
        "--project-dir",
        str(FINANCE),
        "--manifest",
        str(MANIFESTS / "finance.json"),
        *extra,
    ]


def table_counts(db: Path, schema: str) -> dict[str, int]:
    con = duckdb.connect(str(db))
    try:
        return {
            t: con.execute(f'SELECT count(*) FROM "{schema}"."{t}"').fetchone()[0]  # type: ignore[index]
            for t in (
                "ROOT",
                "DBT_PROJECT",
                "DBT_MODEL",
                "DBT_COLUMN",
                "DBT_DEPENDENCY",
                "SKILL_USE",
            )
        }
    finally:
        con.close()


def test_publish_env_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "w.duckdb"
    monkeypatch.setenv("WAREHOUSE_CREDENTIALS", json.dumps({"type": "duckdb", "path": str(db)}))
    code = main(["publish", "--env", "prod", *finance_args()])
    out = capsys.readouterr().out
    print(out)
    assert code == 0
    assert "published project 'finance' to schema AGENTS" in out
    assert "elapsed:" in out
    counts = table_counts(db, "AGENTS")
    assert counts["DBT_PROJECT"] == 1 and counts["DBT_MODEL"] > 0
    con = duckdb.connect(str(db))
    row = con.execute(
        'SELECT repo_url, git_sha, git_branch, environment FROM "AGENTS"."DBT_PROJECT"'
    ).fetchone()
    con.close()
    assert row == ("https://github.com/example/finance", "f" * 40, "main", "prod")


def test_publish_dbt_profile_dev_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "dev.duckdb"
    monkeypatch.setenv("FIXTURE_DUCKDB_PATH", str(db))
    code = main(["publish", "--env", "dev", "--profiles-dir", str(PROFILES_DIR), *finance_args()])
    out = capsys.readouterr().out
    print(out)
    assert code == 0
    assert "schema finance_AGENTS" in out
    assert table_counts(db, "finance_AGENTS")["DBT_PROJECT"] == 1


def test_publish_dbt_profile_target_flag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    db = tmp_path / "t.duckdb"
    (profiles / "profiles.yml").write_text(
        "finance:\n  target: dev\n  outputs:\n"
        "    dev:\n      type: duckdb\n      path: unused.duckdb\n      schema: a\n"
        f"    other:\n      type: duckdb\n      path: '{db}'\n      schema: b\n"
    )
    monkeypatch.setenv("DBT_PROFILES_DIR", str(profiles))
    assert main(["publish", "--env", "dev", "--target", "other", *finance_args()]) == 0
    assert table_counts(db, "b_AGENTS")["DBT_PROJECT"] == 1


def test_dry_run_never_connects(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(
        "WAREHOUSE_CREDENTIALS", json.dumps({"type": "duckdb", "path": "/nonexistent/x.duckdb"})
    )
    calls: list[Any] = []

    def factory(*args: Any, **kwargs: Any) -> Any:
        calls.append(args)
        raise AssertionError("dry run must not open a writer")

    monkeypatch.setattr(cli, "open_writer", factory)
    code = main(["publish", "--env", "prod", "--dry-run", *finance_args()])
    out = capsys.readouterr().out
    assert code == 0
    assert calls == []
    assert "dry run: would publish project 'finance' to schema AGENTS" in out
    assert "DBT_MODEL:" in out


def test_missing_credentials_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["publish", "--env", "prod", *finance_args()]) == 2
    assert "WAREHOUSE_CREDENTIALS" in capsys.readouterr().err


def test_unknown_environment_exit_2(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["publish", "--env", "staging", *finance_args()]) == 2
    err = capsys.readouterr().err
    assert "staging" in err and "dev, prod" in err


def test_missing_manifest_exit_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv(
        "WAREHOUSE_CREDENTIALS", json.dumps({"type": "duckdb", "path": str(tmp_path / "w")})
    )
    code = main(
        [
            "publish",
            "--env",
            "prod",
            "--project-dir",
            str(FINANCE),
            "--manifest",
            str(tmp_path / "none.json"),
        ]
    )
    assert code == 2
    assert "run `dbt parse` first" in capsys.readouterr().err


def test_check_errors_exit_1_without_connecting(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("WAREHOUSE_CREDENTIALS", json.dumps({"type": "duckdb", "path": ":memory:"}))
    monkeypatch.setattr(cli, "open_writer", lambda *a, **k: pytest.fail("must not connect"))
    code = main(
        [
            "publish",
            "--env",
            "prod",
            "--project-dir",
            str(PROJECTS / "bad"),
            "--manifest",
            str(MANIFESTS / "bad.json"),
        ]
    )
    assert code == 1


def test_writer_not_available_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    creds = {
        "type": "databricks",
        "host": "h",
        "http_path": "/p",
        "catalog": "main",
        "token": SECRET,
    }
    monkeypatch.setenv("WAREHOUSE_CREDENTIALS", json.dumps(creds))
    # Every warehouse has a writer since T18; a writer is unavailable when its extra
    # (client library) is not installed.
    monkeypatch.setitem(sys.modules, "databricks.sql", None)
    assert main(["publish", "--env", "prod", *finance_args()]) == 2
    captured = capsys.readouterr()
    assert "databricks" in captured.err
    assert SECRET not in captured.out + captured.err


def test_secrets_never_printed_or_logged(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    creds = {
        "type": "snowflake",
        "account": "acme",
        "user": "svc",
        "warehouse": "wh",
        "database": "ANALYTICS",
        "password": SECRET,
        "private_key_passphrase": SECRET,
    }
    monkeypatch.setenv("WAREHOUSE_CREDENTIALS", json.dumps(creds))
    outputs: list[str] = []
    for argv in (
        ["publish", "--env", "prod", "--dry-run", *finance_args()],
        ["publish", "--env", "prod", *finance_args()],
        ["unpublish", "--env", "prod", "--project-dir", str(FINANCE), "--yes"],
    ):
        main(argv)
        captured = capsys.readouterr()
        outputs.append(captured.out + captured.err)
    del creds["user"]
    monkeypatch.setenv("WAREHOUSE_CREDENTIALS", json.dumps(creds))
    main(["publish", "--env", "prod", *finance_args()])
    captured = capsys.readouterr()
    outputs.append(captured.out + captured.err)
    assert "user" in outputs[-1]
    text = "\n".join(outputs) + caplog.text
    assert SECRET not in text


def test_secret_from_profile_env_var_not_printed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    profiles = tmp_path / "profiles"
    profiles.mkdir()
    (profiles / "profiles.yml").write_text(
        "finance:\n  target: dev\n  outputs:\n    dev:\n      type: duckdb\n"
        f"      path: '{tmp_path / 'p.duckdb'}'\n      schema: s\n"
        "      settings:\n        motherduck_token: \"{{ env_var('MD_TOKEN') }}\"\n"
    )
    monkeypatch.setenv("MD_TOKEN", SECRET)
    assert main(["publish", "--env", "dev", "--profiles-dir", str(profiles), *finance_args()]) == 0
    captured = capsys.readouterr()
    assert SECRET not in captured.out + captured.err + caplog.text


# --- ownership via CLI --------------------------------------------------------------------


def test_force_takeover_flag(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "w.duckdb"
    monkeypatch.setenv("WAREHOUSE_CREDENTIALS", json.dumps({"type": "duckdb", "path": str(db)}))
    assert main(["publish", "--env", "prod", *finance_args()]) == 0
    monkeypatch.setenv("GITHUB_REPOSITORY", "someone/else")
    assert main(["publish", "--env", "prod", *finance_args()]) == 4
    assert main(["publish", "--env", "prod", "--force-takeover", *finance_args()]) == 0


# --- unpublish ----------------------------------------------------------------------------


def publish_both(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WAREHOUSE_CREDENTIALS", json.dumps({"type": "duckdb", "path": str(db)}))
    assert main(["publish", "--env", "prod", *finance_args()]) == 0
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/marketing")
    assert (
        main(
            [
                "publish",
                "--env",
                "prod",
                "--project-dir",
                str(PROJECTS / "marketing"),
                "--manifest",
                str(MANIFESTS / "marketing.json"),
            ]
        )
        == 0
    )


def test_unpublish_with_yes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "w.duckdb"
    publish_both(db, monkeypatch)
    code = main(
        [
            "unpublish",
            "--env",
            "prod",
            "--project-dir",
            str(FINANCE),
            "--project",
            "marketing",
            "--yes",
        ]
    )
    assert code == 0
    assert "unpublished project 'marketing'" in capsys.readouterr().out
    con = duckdb.connect(str(db))
    projects = con.execute('SELECT DISTINCT project FROM "AGENTS"."DBT_MODEL"').fetchall()
    con.close()
    assert projects == [("finance",)]


def test_unpublish_refuses_without_yes_when_not_a_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "w.duckdb"
    publish_both(db, monkeypatch)
    monkeypatch.setattr("sys.stdin", io.StringIO(""))
    monkeypatch.setattr(cli, "open_writer", lambda *a, **k: pytest.fail("must not connect"))
    assert main(["unpublish", "--env", "prod", "--project-dir", str(FINANCE)]) == 2
    assert "--yes" in capsys.readouterr().err


class _Tty(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(("answer", "code", "remaining"), [("finance\n", 0, 1), ("no\n", 2, 2)])
def test_unpublish_prompts_on_tty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str, code: int, remaining: int
) -> None:
    db = tmp_path / "w.duckdb"
    publish_both(db, monkeypatch)
    monkeypatch.setattr("sys.stdin", _Tty(answer))
    assert main(["unpublish", "--env", "prod", "--project-dir", str(FINANCE)]) == code
    assert table_counts(db, "AGENTS")["DBT_PROJECT"] == remaining


# --- T15 review fixes ---------------------------------------------------------------------


def test_duckdb_path_token_never_printed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    path = f"{tmp_path}/no_dir/db.duckdb?motherduck_token={SECRET}"
    monkeypatch.setenv("WAREHOUSE_CREDENTIALS", json.dumps({"type": "duckdb", "path": path}))
    assert main(["publish", "--env", "prod", *finance_args()]) == 3
    captured = capsys.readouterr()
    assert "no_dir" in captured.err  # still names the file
    assert SECRET not in captured.out + captured.err + caplog.text


def test_check_errors_win_over_credential_errors(capsys: pytest.CaptureFixture[str]) -> None:
    # No WAREHOUSE_CREDENTIALS: SPEC 6 checks (exit 1) before connecting (exit 2).
    code = main(
        [
            "publish",
            "--env",
            "prod",
            "--project-dir",
            str(PROJECTS / "bad"),
            "--manifest",
            str(MANIFESTS / "bad.json"),
        ]
    )
    assert code == 1


def test_unpublish_refuses_newer_spec_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "w.duckdb"
    publish_both(db, monkeypatch)
    con = duckdb.connect(str(db))
    con.execute('UPDATE "AGENTS"."DBT_PROJECT" SET spec_version = 2')
    con.close()
    before = table_counts(db, "AGENTS")
    assert main(["unpublish", "--env", "prod", "--project-dir", str(FINANCE), "--yes"]) == 2
    assert "spec_version 2" in capsys.readouterr().err
    assert table_counts(db, "AGENTS") == before
