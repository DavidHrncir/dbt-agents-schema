"""Command-line entry point (SPEC section 7)."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from dbt_agents_schema import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dbt-agents-schema",
        description="Publish dbt project metadata into a shared warehouse agents schema.",
    )
    parser.add_argument("--version", action="version", version=__version__)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    parser.parse_args(argv)
    parser.print_help()
    return 0


def main_entry() -> None:
    sys.exit(main())
