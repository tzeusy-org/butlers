#!/usr/bin/env python3
"""Reproduce the complete fixed OpenCode default-plugin seed without a route."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import platform
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import types
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path("/opt/plugin-build")
RECIPE = Path("/recipe")
NODE = Path("/opt/node")
OUTPUT = Path("/artifacts/default-plugin")
DECLARATION = "deploy/runtime-clis/default-plugin-artifacts"
DEPENDENCIES = {
    "@opencode-ai/plugin": "1.17.7",
    "@opentui/core": "0.3.4",
    "@opentui/keymap": "0.3.4",
    "@opentui/solid": "0.3.4",
}
IMPORTS = (
    "@opencode-ai/plugin",
    "@opencode-ai/plugin/tool",
    "@opencode-ai/plugin/tui",
    "@opencode-ai/sdk",
    "@opencode-ai/sdk/v2",
    "@opencode-ai/sdk/client",
    "@opencode-ai/sdk/server",
    "@opencode-ai/sdk/v2/client",
    "@opencode-ai/sdk/v2/server",
    "@opencode-ai/sdk/v2/gen/client",
)
CONFIGURATION = ("npm-user.npmrc", "npm-global.npmrc")
PRESENT_SUPPLEMENTS = ("node_modules/@opentui/core-linux-x64-musl",)


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    path.chmod(0o644)


def helper(name: str) -> types.ModuleType:
    if name not in {"produce_codex_artifacts", "produce_native_packages", "runtime_artifacts"}:
        raise ValueError("undeclared plugin build helper")
    path = RECIPE / "scripts/runtime_clis" / f"{name}.py"
    if path.is_symlink() or not path.is_file():
        raise ValueError("plugin build helper must be a declared regular source")
    module = types.ModuleType("_plugin_builder_" + name)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), module.__dict__)
    return module


class OwnerSignal(BaseException):
    def __init__(self, number: int):
        self.number = number
        super().__init__(number)


@contextlib.contextmanager
def owner_signals():
    def interrupted(number, _frame):
        raise OwnerSignal(number)

    previous = {
        number: signal.signal(number, interrupted) for number in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        yield
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


class Operations:
    """Every owned command settles before cancellation leaves this scope."""

    def run(
        self,
        argv: list[str],
        *,
        cwd: Path,
        env: dict[str, str],
        capture: bool = False,
        timeout: int = 1800,
    ) -> str:
        # Defer both owner signals while acquiring the process handle and while
        # settling it. Children retain their ordinary unblocked signal masks. An
        # owner signal never raises inside subprocess internals: while armed it
        # kills the child's group, communicate() returns, and OwnerSignal is
        # raised when this scope exits (bu-kiuh8v.34).
        with command_signals() as (arm, disarm):
            child = subprocess.Popen(
                argv,
                cwd=cwd,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE if capture else None,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            try:
                arm(child)
                output, _ = child.communicate(timeout=timeout)
            finally:
                disarm()
                # Also settle descendants left behind by a successful leader.
                with contextlib.suppress(ProcessLookupError):
                    os.killpg(child.pid, signal.SIGKILL)
                child.wait()
        if child.returncode:
            if output:
                sys.stderr.write(output.decode("utf-8", errors="replace")[-32768:])
            raise subprocess.CalledProcessError(child.returncode, argv)
        return output.decode("utf-8") if output else ""


@contextlib.contextmanager
def command_signals():
    # The handler never raises: an exception delivered between subprocess's
    # waitpid-lock acquire and its try/finally leaks the lock, and the settling
    # wait() then blocks forever. It records the first signal and, while a
    # child is armed and unreaped, kills that child's process group.
    requested = None
    armed = None

    def stop(child):
        if child.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(child.pid, signal.SIGKILL)

    def interrupted(number, _frame):
        nonlocal requested
        if requested is None:
            requested = number
        if armed is not None:
            stop(armed)

    def arm(child):
        nonlocal armed
        armed = child
        if requested is not None:
            stop(child)

    def disarm():
        nonlocal armed
        armed = None

    previous = {
        number: signal.signal(number, interrupted) for number in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        yield arm, disarm
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)
    if requested is not None:
        raise OwnerSignal(requested)


def environment(*, offline: bool) -> dict[str, str]:
    return {
        "HOME": os.environ["HOME"],
        "PATH": "/opt/node/bin:/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": "0",
        "npm_config_offline": str(offline).lower(),
    }


def npm(*arguments: str, cache: Path) -> list[str]:
    return [
        str(NODE / "bin/node"),
        str(NODE / "lib/node_modules/npm/bin/npm-cli.js"),
        "--registry=https://registry.npmjs.org",
        "--cache",
        str(cache),
        "--userconfig",
        str(ROOT / CONFIGURATION[0]),
        "--globalconfig",
        str(ROOT / CONFIGURATION[1]),
        "--no-audit",
        "--no-fund",
        *arguments,
    ]


def pins() -> dict[str, str]:
    paths = [
        "Dockerfile.default-plugin-artifacts",
        "scripts/runtime_clis/produce_default_plugin_artifact.py",
        "scripts/runtime_clis/produce_codex_artifacts.py",
        "scripts/runtime_clis/produce_native_packages.py",
        "scripts/runtime_clis/runtime_artifacts.py",
        "scripts/runtime_clis/install_system_artifacts.py",
        "deploy/runtime-clis/toolchains.json",
        "deploy/runtime-clis/debian-system-inputs.json",
        *[
            DECLARATION + "/" + name
            for name in ("build-inputs.json", "package.json", "package-lock.json")
        ],
    ]
    return {name: digest(RECIPE / name) for name in paths}


def declarations() -> tuple[dict, dict]:
    source = RECIPE / DECLARATION
    manifest = json.loads((source / "build-inputs.json").read_bytes())
    package = json.loads((source / "package.json").read_bytes())
    lock = json.loads((source / "package-lock.json").read_bytes())
    tools = json.loads((RECIPE / "deploy/runtime-clis/toolchains.json").read_bytes())
    if (
        manifest["schema"] != "butlers.default-plugin-build-inputs/v1"
        or manifest["target"] != helper("runtime_artifacts").TARGET
        or {key: value for key, value in manifest["node"].items() if key != "tree_sha256"}
        != tools["node"]
        or not isinstance(manifest["node"]["tree_sha256"], str)
        or len(manifest["node"]["tree_sha256"]) != 64
        or any(c not in "0123456789abcdef" for c in manifest["node"]["tree_sha256"])
        or manifest["npm"] != tools["npm"]["version"]
        or {key: value for key, value in manifest["bun"].items() if key != "size"} != tools["bun"]
        or package["dependencies"] != DEPENDENCIES
        or lock["packages"][""]["dependencies"] != DEPENDENCIES
        or digest(source / "package.json") != manifest["package_json_sha256"]
        or digest(source / "package-lock.json") != manifest["package_lock_sha256"]
        or manifest["ordinary_npm_installed"] != 130
        or manifest["ordinary_present_supplements"] != list(PRESENT_SUPPLEMENTS)
        or manifest["complete_lock_packages"] != 141
        or len(lock["packages"]) != 142
        or manifest["imports"]
        != {
            "bun": list(IMPORTS),
            "node": [name for name in IMPORTS if name != "@opencode-ai/plugin/tui"],
        }
    ):
        raise ValueError("fixed complete plugin declarations differ")
    for path, record in lock["packages"].items():
        if not path:
            continue
        name = PurePosixPath(path)
        url = urllib.parse.urlsplit(record["resolved"])
        if (
            name.is_absolute()
            or ".." in name.parts
            or "\\" in path
            or name.parts[0] != "node_modules"
            or any(ord(c) < 32 for c in path)
            or url.scheme != "https"
            or url.netloc != "registry.npmjs.org"
            or url.query
            or url.fragment
            or not record["integrity"].startswith("sha512-")
        ):
            raise ValueError("unexpected plugin lock input")
    supplements = manifest["supplements"]
    if len(supplements) != 12 or len({item["lock_path"] for item in supplements}) != 12:
        raise ValueError("exact twelve complete supplements required")
    for item in supplements:
        record = lock["packages"][item["lock_path"]]
        if (
            record["version"] != item["version"]
            or record["resolved"] != item["archive_url"]
            or record["integrity"] != item["archive_integrity"]
            or record.get("optional") is not True
            or record.get("os") != item["os"]
            or record.get("cpu") != item["cpu"]
            or record.get("libc") != item["lock_libc"]
            or item["package"] != item["lock_path"].rsplit("node_modules/", 1)[1]
        ):
            raise ValueError("supplement differs from its original lock record")
    if [(item["name"], item["version"], item["filename"]) for item in manifest["metadata"]] != [
        ("@opencode-ai/plugin", "1.17.7", "plugin-metadata.json"),
        ("@opencode-ai/sdk", "1.17.7", "sdk-metadata.json"),
    ]:
        raise ValueError("exact original public version metadata required")
    if any(
        item["url"] != f"https://registry.npmjs.org/{item['name']}/{item['version']}"
        for item in manifest["metadata"]
    ):
        raise ValueError("public metadata authority/path differs")
    return manifest, lock


def identity(manifest: dict) -> None:
    if (
        Path.cwd() != ROOT
        or platform.python_version() != "3.12.13"
        or platform.machine() != "x86_64"
        or platform.libc_ver() != ("glibc", "2.41")
    ):
        raise ValueError("fixed pinned Linux/amd64 plugin builder required")
    home = Path(os.environ.get("HOME", "/missing-builder-home"))
    if (
        not home.is_absolute()
        or home.is_symlink()
        or not home.is_dir()
        or home.stat().st_uid != os.getuid()
        or any(home.iterdir())
    ):
        raise ValueError("plugin builder requires an empty owned home backing")
    validate_node(manifest)


def validate_node(manifest: dict) -> None:
    """Bind only the complete declared Node input, never an enclosing output tree."""
    artifacts = helper("runtime_artifacts")
    if (
        digest(NODE / "bin/node") != manifest["node"]["binary_sha256"]
        or artifacts.tree_digest(artifacts.inventory(NODE, closed=True))
        != manifest["node"]["tree_sha256"]
    ):
        raise ValueError("complete official Node input differs")
    if (
        json.loads((NODE / "lib/node_modules/npm/package.json").read_bytes())["version"]
        != manifest["npm"]
    ):
        raise ValueError("bundled npm version differs")


class ManualStatus(urllib.request.HTTPErrorProcessor):
    """No implicit redirect/auth retry can bypass explicit response checks."""

    def http_response(self, request, response):
        return response

    https_response = http_response


def fetch_tool_input(record: dict, output: Path, *, bun: bool = False, opener=None) -> None:
    """Only Bun's one explicit public GitHub asset transition is admitted."""
    if output.exists() or output.is_symlink():
        raise ValueError("new tool input destination required")
    source = urllib.parse.urlsplit(record["url"])
    expected = "github.com" if bun else "registry.npmjs.org"
    if source.scheme != "https" or source.netloc != expected or source.query or source.fragment:
        raise ValueError("invalid public tool input")
    public = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), ManualStatus())
    current = record["url"]
    start = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="plugin-input-", dir=output.parent) as temporary:
            stage = Path(temporary) / "payload"
            for hop in range(2):
                with public.open(current, timeout=60) as response:
                    if 300 <= response.status < 400:
                        locations = response.headers.get_all("Location", [])
                        if not bun or hop != 0 or len(locations) != 1:
                            raise ValueError("public input redirect refused")
                        target = urllib.parse.urlsplit(urllib.parse.urljoin(current, locations[0]))
                        if (
                            target.scheme != "https"
                            or target.netloc != "release-assets.githubusercontent.com"
                            or target.fragment
                            or not target.path.startswith("/")
                            or any(ord(c) < 32 for c in locations[0])
                        ):
                            raise ValueError("public asset redirect refused")
                        current = target.geturl()
                        continue
                    if response.status != 200 or response.geturl() != current:
                        raise ValueError("unexpected public input response")
                    size = 0
                    with stage.open("xb") as stream:
                        while block := response.read(1024**2):
                            size += len(block)
                            if size > record["size"] or time.monotonic() - start > 300:
                                raise ValueError("public input exceeds its bound")
                            stream.write(block)
                    if size != record["size"] or digest(stage) != record["sha256"]:
                        raise ValueError("public input bytes differ")
                    stage.chmod(0o644)
                    os.link(stage, output)
                    return
            raise ValueError("public input redirect bound exceeded")
    except Exception:
        # HTTPError/URLError can contain a transient signed Location URL. Do
        # not propagate either its text or chained context into build logs.
        raise ValueError("pinned public tool input acquisition failed") from None


def extract_bun(archive: Path, output: Path, record: dict) -> None:
    if (
        archive.is_symlink()
        or archive.stat().st_size != record["size"]
        or digest(archive) != record["sha256"]
        or output.exists()
        or output.is_symlink()
    ):
        raise ValueError("complete official Bun archive differs")
    with zipfile.ZipFile(archive) as source:
        members = source.infolist()
        names = set()
        files = set()
        total = 0
        for member in members:
            path = PurePosixPath(member.filename)
            mode = member.external_attr >> 16
            if (
                path.is_absolute()
                or not path.parts
                or path.parts[0] != "bun-linux-x64"
                or ".." in path.parts
                or "\\" in member.filename
                or any(ord(c) < 32 for c in member.filename)
                or path in names
                or stat.S_IFMT(mode) not in (0, stat.S_IFDIR, stat.S_IFREG)
                or mode & 0o7000
                or member.flag_bits & 1
            ):
                raise ValueError("unsafe complete Bun member")
            names.add(path)
            total += member.file_size
            if not member.is_dir():
                files.add(path)
        if (
            not members
            or len(members) > 1000
            or total > 512 * 1024**2
            or any(parent in files for path in names for parent in path.parents)
        ):
            raise ValueError("invalid complete Bun archive layout")
        output.mkdir(mode=0o755)
        output.chmod(0o755)
        for member in members:
            target = output / PurePosixPath(member.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            if member.is_dir():
                target.mkdir(exist_ok=True)
            else:
                with source.open(member) as incoming, target.open("xb") as stream:
                    shutil.copyfileobj(incoming, stream)
                target.chmod(0o755 if (member.external_attr >> 16) & 0o111 else 0o644)
        for directory in output.rglob("*"):
            if directory.is_dir():
                directory.chmod(0o755)
    binary = output / "bun-linux-x64/bun"
    if (
        binary.is_symlink()
        or digest(binary) != record["binary_sha256"]
        or binary.stat().st_mode & 0o777 != 0o755
    ):
        raise ValueError("original Bun executable differs")


def configuration() -> None:
    for name in CONFIGURATION:
        path = ROOT / name
        if not path.exists() and not path.is_symlink():
            path.touch(mode=0o644, exist_ok=False)
            path.chmod(0o644)
        if path.is_symlink() or not path.is_file() or path.read_bytes():
            raise ValueError("only empty explicit npm configuration is permitted")


def project(directory: Path) -> None:
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    for name in ("package.json", "package-lock.json"):
        shutil.copyfile(RECIPE / DECLARATION / name, directory / name)
        (directory / name).chmod(0o644)


def package_population(directory: Path, lock: dict, manifest: dict, *, complete: bool) -> None:
    if {path.name for path in directory.iterdir()} != {
        "package.json",
        "package-lock.json",
        "node_modules",
    }:
        raise ValueError("original plugin root population differs")
    absent = set()
    for name, record in lock["packages"].items():
        if not name:
            continue
        path = directory / name
        if not path.exists() and not path.is_symlink():
            absent.add(name)
            continue
        if path.is_symlink() or not path.is_dir() or (path / "package.json").is_symlink():
            raise ValueError("original installed package must be a real directory")
        package = json.loads((path / "package.json").read_bytes())
        expected_name = name.rsplit("node_modules/", 1)[1]
        if package["name"] != expected_name or package["version"] != record["version"]:
            raise ValueError("installed package differs from original full lock")
    expected_absent = (
        set()
        if complete
        else {
            item["lock_path"]
            for item in manifest["supplements"]
            if item["lock_path"] not in PRESENT_SUPPLEMENTS
        }
    )
    if absent != expected_absent:
        # These are committed public lock paths, never environment values or
        # arbitrary filesystem paths. Preserve the exact failed population so
        # a failed temporary build can be diagnosed without weakening its gate.
        raise ValueError(
            "ordinary or complete package population differs: "
            + json.dumps(
                {"absent": sorted(absent), "expected_absent": sorted(expected_absent)},
                sort_keys=True,
            )
        )
    for name in ("package.json", "package-lock.json"):
        if digest(directory / name) != digest(RECIPE / DECLARATION / name):
            raise ValueError("npm changed the original root declarations")


def supplemental_record(item: dict) -> dict:
    return {
        "name": item["package"],
        "version": item["version"],
        "url": item["archive_url"],
        "size": item["archive_bytes"],
        "maximum_bytes": item["archive_bytes"],
        "sha256": item["archive_sha256"],
        "integrity": item["archive_integrity"],
    }


def preserve_present_supplements(project: Path, archives: Path, manifest: dict) -> None:
    """Verify the exact ordinary npm musl payload; never overwrite or execute it."""
    artifacts = helper("runtime_artifacts")
    native = helper("produce_native_packages")
    for index, item in enumerate(manifest["supplements"]):
        if item["lock_path"] not in PRESENT_SUPPLEMENTS:
            continue
        installed = project / item["lock_path"]
        with tempfile.TemporaryDirectory(prefix="verify-present-", dir=ROOT) as temporary:
            extracted = Path(temporary) / "package"
            native.extract(supplemental_record(item), archives / f"{index:02d}.tgz", extracted)
            package = json.loads((extracted / "package.json").read_bytes())
            if any(
                name in package.get("scripts", {})
                for name in ("preinstall", "install", "postinstall")
            ):
                raise ValueError("present data supplement unexpectedly has lifecycle hooks")
            if artifacts.inventory(installed) != artifacts.inventory(extracted, closed=True):
                raise ValueError(
                    "ordinary present supplement differs from complete original archive"
                )


def metadata_identity(directory: Path, manifest: dict, lock: dict) -> None:
    for record in manifest["metadata"]:
        path = directory / record["filename"]
        if (
            path.is_symlink()
            or path.stat().st_size != record["size"]
            or digest(path) != record["sha256"]
        ):
            raise ValueError("original public version metadata differs")
        value = json.loads(path.read_bytes())
        dependency = lock["packages"]["node_modules/" + record["name"]]
        if (
            value["name"] != record["name"]
            or value["version"] != record["version"]
            or value["dist"]["integrity"] != dependency["integrity"]
            or value["dist"]["tarball"] != dependency["resolved"]
        ):
            raise ValueError("public metadata differs from original locked package")


def public_seed(operations: Operations) -> None:
    manifest, lock = declarations()
    identity(manifest)
    before = pins()
    configuration()
    destination = ROOT / "inputs"
    if destination.exists() or destination.is_symlink() or (ROOT / "seed.json").exists():
        raise ValueError("new plugin public seed required")
    artifacts = helper("runtime_artifacts")
    native = helper("produce_native_packages")
    # Acquisition and ordinary native lifecycle hooks are deliberately NONFINAL.
    # An independently copied, inventoried cache is the build phase's input.
    with tempfile.TemporaryDirectory(prefix="plugin-public-", dir=ROOT) as temporary:
        stage = Path(temporary) / "inputs"
        stage.mkdir(mode=0o755)
        (stage / "archives").mkdir(mode=0o755)
        (stage / "metadata").mkdir(mode=0o755)
        for record in manifest["metadata"]:
            fetch_tool_input(record, stage / "metadata" / record["filename"])
        metadata_identity(stage / "metadata", manifest, lock)
        fetch_tool_input(manifest["bun"], stage / "bun.zip", bun=True)
        for index, item in enumerate(manifest["supplements"]):
            native.download(supplemental_record(item), stage / "archives" / f"{index:02d}.tgz")
        project(stage / "ordinary")
        operations.run(
            npm("ci", cache=stage / "npm-cache"),
            cwd=stage / "ordinary",
            env=environment(offline=False),
        )
        package_population(stage / "ordinary", lock, manifest, complete=False)
        preserve_present_supplements(stage / "ordinary", stage / "archives", manifest)
        if before != pins():
            raise ValueError("plugin recipe changed during public seed")
        entries = artifacts.inventory(stage)
        copied = Path(temporary) / "closed"
        artifacts._copy(stage, copied, entries)
        if (
            artifacts.inventory(copied, closed=True) != entries
            or artifacts.inventory(stage) != entries
        ):
            raise ValueError("public seed independent copy differs")
        if destination.exists() or destination.is_symlink():
            raise ValueError("plugin public seed appeared during acquisition")
        copied.rename(destination)
    write_json(ROOT / "seed.json", {"status": "NONFINAL", "recipe": before, "inputs": entries})
    print("COMPLETE: NONFINAL original plugin public inputs retained", flush=True)


def import_families(directory: Path, bun: Path, manifest: dict, operations: Operations) -> dict:
    env = environment(offline=True)
    for command, expected in (
        ([str(NODE / "bin/node"), "--version"], manifest["node"]["version"]),
        ([str(bun), "--version"], manifest["bun"]["version"]),
    ):
        if (
            operations.run(command, cwd=directory, env=env, capture=True, timeout=60).strip()
            != expected
        ):
            raise ValueError("original import runtime version differs")
    observed = {}
    for runtime in ("node", "bun"):
        names = manifest["imports"][runtime]
        marker = (
            "ALL_NINE_NODE_EXPORT_FAMILIES_IMPORTED"
            if runtime == "node"
            else "ALL_TEN_ORIGINAL_EXPORT_FAMILIES_IMPORTED"
        )
        source = (
            "const names=" + json.dumps(names) + ";for(const name of names){"
            "const value=await import(name);console.log(JSON.stringify({module:name,"
            "exports:Object.keys(value).sort()}));}console.log(" + json.dumps(marker) + ");"
        )
        command = (
            [str(NODE / "bin/node"), "--input-type=module", "-e", source]
            if runtime == "node"
            else [str(bun), "--eval", source]
        )
        output = operations.run(command, cwd=directory, env=env, capture=True, timeout=120)
        lines = output.splitlines()
        if not lines or lines[-1] != marker or len(lines) != len(names) + 1:
            raise ValueError("original export import terminator/population differs")
        records = [json.loads(line) for line in lines[:-1]]
        if [record["module"] for record in records] != names or any(
            not isinstance(record["exports"], list)
            or not record["exports"]
            or record["exports"] != sorted(set(record["exports"]))
            for record in records
        ):
            raise ValueError("original export family result differs")
        observed[runtime] = records
    return observed


def provenance(manifest: dict, recipe: dict, imports: dict) -> dict:
    """Content identities only; mutable npm cache/log clocks are not final inputs."""
    return {
        "schema": "butlers.default-plugin-provenance/v1",
        "recipe": recipe,
        "public_inputs": manifest,
        "ordinary_npm_lifecycle_packages": 130,
        "supplemental_data_packages": 12,
        "supplemental_appended_packages": 11,
        "supplemental_preserved_ordinary_packages": list(PRESENT_SUPPLEMENTS),
        "complete_lock_packages": 141,
        "commands": ["npm ci (all lifecycle scripts)", "npm ci --offline (all lifecycle scripts)"],
        "supplements": "complete SRI-bound archives carried as data; no foreign-platform execution",
        "imports": imports,
    }


def build(operations: Operations) -> None:
    guard = helper("produce_codex_artifacts")
    guard.network_none()
    manifest, lock = declarations()
    identity(manifest)
    configuration()
    artifacts = helper("runtime_artifacts")
    native = helper("produce_native_packages")
    before = pins()
    seed = json.loads((ROOT / "seed.json").read_bytes())
    inputs = ROOT / "inputs"
    if seed != {
        "status": "NONFINAL",
        "recipe": before,
        "inputs": artifacts.inventory(inputs, closed=True),
    }:
        raise ValueError("original NONFINAL plugin inputs differ")
    metadata_identity(inputs / "metadata", manifest, lock)
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise ValueError("new plugin artifact output required")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="default-plugin-", dir=OUTPUT.parent) as temporary:
        work = Path(temporary)
        project(work / "project")
        cache_entries = artifacts.inventory(inputs / "npm-cache", closed=True)
        artifacts._copy(inputs / "npm-cache", work / "npm-cache", cache_entries)
        operations.run(
            npm("ci", "--offline", cache=work / "npm-cache"),
            cwd=work / "project",
            env=environment(offline=True),
        )
        package_population(work / "project", lock, manifest, complete=False)
        preserve_present_supplements(work / "project", inputs / "archives", manifest)
        for index, item in enumerate(manifest["supplements"]):
            if item["lock_path"] in PRESENT_SUPPLEMENTS:
                continue
            native.extract(
                supplemental_record(item),
                inputs / "archives" / f"{index:02d}.tgz",
                work / "project" / item["lock_path"],
            )
        package_population(work / "project", lock, manifest, complete=True)
        extract_bun(inputs / "bun.zip", work / "bun", manifest["bun"])
        imports = import_families(
            work / "project", work / "bun/bun-linux-x64/bun", manifest, operations
        )
        entries = sorted(artifacts.inventory(work / "project"), key=lambda entry: entry["path"])
        artifacts._copy(work / "project", work / "closed", entries)
        if (
            sorted(
                artifacts.inventory(work / "closed", closed=True), key=lambda entry: entry["path"]
            )
            != entries
        ):
            raise ValueError("complete plugin independent copy differs")
        final = {
            "schema": "butlers.opencode-default-plugin/v1",
            "package": "@opencode-ai/plugin",
            "version": "1.17.7",
            "node": manifest["node"]["version"],
            "npm": manifest["npm"],
            "package_lock_sha256": manifest["package_lock_sha256"],
            "entries": entries,
            "provenance": provenance(manifest, before, imports),
        }
        write_json(work / "closed/seed.json", final)
        guard.network_none()
        if before != pins() or artifacts.inventory(inputs, closed=True) != seed["inputs"]:
            raise ValueError("plugin recipe or public inputs changed during build")
        if OUTPUT.exists() or OUTPUT.is_symlink():
            raise ValueError("plugin output appeared during build")
        (work / "closed").rename(OUTPUT)
    print("COMPLETE: complete 141-package default plugin seed built offline", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", required=True, choices=("public-seed", "build"))
    args = parser.parse_args()
    try:
        with owner_signals():
            if args.phase == "public-seed":
                public_seed(Operations())
            else:
                build(Operations())
    except OwnerSignal as interrupted:
        signal.signal(interrupted.number, signal.SIG_DFL)
        os.kill(os.getpid(), interrupted.number)
        raise SystemExit(128 + interrupted.number) from None  # PID1 ignores default signals.


if __name__ == "__main__":
    main()
