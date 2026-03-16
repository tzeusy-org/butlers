#!/usr/bin/env python3
"""Bump the version in pyproject.toml.

Usage:
    python scripts/bump_version.py          # auto-increment patch (0.1.0 → 0.1.1)
    python scripts/bump_version.py 1.2.0    # set explicit version
"""

import re
import sys
import tomllib
from pathlib import Path

PYPROJECT = Path(__file__).parent.parent / "pyproject.toml"


def read_version() -> str:
    with PYPROJECT.open("rb") as f:
        data = tomllib.load(f)
    return data["project"]["version"]


def write_version(new_version: str) -> None:
    content = PYPROJECT.read_text()
    updated = re.sub(r'^version = ".*"', f'version = "{new_version}"', content, flags=re.MULTILINE)
    PYPROJECT.write_text(updated)


def bump_patch(version: str) -> str:
    parts = version.split(".")
    if len(parts) != 3:
        raise ValueError(f"Expected MAJOR.MINOR.PATCH, got: {version!r}")
    major, minor, patch = parts
    return f"{major}.{minor}.{int(patch) + 1}"


def main() -> None:
    current = read_version()

    if len(sys.argv) > 1:
        new_version = sys.argv[1]
    else:
        new_version = bump_patch(current)

    # Validate version format
    if not re.fullmatch(r"\d+\.\d+\.\d+", new_version):
        print(f"Error: version must be MAJOR.MINOR.PATCH, got: {new_version!r}", file=sys.stderr)
        sys.exit(1)

    print(f"Bumping version: {current} → {new_version}")
    write_version(new_version)
    print(f"Updated pyproject.toml to version {new_version}")


if __name__ == "__main__":
    main()
