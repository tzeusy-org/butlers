#!/usr/bin/env python3
"""Assemble complete pinned Claude/Codex target packages in a networkless builder."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import time
import types
import urllib.parse
import urllib.request
from pathlib import Path, PurePosixPath

ROOT = Path("/opt/native-packages")
RECIPE = Path("/recipe")
NODE = Path("/inputs/toolchains/components/node")
CODEX = Path("/inputs/codex")
OUTPUT = Path("/artifacts/native-packages")
MANIFEST = "deploy/runtime-clis/native-package-artifacts/build-inputs.json"
DESTINATIONS = {
    "claude-wrapper": "claude",
    "claude-platform": "claude/node_modules/@anthropic-ai/claude-code-linux-x64",
    "codex-wrapper": "codex",
    "codex-platform": "codex/node_modules/@openai/codex-linux-x64",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def helper(name: str) -> types.ModuleType:
    if name not in {"runtime_artifacts", "produce_codex_artifacts"}:
        raise ValueError("undeclared native package helper")
    path = RECIPE / "scripts/runtime_clis" / (name + ".py")
    if path.is_symlink() or not path.is_file():
        raise ValueError("native package helper must be a declared regular source")
    module = types.ModuleType("_native_package_" + name)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    path.chmod(0o644)


def declarations() -> dict:
    manifest = json.loads((RECIPE / MANIFEST).read_bytes())
    artifacts = helper("runtime_artifacts")
    if (
        manifest["schema"] != "butlers.native-package-inputs/v1"
        or manifest["target"] != artifacts.TARGET
        or manifest["node"]["version"] != artifacts.VERSIONS["node"]
        or manifest["node"]["npm"] != artifacts.VERSIONS["npm"]
        or manifest["claude"]["version"] != artifacts.VERSIONS["claude"]
        or manifest["codex"]["version"] != artifacts.VERSIONS["codex"]
    ):
        raise ValueError("native package target differs")
    records = manifest["archives"]
    if len(records) != 4 or {item["id"]: item["destination"] for item in records} != DESTINATIONS:
        raise ValueError("complete original target archive set is required")
    for item in records:
        if item["maximum_bytes"] != item["size"] or item["size"] <= 0:
            raise ValueError("archive size identity differs")
    if (
        manifest["claude"]["native_path"]
        != "node_modules/@anthropic-ai/claude-code-linux-x64/claude"
        or manifest["claude"]["installed_path"] != "bin/claude.exe"
        or manifest["codex"]["main_path"]
        != "node_modules/@openai/codex-linux-x64/vendor/x86_64-unknown-linux-musl/bin/codex"
    ):
        raise ValueError("original native package layout differs")
    return manifest


def identity(manifest: dict) -> None:
    if (
        Path.cwd() != ROOT
        or platform.python_version() != "3.12.13"
        or platform.libc_ver() != ("glibc", "2.41")
        or platform.machine() != "x86_64"
    ):
        raise ValueError("fixed pinned Linux/amd64 native package builder required")
    home = Path(os.environ.get("HOME", "/missing-owned-home"))
    if not home.is_absolute() or home.is_symlink() or not home.is_dir():
        raise ValueError("builder requires an empty real home")
    if home.stat().st_uid != os.getuid() or any(home.iterdir()):
        raise ValueError("builder requires its empty owned home")
    if digest(NODE / "bin/node") != manifest["node"]["sha256"]:
        raise ValueError("official Node executable differs")
    npm = json.loads((NODE / "lib/node_modules/npm/package.json").read_bytes())
    if npm["version"] != manifest["node"]["npm"]:
        raise ValueError("bundled npm identity differs")
    tools = json.loads((RECIPE / "deploy/runtime-clis/toolchains.json").read_bytes())
    runtime = json.loads((RECIPE / "deploy/runtime-clis/runtime-inputs.json").read_bytes())
    artifacts = helper("runtime_artifacts")
    if (
        tools["node"]["binary_sha256"] != manifest["node"]["sha256"]
        or artifacts.tree_digest(artifacts.inventory(NODE, closed=True))
        != runtime["components"]["node"]["tree_sha256"]
    ):
        raise ValueError("complete official Node component differs")


def pins() -> dict[str, str]:
    return {
        name: digest(RECIPE / name)
        for name in (
            "Dockerfile.native-package-artifacts",
            "scripts/runtime_clis/produce_native_packages.py",
            "scripts/runtime_clis/runtime_artifacts.py",
            "scripts/runtime_clis/produce_codex_artifacts.py",
            "scripts/runtime_clis/install_system_artifacts.py",
            "deploy/runtime-clis/toolchains.json",
            "deploy/runtime-clis/runtime-inputs.json",
            "deploy/runtime-clis/debian-system-inputs.json",
            "deploy/runtime-clis/codex/series.json",
            MANIFEST,
        )
    }


def archive_identity(record: dict, archive: Path) -> None:
    if archive.is_symlink() or not archive.is_file():
        raise ValueError("archive must be regular")
    if archive.stat().st_size != record["size"] or digest(archive) != record["sha256"]:
        raise ValueError("complete official archive differs")
    algorithm, expected = record["integrity"].split("-", 1)
    if algorithm != "sha512":
        raise ValueError("unsupported official archive integrity")
    with archive.open("rb") as stream:
        actual = base64.b64encode(hashlib.file_digest(stream, "sha512").digest()).decode()
    if actual != expected:
        raise ValueError("official npm integrity differs")


def download(record: dict, output: Path, *, opener=None) -> None:
    url = urllib.parse.urlsplit(record["url"])
    if (
        url.scheme != "https"
        or url.netloc != "registry.npmjs.org"
        or url.query
        or url.fragment
        or not url.path.startswith("/")
        or output.exists()
        or output.is_symlink()
    ):
        raise ValueError("invalid official npm input")
    fetcher = helper("produce_codex_artifacts")
    public = opener or urllib.request.build_opener(
        urllib.request.ProxyHandler({}), fetcher.RefuseRedirects()
    )
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="native-input-", dir=output.parent) as temporary:
        stage = Path(temporary) / "archive"
        size = 0
        with public.open(record["url"], timeout=60) as response, stage.open("xb") as stream:
            if response.status != 200 or response.geturl() != record["url"]:
                raise ValueError("unexpected official npm response")
            while block := response.read(1024**2):
                size += len(block)
                if size > record["maximum_bytes"] or time.monotonic() - started > 300:
                    raise ValueError("official npm input exceeds its bound")
                stream.write(block)
        archive_identity(record, stage)
        stage.chmod(0o644)
        os.link(stage, output)


def extract(record: dict, archive: Path, output: Path) -> None:
    """Retain every original member, with canonical safe executable modes."""
    archive_identity(record, archive)
    if output.exists() or output.is_symlink():
        raise ValueError("package destination already exists")
    with tarfile.open(archive, "r:gz") as source:
        members = source.getmembers()
        seen: set[str] = set()
        total = 0
        for member in members:
            path = PurePosixPath(member.name)
            if (
                path.is_absolute()
                or not path.parts
                or path.parts[0] != "package"
                or ".." in path.parts
                or "\\" in member.name
                or any(ord(character) < 32 for character in member.name)
                or not (member.isdir() or member.isfile())
                or member.mode & 0o7000
                or member.size < 0
            ):
                raise ValueError("unsupported official package member")
            relative = PurePosixPath(*path.parts[1:]).as_posix()
            if relative in seen or (relative == "." and not member.isdir()):
                raise ValueError("ambiguous package member")
            seen.add(relative)
            total += member.size
        if not members or len(members) > 100000 or total > 4 * 1024**3:
            raise ValueError("official package expansion exceeds its bound")
        # Check parent types across the entire archive before writing anything.
        regulars = {
            PurePosixPath(*PurePosixPath(member.name).parts[1:])
            for member in members
            if member.isfile()
        }
        paths = [PurePosixPath(*PurePosixPath(member.name).parts[1:]) for member in members]
        if any(parent in regulars for path in paths for parent in path.parents):
            raise ValueError("package member overlaps a regular parent")
        output.mkdir(parents=True, mode=0o755)
        for member in members:
            relative = PurePosixPath(*PurePosixPath(member.name).parts[1:])
            target = output / relative
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                target.chmod(0o755)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            incoming = source.extractfile(member)
            if incoming is None:
                raise ValueError("regular member has no bytes")
            with incoming, target.open("xb") as stream:
                shutil.copyfileobj(incoming, stream)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
        for directory in [output, *[path for path in output.rglob("*") if path.is_dir()]]:
            directory.chmod(0o755)
    package = json.loads((output / "package.json").read_bytes())
    if package["name"] != record["name"] or package["version"] != record["version"]:
        raise ValueError("official package identity differs")


def public_seed() -> None:
    manifest = declarations()
    identity(manifest)
    before = pins()
    (ROOT / "archives").mkdir(mode=0o755)
    for record in manifest["archives"]:
        target = ROOT / "archives" / (record["id"] + ".tgz")
        download(record, target)
    if before != pins():
        raise ValueError("native package recipe changed during acquisition")
    write_json(ROOT / "seed.json", {"status": "NONFINAL", "recipe": before})


def build() -> None:
    guard = helper("produce_codex_artifacts")
    guard.network_none()
    manifest = declarations()
    identity(manifest)
    seed = json.loads((ROOT / "seed.json").read_bytes())
    if seed != {"status": "NONFINAL", "recipe": pins()}:
        raise ValueError("native package public seed changed")
    expected_archives = {record["id"] + ".tgz" for record in manifest["archives"]}
    if {path.name for path in (ROOT / "archives").iterdir()} != expected_archives:
        raise ValueError("native package archive population differs")
    native = manifest["codex"]
    if (
        CODEX.is_symlink()
        or not CODEX.is_dir()
        or {path.name for path in CODEX.iterdir()} != {"codex", "provenance.json"}
        or any(path.is_symlink() or not path.is_file() for path in CODEX.iterdir())
    ):
        raise ValueError("Codex artifact must be its complete regular output")
    provenance = json.loads((CODEX / "provenance.json").read_bytes())
    if (
        digest(CODEX / "codex") != native["binary_sha256"]
        or provenance["binary_sha256"] != native["binary_sha256"]
        or provenance["source_tree_sha256"] != native["source_tree_sha256"]
        or provenance["series_sha256"] != native["series_sha256"]
        or digest(RECIPE / "deploy/runtime-clis/codex/series.json") != native["series_sha256"]
        or provenance["default_features"] is not True
        or provenance["target"] != "x86_64-unknown-linux-gnu"
    ):
        raise ValueError("reproduced Codex artifact differs")
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise ValueError("native package output already exists")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    artifacts = helper("runtime_artifacts")
    with tempfile.TemporaryDirectory(prefix="native-packages-", dir=OUTPUT.parent) as temporary:
        work = Path(temporary) / "work"
        work.mkdir(mode=0o755)
        for record in manifest["archives"]:
            extract(
                record, ROOT / "archives" / (record["id"] + ".tgz"), work / record["destination"]
            )
        claude = work / "claude"
        scripts = json.loads((claude / "package.json").read_bytes())["scripts"]
        if scripts.get("postinstall") != "node install.cjs" or any(
            name in scripts for name in ("preinstall", "install")
        ):
            raise ValueError("original Claude lifecycle identity differs")
        subprocess.run(
            [str(NODE / "bin/node"), "install.cjs"],
            cwd=claude,
            stdin=subprocess.DEVNULL,
            env={
                "HOME": os.environ["HOME"],
                "PATH": f"{NODE}/bin:/usr/bin:/bin",
                "LANG": "C.UTF-8",
            },
            check=True,
        )
        for path in (manifest["claude"]["installed_path"], manifest["claude"]["native_path"]):
            if digest(claude / path) != manifest["claude"]["native_sha256"]:
                raise ValueError("official Claude native bytes changed")
        main = work / "codex" / native["main_path"]
        if main.is_symlink() or not stat.S_ISREG(main.stat().st_mode):
            raise ValueError("Codex main must remain its original regular path")
        shutil.copyfile(CODEX / "codex", main)
        main.chmod(0o755)
        stage = Path(temporary) / "output"
        stage.mkdir(mode=0o755)
        stage.chmod(0o755)
        components = stage / "components"
        components.mkdir(mode=0o755)
        components.chmod(0o755)
        trees = {}
        for name in ("claude", "codex"):
            entries = artifacts.inventory(work / name)
            trees[name] = artifacts.tree_digest(entries)
            if trees[name] != manifest[name]["tree_sha256"]:
                raise ValueError("complete native component differs")
            artifacts._copy(work / name, components / name, entries)
        guard.network_none()
        if seed["recipe"] != pins():
            raise ValueError("native package recipe changed during assembly")
        write_json(
            stage / "provenance.json",
            {
                "schema": "butlers.native-packages/v1",
                "recipe": seed["recipe"],
                "components": trees,
                "archives": {record["id"]: record["sha256"] for record in manifest["archives"]},
                "codex_binary_sha256": native["binary_sha256"],
                "claude_native_sha256": manifest["claude"]["native_sha256"],
                "claude_postinstall": "unmodified original hook, no route",
                "scope": manifest["scope"],
            },
        )
        stage.rename(OUTPUT)
    print("COMPLETE: original native package payloads assembled offline", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("public-seed", "build"), required=True)
    args = parser.parse_args()
    if args.phase == "public-seed":
        public_seed()
    else:
        build()


if __name__ == "__main__":
    main()
