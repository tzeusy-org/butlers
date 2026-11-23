#!/usr/bin/env python3
"""Install a finite Debian profile from the image's verified local artifact context.

Run in the pinned base under a builder's network=none boundary. This helper never
fetches inputs or selects package versions. The complete artifact population and
starting dpkg inventory must match before APT receives any local package path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def validate(manifest: Path, artifacts: Path, profile: str) -> tuple[dict, list[Path]]:
    value = json.loads(manifest.read_text())
    if value["schema"] != "butlers.debian-base-inputs/v1":
        raise ValueError("unsupported system artifacts schema")
    if artifacts.is_symlink() or not artifacts.is_dir():
        raise ValueError("system artifacts must be a real directory")
    records = value["debs"]
    names = [item["filename"] for item in records]
    if not names or len(set(names)) != len(names):
        raise ValueError("empty or duplicate system artifacts")
    for name in names:
        if Path(name).name != name or not re.fullmatch(r"[A-Za-z0-9+_.%:~-]+\.deb", name):
            raise ValueError("unsafe system artifact path")
    if {path.name for path in artifacts.iterdir()} != set(names):
        raise ValueError("system artifact population differs from the pinned manifest")
    for item in records:
        path = artifacts / item["filename"]
        if path.is_symlink() or not path.is_file():
            raise ValueError("system artifact must be a regular file")
        if path.stat().st_size != item["size"] or digest(path) != item["sha256"]:
            raise ValueError("system artifact bytes differ from the pinned manifest")
    selected = value["profiles"][profile]["debs"]
    if not selected or len(set(selected)) != len(selected) or not set(selected) <= set(names):
        raise ValueError("invalid system profile artifact selection")
    return value, [artifacts.resolve() / name for name in selected]


def install(manifest: Path, artifacts: Path, profile: str) -> None:
    value, paths = validate(manifest, artifacts, profile)
    if digest(Path("/var/lib/dpkg/status")) != value["installed_dpkg_status_sha256"]:
        raise ValueError("starting base package inventory differs from the pinned image")
    expected = {item["filename"]: item for item in value["debs"]}
    for path in paths:
        fields = subprocess.check_output(
            ["dpkg-deb", "--field", str(path), "Package", "Version", "Architecture"],
            text=True,
        )
        actual = dict(line.split(": ", 1) for line in fields.strip().splitlines())
        item = expected[path.name]
        if actual != {
            "Package": item["package"],
            "Version": item["version"],
            "Architecture": item["architecture"],
        }:
            raise ValueError("system package identity differs from the pinned manifest")
    # APT's --no-download path needs actual cached archives even when every
    # package argument is a local file. Populate only independently copied,
    # verified bytes, so acquisition cannot introduce another package input.
    cache = Path("/var/cache/apt/archives")
    cache.mkdir(parents=True, exist_ok=True)
    for path in paths:
        destination = cache / path.name
        if destination.is_symlink() or (destination.exists() and not destination.is_file()):
            raise ValueError("unsafe existing APT archive")
        if destination.exists():
            if digest(destination) != expected[path.name]["sha256"]:
                raise ValueError("existing APT archive differs from the pinned input")
        else:
            shutil.copyfile(path, destination)
            destination.chmod(0o644)
    with tempfile.TemporaryDirectory(prefix="butlers-apt-") as temporary:
        isolated = Path(temporary)
        sources = isolated / "sources.list"
        sources.touch()
        parts = isolated / "sources.list.d"
        parts.mkdir()
        lists = isolated / "lists"
        lists.mkdir()
        subprocess.run(
            [
                "apt-get",
                "-y",
                "--no-download",
                "--no-install-recommends",
                "-o",
                f"Dir::Etc::sourcelist={sources}",
                "-o",
                f"Dir::Etc::sourceparts={parts}",
                "-o",
                f"Dir::State::lists={lists}",
                "install",
                *map(str, paths),
            ],
            check=True,
            env={"PATH": os.defpath, "LANG": "C.UTF-8", "DEBIAN_FRONTEND": "noninteractive"},
        )
    for path in paths:
        item = expected[path.name]
        installed = subprocess.check_output(
            ["dpkg-query", "-W", "-f=${Version}\\n${db:Status-Status}", item["package"]],
            text=True,
        )
        if installed != item["version"] + "\ninstalled":
            raise ValueError("installed system package differs from the pinned profile")
    print(json.dumps({"profile": profile, "installed_packages": len(paths)}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--profile", choices=("runtime", "compiler"), required=True)
    arguments = parser.parse_args()
    install(arguments.manifest, arguments.artifacts, arguments.profile)


if __name__ == "__main__":
    main()
