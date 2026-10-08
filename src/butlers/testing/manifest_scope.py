"""Exact Git delta admission for historical hand-manifest compatibility only."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

from butlers.testing.resource_readers import safe_path

NAME = re.compile(r"^\.github/ci-test-shards/(?:unit-[1-5]|integration-[1-6])\.txt$")


def _ownership(root: Path, ref: str) -> dict[str, set[str]]:
    """Validate actual historical whole-file ownership in each marker lane."""
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", ref, ".github/ci-test-shards/"],
        cwd=root,
        capture_output=True,
        check=True,
        timeout=10,
    )
    lanes: dict[str, set[str]] = {}
    for path in result.stdout.decode().splitlines():
        if not path.endswith(".txt"):
            continue
        if not NAME.fullmatch(path):
            raise ValueError("MANIFEST_INELIGIBLE")
        lane = Path(path).name.split("-")[0]
        rows = {row for row in _body(root, ref, path) if row and not row.startswith("#")}
        if lanes.setdefault(lane, set()) & rows:
            raise ValueError("MANIFEST_INELIGIBLE")
        for row in rows:
            check = subprocess.run(
                ["git", "cat-file", "-e", f"{ref}:{row}"], cwd=root, capture_output=True, timeout=10
            )
            if check.returncode:
                raise ValueError("MANIFEST_INELIGIBLE")
        lanes[lane].update(rows)
    return lanes


def _body(root: Path, ref: str, path: str) -> list[str]:
    result = subprocess.run(
        ["git", "show", f"{ref}:{path}"], cwd=root, capture_output=True, timeout=10
    )
    if result.returncode:
        raise ValueError("MANIFEST_INELIGIBLE")
    rows = result.stdout.decode().splitlines()
    names = [line for line in rows if line and not line.startswith("#")]
    if len(names) != len(set(names)) or any(
        not safe_path(name)
        or not name.startswith(("tests/", "roster/"))
        or not name.endswith(".py")
        for name in names
    ):
        raise ValueError("MANIFEST_INELIGIBLE")
    return rows


def eligible(root: Path, changed: list[str], base: str, head: str) -> set[str]:
    manifests = [path for path in changed if path.startswith(".github/ci-test-shards/")]
    if not manifests:
        return set()
    try:
        parent = (
            subprocess.run(
                ["git", "merge-base", base, head],
                cwd=root,
                capture_output=True,
                timeout=10,
                check=True,
            )
            .stdout.decode()
            .strip()
        )
        result = subprocess.run(
            ["git", "diff", "--name-status", "-z", "--no-renames", parent, head],
            cwd=root,
            capture_output=True,
            timeout=10,
            check=True,
        )
        tokens = result.stdout.decode().split("\0")
        status = dict(zip(tokens[1::2], tokens[0::2]))
        before_owners, after_owners = _ownership(root, parent), _ownership(root, head)
        admitted = set()
        for path in manifests:
            if not NAME.fullmatch(path) or status.get(path) != "M":
                raise ValueError("MANIFEST_INELIGIBLE")
            before, after = _body(root, parent, path), _body(root, head, path)
            if [r for r in before if r.startswith("#") or not r] != [
                r for r in after if r.startswith("#") or not r
            ]:
                raise ValueError("MANIFEST_INELIGIBLE")
            delta = set(before) ^ set(after)
            if not delta or any(
                name not in changed or status.get(name) not in {"A", "M", "D"} for name in delta
            ):
                raise ValueError("MANIFEST_INELIGIBLE")
            admitted.add(path)
        # Reassignment of an unchanged file cannot be laundered by another
        # manifest's addition. Removed files may disappear; actual additions
        # retain ownership, and every delta is bound to this verified Git diff.
        for lane in before_owners.keys() | after_owners.keys():
            delta = before_owners.get(lane, set()) ^ after_owners.get(lane, set())
            if any(
                name not in changed or status.get(name) not in {"A", "M", "D"} for name in delta
            ):
                raise ValueError("MANIFEST_INELIGIBLE")
        return admitted
    except (OSError, ValueError, UnicodeError, subprocess.SubprocessError):
        raise ValueError("MANIFEST_INELIGIBLE") from None
