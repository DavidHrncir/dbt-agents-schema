"""scripts/demo_multi_project.sh runs end to end on DuckDB and every guarantee holds."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "demo_multi_project.sh"


def _run(tmp_path: Path) -> subprocess.CompletedProcess[str]:
    drop = {"WAREHOUSE_CREDENTIALS", "GITHUB_REPOSITORY", "GITHUB_SERVER_URL", "CI_PROJECT_URL"}
    env = {k: v for k, v in os.environ.items() if k not in drop}
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


def _fingerprints(output: str, project: str) -> list[str]:
    return re.findall(rf"^  {project} fingerprint: ([0-9a-f]{{32}})$", output, re.M)


def test_demo_exits_zero_and_every_guarantee_holds(tmp_path: Path) -> None:
    result = _run(tmp_path)
    output = result.stdout + result.stderr
    assert result.returncode == 0, output
    assert "FAILED" not in output

    for step in range(1, 6):
        assert f"== {step}. " in output

    # 1. The dry run created nothing.
    assert "ok: no DuckDB file was created" in output

    # 3. Republishing finance left marketing byte-identical, and finance shrank.
    marketing = _fingerprints(output, "marketing")
    assert len(marketing) == 2 and marketing[0] == marketing[1]
    assert "  finance | 7 | 17 | 7 | 1" in output
    assert "skill/finance/payments/reconciliation" not in output.split("== 3.", 1)[1]

    # 4. Another repository was refused with exit 4.
    assert "exit code: 4" in output
    assert "not https://github.com/example/other-repo" in output

    # 5. Unpublishing marketing left finance byte-identical and removed marketing's keys.
    finance = _fingerprints(output, "finance")
    assert len(finance) == 2 and finance[0] == finance[1]
    after_unpublish = output.split("== 5.", 1)[1]
    assert "project/finance" in after_unpublish
    assert "project/marketing" not in after_unpublish

    assert "every guarantee held" in output
    assert (tmp_path / "multi_project.duckdb").is_file()


def test_demo_leaves_the_checkout_clean(tmp_path: Path) -> None:
    def status() -> str:
        return subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout

    before = status()
    assert _run(tmp_path).returncode == 0
    assert status() == before
