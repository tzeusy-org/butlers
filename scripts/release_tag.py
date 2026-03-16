#!/usr/bin/env python3
"""Create an annotated git tag for the current pyproject.toml version.

Usage:
    python scripts/release_tag.py

The tag is created locally. Push it to trigger the release workflow:
    git push origin v<VERSION>

Or use:
    make release-tag && git push origin v<VERSION>
"""

import subprocess
import sys
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def read_version() -> str:
    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def main() -> None:
    version = read_version()
    tag = f"v{version}"

    # Check whether the tag already exists
    result = subprocess.run(
        ["git", "tag", "-l", tag],
        capture_output=True,
        text=True,
    )
    if result.stdout.strip():
        print(f"Error: tag {tag!r} already exists.", file=sys.stderr)
        sys.exit(1)

    # Create annotated tag
    subprocess.run(
        ["git", "tag", "-a", tag, "-m", f"Release {tag}"],
        check=True,
    )
    print(f"Tag {tag} created.")
    print(f"Push with: git push origin {tag}")


if __name__ == "__main__":
    main()
