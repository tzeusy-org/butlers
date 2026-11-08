#!/usr/bin/env python3
"""Seal and run an already installed, lock-consistent Python environment offline.

Build the complete requested groups/extras with pinned uv before sealing. This
entrypoint never installs, resolves, repairs or downloads dependencies. Mounted
source can change; its project/lock inputs and editable import identity must agree.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

INVENTORY = r"""
import hashlib, importlib.metadata, json, pathlib, sys
import butlers
environment = pathlib.Path(sys.prefix).resolve()
project = pathlib.Path(sys.argv[1]).resolve()
source = pathlib.Path(butlers.__file__).resolve()
if not source.is_relative_to(project / 'src' / 'butlers'):
    raise SystemExit('editable Butlers import points outside intended project')
def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()
distributions = []
for distribution in importlib.metadata.distributions():
    files = {}
    for entry in distribution.files or []:
        path = pathlib.Path(distribution.locate_file(entry))
        if path.suffix == '.pth' or path.name in {
            'METADATA', 'PKG-INFO', 'RECORD', 'WHEEL', 'direct_url.json', 'entry_points.txt'
        }:
            relative = path.absolute().relative_to(environment)
            files[str(relative)] = digest(path)
    distributions.append({
        'name': distribution.metadata['Name'], 'version': distribution.version,
        'metadata': files,
    })
print(json.dumps({
    'python': list(sys.version_info[:3]),
    'interpreter_sha256': digest(pathlib.Path(sys.executable).resolve()),
    'source_relative': str(source.relative_to(project)),
    'distributions': sorted(distributions, key=lambda value: (value['name'], value['version'])),
    'pth': {
        str(path.relative_to(environment)): digest(path)
        for path in environment.glob('lib/python*/site-packages/*.pth')
    },
}, sort_keys=True))
"""


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def inventory(environment: Path, project: Path) -> dict:
    if environment.is_symlink() or not environment.is_dir():
        raise ValueError("Python environment must be a real directory")
    result = subprocess.run(
        [str(environment / "bin/python"), "-I", "-c", INVENTORY, str(project)],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def seal(environment: Path, project: Path, groups: list[str], extras: list[str]) -> dict:
    return {
        "schema": "butlers.python-environment/v1",
        "project": {name: digest(project / name) for name in ["pyproject.toml", "uv.lock"]},
        "groups": sorted(set(groups)),
        "extras": sorted(set(extras)),
        "environment": inventory(environment, project),
    }


def validate(manifest: Path, environment: Path, project: Path) -> None:
    expected = json.loads(manifest.read_text())
    if expected["schema"] != "butlers.python-environment/v1":
        raise ValueError("unsupported Python environment manifest")
    actual_project = {name: digest(project / name) for name in ["pyproject.toml", "uv.lock"]}
    if actual_project != expected["project"]:
        raise ValueError("mounted project/uv.lock differs from the built Python environment")
    if inventory(environment, project) != expected["environment"]:
        raise ValueError("installed Python environment or editable project identity differs")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["seal", "check", "run"])
    parser.add_argument("--environment", type=Path, required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--group", action="append", default=[])
    parser.add_argument("--extra", action="append", default=[])
    # Split the command payload before parsing our own options. A child option
    # named --project must never change the environment validation target.
    supplied = sys.argv[1:]
    separator = supplied.index("--") if "--" in supplied else len(supplied)
    args = parser.parse_args(supplied[:separator])
    arguments = supplied[separator + 1 :]
    if arguments and args.action != "run":
        parser.error("command arguments are only valid for run")
    try:
        if args.action == "seal":
            value = seal(args.environment, args.project, args.group, args.extra)
            args.manifest.write_text(json.dumps(value, indent=2) + "\n")
            return
        validate(args.manifest, args.environment, args.project)
        if args.action == "run":
            executable = args.environment / "bin/python"
            entrypoint = args.environment / "bin/butlers"
            os.execv(str(executable), [str(executable), "-I", str(entrypoint), *arguments])
    except (ValueError, OSError, KeyError, subprocess.CalledProcessError) as error:
        parser.exit(78, f"Offline Python environment refused: {error}\n")


if __name__ == "__main__":
    main()
