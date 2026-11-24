#!/usr/bin/env python3
"""Build-input verification and installed-only application startup.

Image construction establishes dependency completeness. Startup only validates
that sealed environment and execs the original console or Python role payload.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

_ENVIRONMENT = Path("/app/.venv")
_PROJECT = Path("/app")
_MANIFEST = Path("/usr/local/share/butlers/python-environment.json")
_TARGET = {"python": "3.12.13", "platform": "linux-amd64", "libc": "glibc2.41"}


def _validator():
    # -I deliberately excludes script/CWD import discovery. Load only this
    # image-owned sibling, never an ambient module of the same name.
    path = Path(__file__).with_name("python_environment.py")
    spec = importlib.util.spec_from_file_location("_image_python_environment", path)
    if spec is None or spec.loader is None:
        raise ValueError("image environment validator is unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.validate


def run(
    arguments: list[str],
    *,
    environment: Path = _ENVIRONMENT,
    project: Path = _PROJECT,
    manifest: Path = _MANIFEST,
) -> None:
    if not arguments or arguments[0] not in {"butlers", "python"}:
        raise ValueError("expected an installed butlers or Python role")
    _validator()(manifest, environment, project)
    executable = str(environment / "bin/python")
    # Preserve uv-run's environment selection for ordinary child console/Python
    # tools without invoking uv or changing the original remaining PATH order.
    os.environ["VIRTUAL_ENV"] = str(environment)
    os.environ["PATH"] = str(environment / "bin") + os.pathsep + os.environ.get("PATH", os.defpath)
    payload = arguments[1:]
    if arguments[0] == "butlers":
        payload = [str(environment / "bin/butlers"), *payload]
    os.execv(executable, [executable, "-I", *payload])


def _digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def verify_build(artifacts: Path, project: Path, profile: str, extras: list[str]) -> None:
    """Verify the canonical producer's complete closed regular-file input set."""
    if artifacts.is_symlink() or not artifacts.is_dir():
        raise ValueError("missing real Python artifacts context")
    provenance = artifacts / "provenance.json"
    if provenance.is_symlink() or not provenance.is_file():
        raise ValueError("missing regular Python artifact provenance")
    value = json.loads(provenance.read_text())
    if set(value) != {"schema", "target", "project", "profiles", "inputs"}:
        raise ValueError("invalid Python artifact provenance fields")
    if value["schema"] != "butlers.python-artifacts/v1" or value["target"] != _TARGET:
        raise ValueError("Python artifacts target differs from the image contract")
    if value["project"] != {
        name: _digest(project / name) for name in ("pyproject.toml", "uv.lock")
    }:
        raise ValueError("Python artifacts project/lock drift")
    selected = sorted(set(extras))
    expected_profiles = {
        "production": {"groups": [], "extras": selected},
        "development": {"groups": ["dev"], "extras": selected},
    }
    if profile not in expected_profiles or value["profiles"] != expected_profiles:
        raise ValueError("Python artifacts groups/extras differ from the requested install")
    declared: dict[str, str] = {}
    if not isinstance(value["inputs"], list):
        raise ValueError("invalid Python artifacts input population")
    for item in value["inputs"]:
        if not isinstance(item, dict) or set(item) != {"path", "sha256"}:
            raise ValueError("invalid Python artifacts input record")
        name, digest = item["path"], item["sha256"]
        if not isinstance(name, str) or not isinstance(digest, str):
            raise ValueError("invalid Python artifacts input types")
        parts = name.split("/")
        if (
            name in declared
            or any(part in {"", ".", ".."} for part in parts)
            or "\\" in name
            or not (
                name == "build-constraints.txt"
                or (len(parts) > 1 and parts[0] in {"wheelhouse", "uv-cache"})
            )
            or len(digest) != 64
            or any(char not in "0123456789abcdef" for char in digest)
        ):
            raise ValueError("unsafe or duplicate Python artifacts input")
        declared[name] = digest
    if "build-constraints.txt" not in declared or not any(
        name.startswith("wheelhouse/") and name.endswith(".whl") for name in declared
    ):
        raise ValueError("complete wheelhouse and build constraints are required")
    actual = {}
    for path in artifacts.rglob("*"):
        if path.is_symlink():
            raise ValueError("Python artifacts symlinks are not supported")
        if path.is_file() and path != artifacts / "provenance.json":
            actual[path.relative_to(artifacts).as_posix()] = _digest(path)
        elif not path.is_dir() and path != artifacts / "provenance.json":
            raise ValueError("unsupported Python artifacts filesystem entry")
    if actual != declared or not (artifacts / "build-constraints.txt").read_text().strip():
        raise ValueError("Python artifacts missing, undeclared or changed inputs")


def main() -> None:
    try:
        if sys.argv[1:2] == ["verify-build"]:
            parser = argparse.ArgumentParser(description="Verify closed image build inputs")
            parser.add_argument("--artifacts", type=Path, required=True)
            parser.add_argument("--project", type=Path, required=True)
            parser.add_argument("--profile", choices=["production", "development"], required=True)
            parser.add_argument("--extra", action="append", default=[])
            args = parser.parse_args(sys.argv[2:])
            verify_build(args.artifacts, args.project, args.profile, args.extra)
            return
        run(sys.argv[1:])
    except (ValueError, OSError, KeyError, TypeError, subprocess.CalledProcessError) as error:
        # Never print command arguments, environment values or inventory stderr.
        print(f"Offline Python startup refused ({type(error).__name__})", file=sys.stderr)
        raise SystemExit(78) from None


if __name__ == "__main__":
    main()
