"""Copy a dbt manifest, replacing local absolute paths so it can be committed.

Usage: scrub_manifest.py <manifest.json> <projects-dir> <out.json>
"""

import json
import os
import sys


def main() -> None:
    src, projects_dir, out = sys.argv[1:4]
    with open(src, encoding="utf-8") as fh:
        text = fh.read()
    for path in {projects_dir, os.path.realpath(projects_dir)}:
        text = text.replace(json.dumps(path)[1:-1], "/fixtures/projects")
    home = os.path.expanduser("~")
    text = text.replace(json.dumps(home)[1:-1], "/home/user")
    data = json.loads(text)
    data["metadata"]["env"] = {}
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=1, sort_keys=True)
        fh.write("\n")


if __name__ == "__main__":
    main()
