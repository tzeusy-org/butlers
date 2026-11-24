#!/usr/bin/env python3
"""Reproduce the two patched SDK packages consumed by the Gemini materializer.

Public acquisition runs ordinary locked installs and their complete lifecycle.
The separate networkless build repeats that install from closed build inputs,
packs the original public package, and requires its declared artifact digest.
"""

from __future__ import annotations

import argparse
import base64
import copy
import hashlib
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import types
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path("/opt/js-build")
RECIPE = Path("/recipe")
OUTPUT = Path("/artifacts")
PACKAGES = {
    "google-auth-library": ("google-auth-library", "10.9.0", "google-auth-core"),
    "js-genai": ("@google/genai", "1.30.0", "genai-sdk"),
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def private_helper(name: str) -> types.ModuleType:
    if name != "produce_codex_artifacts":
        raise ValueError("undeclared build helper")
    path = RECIPE / "scripts/runtime_clis" / f"{name}.py"
    if path.is_symlink() or not path.is_file():
        raise ValueError("build helper must be a declared regular source file")
    module = types.ModuleType(f"_npm_builder_{name}")
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    path.chmod(0o644)


def command(argv: list[str], *, offline: bool, cwd: Path) -> None:
    subprocess.run(
        argv,
        cwd=cwd,
        env=environment(offline=offline),
        stdin=subprocess.DEVNULL,
        check=True,
        umask=0o027,
    )


def environment(*, offline: bool) -> dict[str, str]:
    return {
        "HOME": os.environ["HOME"],
        "PATH": "/opt/node/bin:/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": "0",
        "PUPPETEER_CACHE_DIR": str(ROOT / "browser-cache"),
        "npm_config_offline": str(offline).lower(),
        **(
            {
                "BUTLERS_TEST_SERVER_ARCHIVE": str(
                    ROOT / "dependencies/test-server_Linux_x86_64.tar.gz"
                )
            }
            if (ROOT / "dependencies/test-server_Linux_x86_64.tar.gz").is_file()
            else {}
        ),
    }


def npm(*arguments: str) -> list[str]:
    return [
        "/opt/node/bin/node",
        "/opt/node/lib/node_modules/npm/bin/npm-cli.js",
        "--registry=https://registry.npmjs.org",
        "--cache",
        str(ROOT / "npm-cache"),
        "--userconfig",
        str(ROOT / "npm-user.npmrc"),
        "--globalconfig",
        str(ROOT / "npm-global.npmrc"),
        "--no-audit",
        "--no-fund",
        *arguments,
    ]


def identity(vendor: str) -> tuple[dict, dict, types.ModuleType]:
    if vendor not in PACKAGES:
        raise ValueError("unsupported SDK package")
    if (
        Path.cwd() != ROOT
        or platform.python_version() != "3.12.13"
        or platform.libc_ver() != ("glibc", "2.41")
        or platform.machine() != "x86_64"
    ):
        raise ValueError("fixed pinned Linux/amd64 SDK builder required")
    home = Path(os.environ.get("HOME", "/missing-builder-home"))
    if (
        not home.is_absolute()
        or home.is_symlink()
        or not home.is_dir()
        or home.stat().st_uid != os.getuid()
        or any(home.iterdir())
    ):
        raise ValueError("builder requires an empty owned home backing")
    tools = json.loads((RECIPE / "deploy/runtime-clis/toolchains.json").read_text())
    if digest(Path("/opt/node/bin/node")) != tools["node"]["binary_sha256"]:
        raise ValueError("official Node executable differs")
    npm_package = json.loads(Path("/opt/node/lib/node_modules/npm/package.json").read_text())
    if npm_package["version"] != tools["npm"]["version"]:
        raise ValueError("bundled npm version differs")
    series = json.loads((RECIPE / f"deploy/runtime-clis/{vendor}/series.json").read_text())
    expected = json.loads((RECIPE / "deploy/runtime-clis/gemini/artifact-inputs.json").read_text())
    artifact = next(item for item in expected["artifacts"] if item["name"] == PACKAGES[vendor][2])
    if (
        digest(RECIPE / f"deploy/runtime-clis/{vendor}/series.json")
        != artifact["source_series_sha256"]
        or series["source_tree_sha256"] != artifact["source_tree_sha256"]
    ):
        raise ValueError("SDK series differs from its declared materializer input")
    return series, artifact, private_helper("produce_codex_artifacts")


def pins(vendor: str) -> dict[str, str]:
    paths = [
        "Dockerfile.npm-artifacts",
        "scripts/runtime_clis/produce_npm_artifact.py",
        "scripts/runtime_clis/produce_codex_artifacts.py",
        "scripts/runtime_clis/prepare_vendor.py",
        "deploy/runtime-clis/toolchains.json",
        "deploy/runtime-clis/builders.json",
        "deploy/runtime-clis/debian-system-inputs.json",
        "deploy/runtime-clis/codex/compiler-inputs.json",
        "deploy/runtime-clis/gemini/artifact-inputs.json",
        "deploy/runtime-clis/npm-artifacts/build-inputs.json",
        "deploy/runtime-clis/npm-artifacts/patches/001-test-server-explicit-build-cache.patch",
        f"deploy/runtime-clis/{vendor}/series.json",
    ]
    return {path: digest(RECIPE / path) for path in paths}


def build_inputs() -> dict:
    inputs = json.loads(
        (RECIPE / "deploy/runtime-clis/npm-artifacts/build-inputs.json").read_bytes()
    )
    if inputs["schema"] != "butlers.npm-build-inputs/v1" or inputs["npm_child_umask"] != 0o027:
        raise ValueError("unsupported SDK build inputs")
    return inputs["test_server"]


class ManualStatus(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        return response

    https_response = http_response


def fetch_dependency(record: dict, output: Path, *, release: bool = False, opener=None) -> None:
    """Only the original release's single explicit public asset hop is allowed."""
    source = urllib.parse.urlsplit(record["url"])
    if (
        source.scheme != "https"
        or source.netloc != ("github.com" if release else "registry.npmjs.org")
        or source.query
        or source.fragment
        or not source.path.startswith("/")
        or output.exists()
        or output.is_symlink()
    ):
        raise ValueError("invalid public dependency input")
    public = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), ManualStatus())
    current = record["url"]
    start = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="sdk-input-", dir=output.parent) as temporary:
            staged = Path(temporary) / "archive"
            for hop in range(2):
                with public.open(current, timeout=60) as response:
                    if 300 <= response.status < 400:
                        locations = response.headers.get_all("Location", [])
                        if not release or hop != 0 or len(locations) != 1:
                            raise ValueError("public dependency redirect refused")
                        target = urllib.parse.urlsplit(urllib.parse.urljoin(current, locations[0]))
                        if (
                            target.scheme != "https"
                            or target.netloc != "release-assets.githubusercontent.com"
                            or target.fragment
                            or not target.path.startswith("/")
                            or any(ord(c) < 32 for c in locations[0])
                        ):
                            raise ValueError("public release redirect refused")
                        current = target.geturl()
                        continue
                    if response.status != 200 or response.geturl() != current:
                        raise ValueError("unexpected public dependency response")
                    size = 0
                    with staged.open("xb") as stream:
                        while chunk := response.read(1024**2):
                            size += len(chunk)
                            if size > record["size"] or time.monotonic() - start > 300:
                                raise ValueError("public dependency exceeds its bound")
                            stream.write(chunk)
                    if size != record["size"] or digest(staged) != record["sha256"]:
                        raise ValueError("public dependency bytes differ")
                    staged.chmod(0o644)
                    os.link(staged, output)
                    return
            raise ValueError("public dependency redirect bound exceeded")
    except Exception:
        # Transient GitHub asset URLs carry signed queries. No exception text
        # or chained urllib response may become a durable build input or log.
        raise ValueError("pinned public dependency acquisition failed") from None


def verify_archive(path: Path, record: dict) -> None:
    if (
        path.is_symlink()
        or not path.is_file()
        or path.stat().st_size != record["size"]
        or digest(path) != record["sha256"]
    ):
        raise ValueError("declared dependency archive differs")
    if "integrity" in record:
        with path.open("rb") as stream:
            actual = (
                "sha512-"
                + base64.b64encode(hashlib.file_digest(stream, "sha512").digest()).decode()
            )
        if actual != record["integrity"]:
            raise ValueError("declared dependency integrity differs")


def dependency_inputs(*, offline: bool) -> dict:
    """Create a declared six-member derivative; npm ci installs it normally."""
    inputs = build_inputs()
    root = ROOT / "dependencies"
    if not offline:
        root.mkdir()
        fetch_dependency(inputs["original_archive"], root / "test-server-sdk-0.2.9.tgz")
        fetch_dependency(
            inputs["release_archive"], root / "test-server_Linux_x86_64.tar.gz", release=True
        )
    original = root / "test-server-sdk-0.2.9.tgz"
    verify_archive(original, inputs["original_archive"])
    verify_archive(root / "test-server_Linux_x86_64.tar.gz", inputs["release_archive"])
    patch_path = RECIPE / "deploy/runtime-clis/npm-artifacts" / inputs["patch"]["path"]
    if digest(patch_path) != inputs["patch"]["sha256"]:
        raise ValueError("declared dependency patch differs")
    with tarfile.open(original) as archive:
        members = archive.getmembers()
        if len(members) != len(inputs["members"]) or not all(item.isfile() for item in members):
            raise ValueError("original dependency population differs")
        payload = {}
        for member, expected in zip(members, inputs["members"], strict=True):
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("original dependency member is not readable")
            data = stream.read()
            if {
                "path": member.name,
                "mode": member.mode,
                "size": member.size,
                "sha256": hashlib.sha256(data).hexdigest(),
            } != expected:
                raise ValueError("original dependency member differs")
            payload[member.name] = data
    if (
        hashlib.sha256(payload["package/postinstall.js"]).hexdigest()
        != inputs["patch"]["before_sha256"]
        or hashlib.sha256(payload["package/checksums.json"]).hexdigest()
        != inputs["checksums_sha256"]
    ):
        raise ValueError("original lifecycle or checksums differ")
    if (
        json.loads(payload["package/checksums.json"])["v0.2.9"]["test-server_Linux_x86_64.tar.gz"]
        != inputs["release_archive"]["sha256"]
    ):
        raise ValueError("release archive differs from original lifecycle checksum")
    with tempfile.TemporaryDirectory(prefix="sdk-dependency-", dir=ROOT) as temporary:
        stage = Path(temporary)
        hook = stage / "postinstall.js"
        hook.write_bytes(payload["package/postinstall.js"])
        command(
            ["patch", "--batch", "--forward", "--fuzz=0", "-p1", "-i", str(patch_path)],
            offline=offline,
            cwd=stage,
        )
        if digest(hook) != inputs["patch"]["after_sha256"]:
            raise ValueError("patched lifecycle differs")
        payload["package/postinstall.js"] = hook.read_bytes()
        derived = stage / inputs["derived_archive"]["filename"]
        with tarfile.open(derived, "w", format=tarfile.USTAR_FORMAT) as archive:
            for member in members:
                entry = copy.copy(member)
                entry.size = len(payload[entry.name])
                archive.addfile(entry, io.BytesIO(payload[entry.name]))
        verify_archive(derived, inputs["derived_archive"])
        target = root / derived.name
        if offline:
            verify_archive(target, inputs["derived_archive"])
        else:
            shutil.copyfile(derived, target)
            target.chmod(0o644)
    if {path.name for path in root.iterdir()} != {
        "test-server-sdk-0.2.9.tgz",
        "test-server_Linux_x86_64.tar.gz",
        inputs["derived_archive"]["filename"],
    }:
        raise ValueError("unexpected declared dependency population")
    return inputs


def overlay_lock(inputs: dict) -> None:
    path = ROOT / "source/package-lock.json"
    if digest(path) != inputs["lock"]["before_sha256"]:
        raise ValueError("original SDK dependency lock differs")
    original = path.read_text()
    before = json.loads(original)
    record = before["packages"][inputs["lock_path"]]
    if (
        record["version"] != inputs["version"]
        or record["resolved"] != inputs["original_archive"]["url"]
        or record["integrity"] != inputs["original_archive"]["integrity"]
    ):
        raise ValueError("original dependency lock record differs")
    after = copy.deepcopy(before)
    after["packages"][inputs["lock_path"]].update(
        resolved=inputs["lock"]["resolved"], integrity=inputs["derived_archive"]["integrity"]
    )
    if original.count(record["resolved"]) != 1 or original.count(record["integrity"]) != 1:
        raise ValueError("ambiguous dependency lock replacement")
    encoded = original.replace(record["resolved"], inputs["lock"]["resolved"]).replace(
        record["integrity"], inputs["derived_archive"]["integrity"]
    )
    if (
        json.loads(encoded) != after
        or hashlib.sha256(encoded.encode()).hexdigest() != inputs["lock"]["after_sha256"]
    ):
        raise ValueError("declared dependency lock overlay differs")
    path.write_text(encoded)


def prepare(vendor: str, *, offline: bool) -> None:
    command(
        [
            sys.executable,
            "-I",
            "-B",
            str(RECIPE / "scripts/runtime_clis/prepare_vendor.py"),
            "--series",
            str(RECIPE / f"deploy/runtime-clis/{vendor}/series.json"),
            "--archive",
            str(ROOT / "source.tgz"),
            "--output",
            str(ROOT / "source"),
            "--receipt",
            str(ROOT / "prepared.json"),
        ],
        offline=offline,
        cwd=ROOT,
    )

    if vendor == "js-genai":
        overlay_lock(build_inputs())


def cache_inventory(root: Path, helper: types.ModuleType) -> list[dict]:
    """Cache links must remain readable within their complete cache root.

    Upstream source fixtures deliberately have a different boundary: their
    literal links, including dangling test fixtures, are bound by the series.
    """
    if root.is_symlink() or not root.is_dir():
        raise ValueError("SDK cache must be a real directory")
    for path in root.rglob("*"):
        if path.is_symlink():
            try:
                target = path.resolve(strict=True)
            except (OSError, RuntimeError) as error:
                raise ValueError("SDK cache link is unreadable") from error
            if not target.is_relative_to(root.resolve()):
                raise ValueError("SDK cache link escapes its root")
    return helper.inventory(root)


def source_identity(root: Path, entries: list[dict], helper: types.ModuleType) -> None:
    expected = {entry["path"]: entry for entry in entries}
    current = {entry["path"]: entry for entry in helper.inventory(root)}
    if any(current.get(path) != entry for path, entry in expected.items()):
        raise ValueError("original SDK source changed during lifecycle execution")


def public_seed(vendor: str) -> None:
    series, _artifact, helper = identity(vendor)
    recipe = pins(vendor)
    helper.fetch({**series["archive"], "maximum_bytes": 256 * 1024 * 1024}, ROOT / "source.tgz")
    if vendor == "js-genai":
        dependency_inputs(offline=False)
    prepare(vendor, offline=False)
    for name in ("npm-cache", "browser-cache"):
        (ROOT / name).mkdir()
    for name in ("npm-user.npmrc", "npm-global.npmrc"):
        (ROOT / name).touch()
    before = helper.inventory(ROOT / "source")
    command(npm("ci", "--foreground-scripts"), offline=False, cwd=ROOT / "source")
    source_identity(ROOT / "source", before, helper)
    if pins(vendor) != recipe:
        raise ValueError("declared SDK build recipe changed during lifecycle execution")
    write_json(
        ROOT / "seed.json",
        {
            "status": "NONFINAL",
            "vendor": vendor,
            "recipe": recipe,
            "source": before,
            "source_archive_sha256": digest(ROOT / "source.tgz"),
            "npm_cache": cache_inventory(ROOT / "npm-cache", helper),
            "browser_cache": cache_inventory(ROOT / "browser-cache", helper),
            "dependencies": cache_inventory(ROOT / "dependencies", helper)
            if vendor == "js-genai"
            else [],
            "configuration": {
                name: digest(ROOT / name) for name in ("npm-user.npmrc", "npm-global.npmrc")
            },
        },
    )


def build(vendor: str) -> None:
    series, artifact, helper = identity(vendor)
    helper.network_none()
    seed = json.loads((ROOT / "seed.json").read_text())
    if (
        seed["status"] != "NONFINAL"
        or seed["vendor"] != vendor
        or seed["recipe"] != pins(vendor)
        or digest(ROOT / "source.tgz") != seed["source_archive_sha256"]
        or cache_inventory(ROOT / "npm-cache", helper) != seed["npm_cache"]
        or cache_inventory(ROOT / "browser-cache", helper) != seed["browser_cache"]
        or {name: digest(ROOT / name) for name in seed["configuration"]} != seed["configuration"]
    ):
        raise ValueError("SDK public seed inputs changed")
    # The prior layer is immutable evidence. Recreate the original source at
    # the same fixed path, then run every ordinary install/prepare/pack hook
    # without a route. No ignored scripts, omitted dependencies or test stubs.
    if vendor == "js-genai":
        if cache_inventory(ROOT / "dependencies", helper) != seed["dependencies"]:
            raise ValueError("declared SDK dependency inputs changed")
        dependency_inputs(offline=True)
    shutil.rmtree(ROOT / "source")
    prepare(vendor, offline=True)
    if helper.inventory(ROOT / "source") != seed["source"]:
        raise ValueError("fresh SDK preparation differs")
    command(npm("ci", "--offline", "--foreground-scripts"), offline=True, cwd=ROOT / "source")
    source_identity(ROOT / "source", seed["source"], helper)
    if pins(vendor) != seed["recipe"]:
        raise ValueError("declared SDK build recipe changed during lifecycle execution")
    package = json.loads((ROOT / "source/package.json").read_text())
    name, version, _input = PACKAGES[vendor]
    if (package["name"], package["version"]) != (name, version):
        raise ValueError("SDK public package identity differs")
    packed = ROOT / "packed"
    packed.mkdir()
    command(
        npm("pack", "--foreground-scripts", "--pack-destination", str(packed)),
        offline=True,
        cwd=ROOT / "source",
    )
    source_identity(ROOT / "source", seed["source"], helper)
    if pins(vendor) != seed["recipe"]:
        raise ValueError("declared SDK build recipe changed during pack execution")
    expected_name = Path(artifact["path"]).name
    if {path.name for path in packed.iterdir()} != {expected_name} or digest(
        packed / expected_name
    ) != artifact["sha256"]:
        raise ValueError("fixed-builder SDK artifact differs from the declared materializer input")
    helper.network_none()
    output = OUTPUT / vendor
    if output.exists() or output.is_symlink():
        raise ValueError("SDK artifact output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="sdk-", dir=output.parent) as temporary:
        stage = Path(temporary) / "output"
        stage.mkdir(mode=0o755)
        shutil.copyfile(packed / expected_name, stage / expected_name)
        (stage / expected_name).chmod(0o644)
        # Cache indexes and npm logs contain acquisition timestamps. They are
        # checked as closed inputs above, not copied into reproducible package
        # provenance. Source locks, recipe and exact packed bytes are stable.
        write_json(
            stage / "provenance.json",
            {
                "schema": "butlers.npm-artifact/v1",
                "vendor": vendor,
                "name": name,
                "version": version,
                "recipe": seed["recipe"],
                "source_archive_sha256": series["archive"]["sha256"],
                "source_tree_sha256": series["source_tree_sha256"],
                "package_lock_sha256": digest(ROOT / "source/package-lock.json"),
                "archive_sha256": digest(stage / expected_name),
                "lifecycle_scripts": "all original hooks executed",
                "lifecycle_cache_branch": "declared test-server archive"
                if vendor == "js-genai"
                else None,
                "npm_child_umask": "0027",
                "build_dependencies": build_inputs() if vendor == "js-genai" else {},
                "node_sha256": digest(Path("/opt/node/bin/node")),
                "source_date_epoch": "0",
            },
        )
        stage.rename(output)
    print("COMPLETE: offline SDK package matches declared materializer input", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vendor", choices=tuple(PACKAGES), required=True)
    parser.add_argument("--phase", choices=("public-seed", "build"), required=True)
    arguments = parser.parse_args()
    if arguments.phase == "public-seed":
        public_seed(arguments.vendor)
    else:
        build(arguments.vendor)


if __name__ == "__main__":
    main()
