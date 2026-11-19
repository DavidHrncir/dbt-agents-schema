import subprocess
import sys

import pytest

from dbt_agents_schema import __version__
from dbt_agents_schema.cli import main


def test_version_flag_prints_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        main(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.strip() == "0.2.0"


def test_version_constant() -> None:
    assert __version__ == "0.2.0"


def test_version_via_module() -> None:
    out = subprocess.run(
        [sys.executable, "-m", "dbt_agents_schema", "--version"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "0.2.0"
