#!/usr/bin/env python3
"""Assemble a closed Python build context from an owned, verified public seed.

This does not resolve, download, install or certify dependency completeness. The
result still needs the image validator and a fresh offline install for each
declared profile. Constraints document the seeded backend versions; uv sync
enforces the closed cache/wheelhouse through offline/no-index operation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import tempfile
from pathlib import Path

_TARGET = {"python": "3.12.13", "platform": "linux-amd64", "libc": "glibc2.41"}


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _copy_closed(source: Path, destination: Path, root: Path, ancestors: tuple[Path, ...] = ()):
    resolved = source.resolve(strict=True)
    if not resolved.is_relative_to(root):
        raise ValueError("Python seed link leaves its declared input root")
    if resolved.is_dir():
        if resolved in ancestors:
            raise ValueError("Python seed directory link cycle")
        destination.mkdir()
        for child in sorted(resolved.iterdir()):
            _copy_closed(child, destination / child.name, root, (*ancestors, resolved))
    elif resolved.is_file():
        # Independent bytes prevent later seed-cache writes changing the
        # already-hashed context through a shared inode.
        shutil.copyfile(resolved, destination)
        destination.chmod(0o644)
    else:
        raise ValueError("Python seed contains an unsupported filesystem entry")


def close_context(
    *,
    project: Path,
    wheelhouse: Path,
    cache: Path,
    constraints: Path,
    output: Path,
    extras: list[str],
) -> dict:
    """Publish one complete regular-file context, or leave no partial output."""
    if output.exists() or output.is_symlink():
        raise ValueError("Python artifacts output already exists")
    if any(path.is_symlink() or not path.is_dir() for path in (wheelhouse, cache)):
        raise ValueError("Python seed roots must be real directories")
    if constraints.is_symlink() or not constraints.is_file() or not constraints.read_text().strip():
        raise ValueError("Python backend constraints must be a nonempty regular file")
    project_pins = {}
    for name in ("pyproject.toml", "uv.lock"):
        path = project / name
        if path.is_symlink() or not path.is_file():
            raise ValueError("Python project and lock must be regular files")
        project_pins[name] = _digest(path)
    selected = sorted(set(extras))
    if any(not re.fullmatch(r"[A-Za-z0-9]+(?:[-_.][A-Za-z0-9]+)*", extra) for extra in selected):
        raise ValueError("invalid Python extra name")
    roots = [wheelhouse.resolve(), cache.resolve()]
    destination = output.resolve()
    if any(destination.is_relative_to(root) or root.is_relative_to(destination) for root in roots):
        raise ValueError("Python artifacts output overlaps an input root")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="python-artifacts-", dir=destination.parent
    ) as temporary:
        context = Path(temporary) / "context"
        context.mkdir()
        _copy_closed(wheelhouse, context / "wheelhouse", roots[0])
        _copy_closed(cache, context / "uv-cache", roots[1])
        shutil.copyfile(constraints, context / "build-constraints.txt")
        inputs = [
            {"path": path.relative_to(context).as_posix(), "sha256": _digest(path)}
            for path in sorted(context.rglob("*"))
            if path.is_file()
        ]
        if not any(
            item["path"].startswith("wheelhouse/") and item["path"].endswith(".whl")
            for item in inputs
        ):
            raise ValueError("Python wheelhouse must contain a wheel")
        value = {
            "schema": "butlers.python-artifacts/v1",
            "target": _TARGET,
            "project": project_pins,
            "profiles": {
                "production": {"groups": [], "extras": selected},
                "development": {"groups": ["dev"], "extras": selected},
            },
            "inputs": inputs,
        }
        (context / "provenance.json").write_text(json.dumps(value, indent=2) + "\n")
        context.rename(destination)
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("project", "wheelhouse", "cache", "constraints", "output"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    parser.add_argument("--extra", action="append", default=[])
    arguments = vars(parser.parse_args())
    arguments["extras"] = arguments.pop("extra")
    value = close_context(**arguments)
    print(json.dumps({"schema": value["schema"], "regular_inputs": len(value["inputs"])}))


if __name__ == "__main__":
    main()
