#!/usr/bin/env python3
"""Build the complete patched Gemini workspace and installed graph offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path("/opt/js-build")
RECIPE = Path("/recipe")
SDK = Path("/inputs/sdk")
NODE = Path("/opt/node")
OUTPUT = Path("/artifacts/gemini")
SDK_VENDORS = {"genai-sdk": "js-genai", "google-auth-core": "google-auth-library"}
DOC_LINKS = {
    "bundle/docs/CONTRIBUTING.md": "../../CONTRIBUTING.md",
    "packages/core/dist/docs/CONTRIBUTING.md": "../../../../CONTRIBUTING.md",
}
CONFIGURATION = ("npm-user.npmrc", "npm-global.npmrc")


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def helper(name: str) -> types.ModuleType:
    if name not in {
        "produce_npm_artifact",
        "produce_codex_artifacts",
        "materialize_inputs",
        "runtime_artifacts",
    }:
        raise ValueError("undeclared Gemini builder helper")
    path = RECIPE / "scripts/runtime_clis" / f"{name}.py"
    if path.is_symlink() or not path.is_file():
        raise ValueError("builder helper must be a declared regular source")
    module = types.ModuleType("_gemini_builder_" + name)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    path.chmod(0o644)


def pins() -> dict[str, str]:
    paths = [
        "Dockerfile.gemini-artifacts",
        "scripts/runtime_clis/produce_gemini_component.py",
        "scripts/runtime_clis/produce_npm_artifact.py",
        "scripts/runtime_clis/produce_codex_artifacts.py",
        "scripts/runtime_clis/prepare_vendor.py",
        "scripts/runtime_clis/materialize_inputs.py",
        "scripts/runtime_clis/runtime_artifacts.py",
        "deploy/runtime-clis/toolchains.json",
        "deploy/runtime-clis/runtime-inputs.json",
        "deploy/runtime-clis/debian-system-inputs.json",
        "deploy/runtime-clis/codex/compiler-inputs.json",
        "deploy/runtime-clis/gemini/series.json",
        "deploy/runtime-clis/gemini/artifact-inputs.json",
    ]
    return {path: digest(RECIPE / path) for path in paths}


def identity() -> dict:
    if (
        Path.cwd() != ROOT
        or platform.python_version() != "3.12.13"
        or platform.libc_ver() != ("glibc", "2.41")
        or platform.machine() != "x86_64"
    ):
        raise ValueError("fixed pinned Gemini Linux/amd64 builder required")
    home = Path(os.environ.get("HOME", "/missing-builder-home"))
    if (
        not home.is_absolute()
        or home.is_symlink()
        or not home.is_dir()
        or home.stat().st_uid != os.getuid()
        or any(home.iterdir())
    ):
        raise ValueError("builder requires an empty owned home backing")
    tools = json.loads((RECIPE / "deploy/runtime-clis/toolchains.json").read_bytes())
    runtime = json.loads((RECIPE / "deploy/runtime-clis/runtime-inputs.json").read_bytes())
    artifacts = helper("runtime_artifacts")
    if (
        digest(NODE / "bin/node") != tools["node"]["binary_sha256"]
        or json.loads((NODE / "lib/node_modules/npm/package.json").read_bytes())["version"]
        != tools["npm"]["version"]
        or artifacts.tree_digest(artifacts.inventory(NODE, closed=True))
        != runtime["components"]["node"]["tree_sha256"]
    ):
        raise ValueError("complete official Node component differs")
    series = json.loads((RECIPE / "deploy/runtime-clis/gemini/series.json").read_bytes())
    if series["vendor"] != "gemini" or series["version"] != artifacts.VERSIONS["gemini"]:
        raise ValueError("Gemini version differs")
    return series


def sdk_inputs() -> dict[str, Path]:
    declaration = json.loads(
        (RECIPE / "deploy/runtime-clis/gemini/artifact-inputs.json").read_bytes()
    )
    records = declaration["artifacts"]
    if len(records) != 2 or {item["name"] for item in records} != set(SDK_VENDORS):
        raise ValueError("complete declared Gemini SDK inputs required")
    result = {}
    for record in records:
        vendor = SDK_VENDORS[record["name"]]
        root = SDK / vendor
        archive = Path(record["path"]).name
        if (
            root.is_symlink()
            or not root.is_dir()
            or {path.name for path in root.iterdir()} != {archive, "provenance.json"}
        ):
            raise ValueError("SDK input must be its complete producer output")
        if any(path.is_symlink() or not path.is_file() for path in root.iterdir()):
            raise ValueError("SDK output must contain regular files")
        provenance = json.loads((root / "provenance.json").read_bytes())
        if (
            provenance["schema"] != "butlers.npm-artifact/v1"
            or provenance["vendor"] != vendor
            or provenance["archive_sha256"] != record["sha256"]
            or digest(root / archive) != record["sha256"]
            or provenance["source_tree_sha256"] != record["source_tree_sha256"]
            or provenance["recipe"][f"deploy/runtime-clis/{vendor}/series.json"]
            != record["source_series_sha256"]
        ):
            raise ValueError("SDK producer identity differs")
        result[record["name"]] = root / archive
    if {path.name for path in SDK.iterdir()} != set(SDK_VENDORS.values()):
        raise ValueError("unexpected Gemini SDK input population")
    return result


def command(argv: list[str], *, offline: bool, cwd: Path, commit: str) -> None:
    environment = helper("produce_npm_artifact").environment(offline=offline)
    environment["GIT_COMMIT"] = commit
    subprocess.run(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL, check=True)


def prepare(series: dict, *, offline: bool) -> None:
    command(
        [
            sys.executable,
            "-I",
            "-B",
            str(RECIPE / "scripts/runtime_clis/prepare_vendor.py"),
            "--series",
            str(RECIPE / "deploy/runtime-clis/gemini/series.json"),
            "--archive",
            str(ROOT / "source.tgz"),
            "--output",
            str(ROOT / "source"),
            "--receipt",
            str(ROOT / "prepared.json"),
        ],
        offline=offline,
        cwd=ROOT,
        commit=series["commit"],
    )
    helper("materialize_inputs").materialize(
        RECIPE / "deploy/runtime-clis/gemini/artifact-inputs.json", ROOT / "source", sdk_inputs()
    )


def build_commands(series: dict, *, offline: bool) -> None:
    npm = helper("produce_npm_artifact").npm
    args = ["ci", "--foreground-scripts"]
    if offline:
        args.append("--offline")
    command(npm(*args), offline=offline, cwd=ROOT / "source", commit=series["commit"])
    command(npm("run", "build"), offline=offline, cwd=ROOT / "source", commit=series["commit"])


def public_seed() -> None:
    series = identity()
    before = pins()
    inputs = {name: digest(path) for name, path in sdk_inputs().items()}
    transport = helper("produce_codex_artifacts")
    transport.fetch({**series["archive"], "maximum_bytes": 256 * 1024**2}, ROOT / "source.tgz")
    prepare(series, offline=False)
    npm = helper("produce_npm_artifact")
    for name in ("npm-cache", "browser-cache"):
        (ROOT / name).mkdir()
    for name in CONFIGURATION:
        (ROOT / name).touch()
    source = transport.inventory(ROOT / "source")
    build_commands(series, offline=False)
    npm.source_identity(ROOT / "source", source, transport)
    if before != pins() or inputs != {name: digest(path) for name, path in sdk_inputs().items()}:
        raise ValueError("Gemini source or SDK recipe changed during public preparation")
    write_json(
        ROOT / "seed.json",
        {
            "status": "NONFINAL",
            "recipe": before,
            "sdk": inputs,
            "source": source,
            "source_archive_sha256": digest(ROOT / "source.tgz"),
            "npm_cache": npm.cache_inventory(ROOT / "npm-cache", transport),
            "browser_cache": npm.cache_inventory(ROOT / "browser-cache", transport),
            "configuration": {name: digest(ROOT / name) for name in CONFIGURATION},
        },
    )


def relocate_docs() -> list[dict[str, str]]:
    source = ROOT / "source"
    canonical = source / "CONTRIBUTING.md"
    if canonical.is_symlink() or not canonical.is_file():
        raise ValueError("original documentation target differs")
    records = []
    for relative, target in DOC_LINKS.items():
        link = source / relative
        if (
            not link.is_symlink()
            or link.readlink() != canonical
            or digest(link) != digest(canonical)
        ):
            raise ValueError("generated documentation link differs")
        link.unlink()
        link.symlink_to(target)
        records.append({"path": relative, "target": target, "sha256": digest(canonical)})
    return records


def build() -> None:
    transport = helper("produce_codex_artifacts")
    transport.network_none()
    series = identity()
    npm = helper("produce_npm_artifact")
    seed = json.loads((ROOT / "seed.json").read_bytes())
    if (
        seed["status"] != "NONFINAL"
        or seed["recipe"] != pins()
        or seed["sdk"] != {name: digest(path) for name, path in sdk_inputs().items()}
        or seed["source_archive_sha256"] != digest(ROOT / "source.tgz")
        or seed["npm_cache"] != npm.cache_inventory(ROOT / "npm-cache", transport)
        or seed["browser_cache"] != npm.cache_inventory(ROOT / "browser-cache", transport)
        or seed["configuration"] != {name: digest(ROOT / name) for name in CONFIGURATION}
    ):
        raise ValueError("closed Gemini preparation changed")
    shutil.rmtree(ROOT / "source")
    prepare(series, offline=True)
    if transport.inventory(ROOT / "source") != seed["source"]:
        raise ValueError("fresh Gemini source preparation differs")
    build_commands(series, offline=True)
    npm.source_identity(ROOT / "source", seed["source"], transport)
    package = json.loads((ROOT / "source/package.json").read_bytes())
    if package["version"] != series["version"] or not (ROOT / "source/bundle/gemini.js").is_file():
        raise ValueError("complete Gemini build identity differs")
    docs = relocate_docs()
    artifacts = helper("runtime_artifacts")
    # Prepared source/lifecycle output retains upstream access bits. The
    # independent component copy canonicalizes them, then we verify closure.
    entries = artifacts.inventory(ROOT / "source")
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise ValueError("Gemini output already exists")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="gemini-", dir=OUTPUT.parent) as temporary:
        stage = Path(temporary) / "output"
        stage.mkdir(mode=0o755)
        stage.chmod(0o755)
        components = stage / "components"
        components.mkdir(mode=0o755)
        components.chmod(0o755)
        artifacts._copy(ROOT / "source", components / "gemini", entries)
        if artifacts.inventory(components / "gemini", closed=True) != entries:
            raise ValueError("complete copied Gemini component differs")
        transport.network_none()
        if pins() != seed["recipe"] or seed["sdk"] != {
            name: digest(path) for name, path in sdk_inputs().items()
        }:
            raise ValueError("Gemini recipe changed during compilation/copy")
        write_json(
            stage / "provenance.json",
            {
                "schema": "butlers.gemini-component/v1",
                "recipe": seed["recipe"],
                "source_tree_sha256": series["source_tree_sha256"],
                "source_archive_sha256": series["archive"]["sha256"],
                "sdk": seed["sdk"],
                "package_lock_sha256": digest(ROOT / "source/package-lock.json"),
                "tree_sha256": artifacts.tree_digest(entries),
                "documentation_links": docs,
                "version": series["version"],
                "git_commit": series["commit"],
                "scope": (
                    "Complete patched workspace, installed dependency graph, original "
                    "ci/prepare/build lifecycle and generated assets; no runtime auth/tool "
                    "or final image conformance inferred."
                ),
            },
        )
        stage.rename(OUTPUT)
    print("COMPLETE: complete Gemini component built offline", flush=True)


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
