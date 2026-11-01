"""Advisory CI environment cache with mandatory frozen repair on every restore.

Only the checkout's real .venv is managed. A cache is dependencies, never test
evidence. Compatibility excludes commit SHA but includes the installer itself.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

SYNC_TIMEOUT_S = 640


def compatibility(root: Path) -> dict:
    return {
        "schema": 1,
        "system": platform.system(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "abi": sysconfig.get_config_var("SOABI"),
        "uv": subprocess.check_output(["uv", "--version"], text=True, timeout=10).strip(),
        "mode": "frozen-dev-bytecode-editable",
        "inputs": {
            name: hashlib.sha256((root / name).read_bytes()).hexdigest()
            for name in ("uv.lock", "pyproject.toml", "scripts/ci_environment.py")
        },
    }


def cache_key(context: dict) -> str:
    return (
        "butlers-venv-v1-"
        + hashlib.sha256(json.dumps(context, sort_keys=True).encode()).hexdigest()
    )


def prepare(root: Path) -> dict:
    started = time.monotonic()

    def remaining() -> float:
        budget = SYNC_TIMEOUT_S - (time.monotonic() - started)
        if budget <= 0:
            raise subprocess.TimeoutExpired("CI environment preparation", SYNC_TIMEOUT_S)
        return budget

    context = compatibility(root)
    directory = root / ".venv"
    if directory.is_symlink():
        raise ValueError("CI environment must be an owned real directory")
    # --frozen intentionally skips freshness checking. Validate lock/project
    # agreement independently on every hit/miss, without rewriting the lock.
    subprocess.run(["uv", "lock", "--check"], cwd=root, check=True, timeout=remaining())
    state = "miss"
    if directory.exists():
        try:
            recorded = json.loads((directory / "ci-environment.json").read_text())
            if recorded != context or not (directory / "bin/python").is_file():
                raise ValueError("incompatible")
            state = "hit"
        except (OSError, ValueError):
            state = "invalid"
            if not directory.is_dir():
                directory.unlink()
            else:
                shutil.rmtree(directory)
    # Both cache paths run exactly the same bounded frozen installer. Reinstall
    # the editable project even on a hit: a .pth can name a previous checkout.
    subprocess.run(
        ["uv", "sync", "--frozen", "--dev", "--compile-bytecode", "--reinstall-package", "butlers"],
        cwd=root,
        check=True,
        timeout=remaining(),
    )
    validate_source(root, timeout=min(30, remaining()))
    (directory / "ci-environment.json").write_text(json.dumps(context, sort_keys=True) + "\n")
    return {
        "cache": state,
        "compatibility": cache_key(context),
        "frozen_sync": "passed",
        "editable_repair": "passed",
        "own_source": "passed",
    }


def validate_source(root: Path, *, timeout: float = 30) -> None:
    # A fresh interpreter prevents a previously imported module from validating
    # a repaired .pth. The external-package opt-out never enters this process.
    code = (
        "import pathlib,sys,butlers; r=pathlib.Path.cwd().resolve(); "
        "p=pathlib.Path(butlers.__file__).resolve(); "
        "assert p.is_relative_to(r/'src'); "
        "assert pathlib.Path(sys.prefix).resolve()==r/'.venv'"
    )
    subprocess.run(
        [str(root / ".venv/bin/python"), "-I", "-c", code], cwd=root, check=True, timeout=timeout
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("key", "prepare"))
    args = parser.parse_args()
    root = Path.cwd().resolve()
    try:
        if args.command == "key":
            print(cache_key(compatibility(root)))
        else:
            print(json.dumps(prepare(root), sort_keys=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError):
        print("CI environment preparation refused or failed", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
