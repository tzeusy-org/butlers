"""Bind the one guards build to its actual source/attempt before browser reuse."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def files_digest(directory: Path) -> dict[str, str]:
    values = {}
    for file in sorted(directory.rglob("*")):
        if file.is_symlink():
            raise ValueError("build artifact symlink refused")
        if file.is_file():
            values[str(file.relative_to(directory))] = hashlib.sha256(file.read_bytes()).hexdigest()
    if not values:
        raise ValueError("build artifact empty")
    return values


def identity(root: Path) -> dict:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root).decode().strip()
    if os.environ.get("GITHUB_SHA", head) != head:
        raise ValueError("frontend checkout differs from workflow")
    # Only actual tracked source/config/lock bytes; node_modules/dist are advisory.
    paths = (
        subprocess.check_output(["git", "ls-files", "-z", "frontend"], cwd=root)
        .decode()
        .split("\0")
    )
    source = {
        name: hashlib.sha256((root / name).read_bytes()).hexdigest()
        for name in paths
        if name and (root / name).is_file()
    }
    return {
        "head": head,
        "run": os.environ.get("GITHUB_RUN_ID", "local"),
        "attempt": os.environ.get("GITHUB_RUN_ATTEMPT", "local"),
        "source_digest": hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest(),
        "lock_digest": source["frontend/package-lock.json"],
    }


def seal(root: Path, output: Path) -> None:
    shutil.copytree(root / "frontend/dist", output / "dist")
    receipt = {
        "schema": "ci-frontend-build.v1",
        "identity": identity(root),
        "files": files_digest(output / "dist"),
    }
    (output / "receipt.json").write_text(json.dumps(receipt, sort_keys=True) + "\n")


def consume(root: Path, artifact: Path) -> None:
    receipt = json.loads((artifact / "receipt.json").read_text())
    if receipt.get("schema") != "ci-frontend-build.v1" or receipt.get("identity") != identity(root):
        raise ValueError("frontend build source/attempt unavailable")
    if receipt.get("files") != files_digest(artifact / "dist"):
        raise ValueError("frontend build content differs")
    shutil.copytree(artifact / "dist", root / "frontend/dist")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("seal", "consume"))
    parser.add_argument("--artifact", type=Path, required=True)
    args = parser.parse_args()
    try:
        (seal if args.mode == "seal" else consume)(ROOT, args.artifact)
        return 0
    except (OSError, ValueError, KeyError):
        print("::error::source-bound frontend build unavailable")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
