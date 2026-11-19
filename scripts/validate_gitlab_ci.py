"""Validate GitLab CI files with check-jsonschema, one YAML document at a time.

Templates with `spec: inputs` hold a header document and a body document, and
check-jsonschema reads only single-document files. This splits every file into its
documents in a temporary directory and validates them all against the builtin
`vendor.gitlab-ci` schema (SPEC 10.4). Usage:
    python scripts/validate_gitlab_ci.py FILE [FILE ...]
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

import yaml


def split(files: list[str], out: Path) -> list[Path]:
    """Write each document of each file to `out`; return the written paths."""
    written: list[Path] = []
    for n, name in enumerate(files):
        source = Path(name)
        text = source.read_text(encoding="utf-8")
        docs = [d for d in yaml.safe_load_all(text) if d is not None]
        stem = source.name.lstrip(".").replace(".", "_")
        for k, doc in enumerate(docs):
            target = out / f"{n:02d}-{stem}-doc{k}.yml"
            target.write_text(yaml.safe_dump(doc, sort_keys=False), encoding="utf-8")
            written.append(target)
        print(f"{source}: {len(docs)} document(s)")
    return written


def main(files: list[str]) -> int:
    if not files:
        print(__doc__, file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="gitlab-ci-validate-") as tmp:
        parts = split(files, Path(tmp))
        command = [
            sys.executable,
            "-m",
            "check_jsonschema",
            "--builtin-schema",
            "vendor.gitlab-ci",
            *map(str, parts),
        ]
        return subprocess.run(command).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
