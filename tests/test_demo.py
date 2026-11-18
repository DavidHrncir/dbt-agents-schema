"""T28: scripts/demo_duckdb.sh runs end to end on DuckDB with no network."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "demo_duckdb.sh"


def _run(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    env = {k: v for k, v in os.environ.items() if k not in {"WAREHOUSE_CREDENTIALS"}}
    env["DEMO_DIR"] = str(tmp_path)
    return subprocess.run(
        ["bash", str(SCRIPT)],
        cwd=tmp_path,  # works from any directory
        env=env,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def test_demo_exits_zero_and_shows_results(tmp_path: Path) -> None:
    result = _run(tmp_path)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output

    # Both fixtures checked; three publishes.
    assert output.count("== check ") == 2
    assert "== publish finance -> prod" in output
    assert "== publish marketing -> prod" in output
    assert "== publish finance -> dev" in output

    # Three analyst-skill queries, read from the published skill in ROOT.
    assert output.count("== analyst query ") == 3
    queries = output.split("== analyst query ", 1)[1]
    assert "finance" in queries and "marketing" in queries
    # Cross-project lineage: the shared package's calendar feeds both projects.
    assert "model.shared_pkg.dim_calendar" in queries
    assert "skill/finance/" in queries

    # Dev publish went to its own schema; prod holds both projects.
    assert "prod projects: finance, marketing" in output
    assert "dev projects: finance" in output
    assert (tmp_path / "demo.duckdb").is_file()


def test_demo_leaves_the_checkout_clean(tmp_path: Path) -> None:
    before = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert _run(tmp_path).returncode == 0
    after = subprocess.run(
        ["git", "status", "--porcelain", "--untracked-files=all"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert after == before
