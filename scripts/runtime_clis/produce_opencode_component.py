#!/usr/bin/env python3
"""Build the complete pinned OpenCode workspace, UI and installed package offline."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import shutil
import tarfile
import tempfile
import types
from pathlib import Path

ROOT = Path("/opt/opencode-build")
RECIPE = Path("/recipe")
NODE = Path("/opt/node")
PLUGIN = Path("/inputs/default-plugin")
OUTPUT = Path("/artifacts/opencode")
DECLARATION = "deploy/runtime-clis/opencode/component-inputs.json"
BUILD_INPUTS = "deploy/runtime-clis/opencode/build-inputs.json"
SERIES = "deploy/runtime-clis/opencode/series.json"
MODEL_SNAPSHOT = "deploy/runtime-clis/opencode/model-inputs.tar.gz"
BIN = "node_modules/opencode-linux-x64/bin/opencode"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    path.chmod(0o644)


def helper(name: str) -> types.ModuleType:
    if name not in {
        "produce_codex_artifacts",
        "produce_native_packages",
        "produce_default_plugin_artifact",
        "prepare_vendor",
        "runtime_artifacts",
    }:
        raise ValueError("undeclared OpenCode producer helper")
    path = RECIPE / "scripts/runtime_clis" / (name + ".py")
    if path.is_symlink() or not path.is_file():
        raise ValueError("OpenCode helper must be a regular declared source")
    module = types.ModuleType("_opencode_builder_" + name)
    module.__file__ = str(path)
    exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), module.__dict__)
    if hasattr(module, "RECIPE"):
        module.__dict__["RECIPE"] = RECIPE
    return module


def declarations() -> tuple[dict, dict, dict]:
    manifest = json.loads((RECIPE / DECLARATION).read_bytes())
    series = json.loads((RECIPE / SERIES).read_bytes())
    models = json.loads((RECIPE / BUILD_INPUTS).read_bytes())
    tools = json.loads((RECIPE / "deploy/runtime-clis/toolchains.json").read_bytes())
    if (
        manifest["schema"] != "butlers.opencode-component-inputs/v1"
        or manifest["version"] != series["version"]
        or manifest["version"] != "1.17.7"
        or series["vendor"] != "opencode"
        or models["schema"] != "butlers.opencode-build-inputs/v1"
        or models["version"] != manifest["version"]
        or models["channel"] != "latest"
        or models["bun"] != tools["bun"]["version"]
        or {key: value for key, value in manifest["bun"].items() if key != "size"} != tools["bun"]
        or len(manifest["archives"]) != 2
        or [record["name"] for record in manifest["archives"]]
        != ["opencode-ai", "opencode-linux-x64"]
        or any(record["version"] != manifest["version"] for record in manifest["archives"])
        or models["catalogue"]["url"] != "https://models.dev/api.json"
        or len(models["logos"]) != models["catalogue"]["providers"]
        or len({item["provider"] for item in models["logos"]}) != len(models["logos"])
    ):
        raise ValueError("fixed complete OpenCode declarations differ")
    for record in manifest["archives"]:
        if record["size"] <= 0 or record["size"] != record["maximum_bytes"]:
            raise ValueError("official OpenCode archive size differs")
    plugin, _lock = helper("produce_default_plugin_artifact").declarations()
    if (
        manifest["node_tree_sha256"] != plugin["node"]["tree_sha256"]
        or manifest["default_plugin"]["package_json_sha256"] != plugin["package_json_sha256"]
        or manifest["default_plugin"]["package_lock_sha256"] != plugin["package_lock_sha256"]
        or manifest["default_plugin"]["complete_lock_packages"] != plugin["complete_lock_packages"]
    ):
        raise ValueError("declared complete plugin input differs")
    return manifest, series, models


def pins() -> dict[str, str]:
    series = json.loads((RECIPE / SERIES).read_bytes())
    paths = {
        "Dockerfile.opencode-artifacts",
        "scripts/runtime_clis/produce_opencode_component.py",
        "scripts/runtime_clis/produce_codex_artifacts.py",
        "scripts/runtime_clis/produce_native_packages.py",
        "scripts/runtime_clis/produce_default_plugin_artifact.py",
        "scripts/runtime_clis/prepare_vendor.py",
        "scripts/runtime_clis/runtime_artifacts.py",
        "scripts/runtime_clis/install_system_artifacts.py",
        "deploy/runtime-clis/toolchains.json",
        "deploy/runtime-clis/debian-system-inputs.json",
        "deploy/runtime-clis/codex/compiler-inputs.json",
        DECLARATION,
        BUILD_INPUTS,
        MODEL_SNAPSHOT,
        "deploy/runtime-clis/opencode/model-inputs.md",
        "deploy/runtime-clis/opencode/LICENSE.models-dev",
        SERIES,
        *helper("produce_default_plugin_artifact").pins(),
        *("deploy/runtime-clis/opencode/" + item["path"] for item in series["patches"]),
    }
    return {path: digest(RECIPE / path) for path in sorted(paths)}


def identity(manifest: dict) -> None:
    if (
        Path.cwd() != ROOT
        or platform.python_version() != "3.12.13"
        or platform.libc_ver() != ("glibc", "2.41")
        or platform.machine() != "x86_64"
    ):
        raise ValueError("fixed pinned OpenCode Linux/amd64 builder required")
    home = Path(os.environ.get("HOME", "/missing-builder-home"))
    if (
        not home.is_absolute()
        or home.is_symlink()
        or not home.is_dir()
        or home.stat().st_uid != os.getuid()
        or any(home.iterdir())
    ):
        raise ValueError("builder requires an empty owned home backing")
    compiler = json.loads((RECIPE / "deploy/runtime-clis/codex/compiler-inputs.json").read_bytes())
    if digest(Path("/var/lib/dpkg/status")) != compiler["output_installed_dpkg_status_sha256"]:
        raise ValueError("complete pinned OpenCode compiler profile differs")
    plugin = helper("produce_default_plugin_artifact")
    declaration, _ = plugin.declarations()
    plugin.validate_node(declaration)
    if declaration["node"]["tree_sha256"] != manifest["node_tree_sha256"]:
        raise ValueError("OpenCode Node input differs")


def plugin_input(manifest: dict) -> list[dict]:
    """Validate the real closed graph and its noncyclic producing recipe."""
    artifacts = helper("runtime_artifacts")
    entries = artifacts.inventory(PLUGIN, closed=True)
    seed = json.loads((PLUGIN / "seed.json").read_bytes())
    payload = sorted(
        (entry for entry in entries if entry["path"] != "seed.json"),
        key=lambda entry: entry["path"],
    )
    producer = helper("produce_default_plugin_artifact")
    declaration, lock = producer.declarations()
    if (
        set(seed)
        != {
            "schema",
            "package",
            "version",
            "node",
            "npm",
            "package_lock_sha256",
            "entries",
            "provenance",
        }
        or seed["schema"] != "butlers.opencode-default-plugin/v1"
        or seed["package"] != "@opencode-ai/plugin"
        or seed["version"] != manifest["version"]
        or seed["node"] != declaration["node"]["version"]
        or seed["npm"] != declaration["npm"]
        or seed["entries"] != payload
        or artifacts.tree_digest(payload) != manifest["default_plugin"]["entries_sha256"]
        or seed["package_lock_sha256"] != declaration["package_lock_sha256"]
        or digest(PLUGIN / "package-lock.json") != declaration["package_lock_sha256"]
        or digest(PLUGIN / "package.json") != declaration["package_json_sha256"]
        or seed["provenance"]["schema"] != "butlers.default-plugin-provenance/v1"
        or seed["provenance"]["recipe"] != producer.pins()
        or seed["provenance"]["public_inputs"] != declaration
    ):
        raise ValueError("complete source-owned plugin seed differs")
    # A published seed has one additional metadata file, unlike the producer's
    # raw npm project. Check the same complete lock population in this layout.
    if {path.name for path in PLUGIN.iterdir()} != {
        "package.json",
        "package-lock.json",
        "node_modules",
        "seed.json",
    }:
        raise ValueError("published plugin root population differs")
    for relative, expected in lock["packages"].items():
        if not relative:
            continue
        directory = PLUGIN / relative
        package = directory / "package.json"
        if (
            directory.is_symlink()
            or not directory.is_dir()
            or package.is_symlink()
            or not package.is_file()
        ):
            raise ValueError("complete published plugin lock package is missing")
        actual = json.loads(package.read_bytes())
        if (
            actual["name"] != relative.rsplit("node_modules/", 1)[1]
            or actual["version"] != expected["version"]
        ):
            raise ValueError("published plugin package differs from complete lock")
    return entries


def materialize_models(models: dict) -> None:
    """Stage the entire retained public snapshot without a mutable HTTP dependency."""
    archive_path = RECIPE / MODEL_SNAPSHOT
    record = models["snapshot"]
    output = ROOT / "models"
    if (
        record["path"] != "model-inputs.tar.gz"
        or record["format"] != "ustar+gzip"
        or record["members"] != len(models["logos"]) + 1
        or archive_path.is_symlink()
        or not archive_path.is_file()
        or archive_path.stat().st_size != record["size"]
        or digest(archive_path) != record["sha256"]
        or output.exists()
        or output.is_symlink()
    ):
        raise ValueError("closed model snapshot input differs")
    expected = {"api.json": models["catalogue"]}
    expected.update({"logos/" + item["provider"] + ".svg": item for item in models["logos"]})
    if len(expected) != record["members"] or any(
        name != "api.json" and re.fullmatch(r"logos/[a-z0-9][a-z0-9._-]*\.svg", name) is None
        for name in expected
    ):
        raise ValueError("model snapshot declaration population differs")
    with tarfile.open(archive_path, "r:gz") as archive:
        members = archive.getmembers()
        if [item.name for item in members] != sorted(expected):
            raise ValueError("model snapshot member population differs")
        for item in members:
            if (
                not item.isfile()
                or item.issparse()
                or item.mode != 0o644
                or item.uid != 0
                or item.gid != 0
                or item.mtime != 0
                or item.uname
                or item.gname
                or item.pax_headers
                or item.size < 1
                or item.size > (64 * 1024**2 if item.name == "api.json" else 2 * 1024**2)
            ):
                raise ValueError("model snapshot requires canonical finite data members")
        with tempfile.TemporaryDirectory(prefix="model-snapshot-", dir=ROOT) as temporary:
            stage = Path(temporary) / "models"
            stage.mkdir(mode=0o755)
            stage.chmod(0o755)
            (stage / "logos").mkdir(mode=0o755)
            (stage / "logos").chmod(0o755)
            for item in members:
                stream = archive.extractfile(item)
                if stream is None:
                    raise ValueError("model snapshot member is not data")
                with stream:
                    data = stream.read(item.size + 1)
                original = expected[item.name]
                if (
                    len(data) != item.size
                    or hashlib.sha256(data).hexdigest() != original["sha256"]
                    or "size" in original
                    and len(data) != original["size"]
                ):
                    raise ValueError("original model snapshot payload differs")
                path = stage / item.name
                path.write_bytes(data)
                path.chmod(0o644)
            models_identity(models, root=stage)
            stage.rename(output)


def models_identity(models: dict, *, root: Path | None = None) -> None:
    root = ROOT / "models" if root is None else root
    catalogue = root / "api.json"
    logos = root / "logos"
    data = json.loads(catalogue.read_bytes())
    expected = {record["provider"] + ".svg" for record in models["logos"]}
    if (
        digest(catalogue) != models["catalogue"]["sha256"]
        or len(data) != models["catalogue"]["providers"]
        or sum(len(provider["models"]) for provider in data.values())
        != models["catalogue"]["models"]
        or {record["provider"] for record in models["logos"]} != set(data)
        or {path.name for path in logos.iterdir()} != expected
    ):
        raise ValueError("complete model catalogue/logo population differs")
    for record in models["logos"]:
        path = logos / (record["provider"] + ".svg")
        if (
            path.is_symlink()
            or not path.is_file()
            or path.stat().st_size != record["size"]
            or digest(path) != record["sha256"]
        ):
            raise ValueError("original model logo differs")


def environment() -> dict[str, str]:
    return {
        "HOME": os.environ["HOME"],
        "PATH": str(ROOT / "bun/bun-linux-x64") + ":/opt/node/bin:/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "TZ": "UTC",
        "SOURCE_DATE_EPOCH": "0",
        "BUN_INSTALL_CACHE_DIR": str(ROOT / "bun-cache"),
        "BUN_INSTALL": str(ROOT / "bun-install"),
        "BUN_RUNTIME_TRANSPILER_CACHE_PATH": str(ROOT / "transpiler-cache"),
        "OPENCODE_VERSION": "1.17.7",
        "OPENCODE_CHANNEL": "latest",
        "MODELS_DEV_API_JSON": str(ROOT / "models/api.json"),
        "OPENCODE_MODELS_LOGOS": str(ROOT / "models/logos"),
        "npm_config_nodedir": str(NODE),
        "npm_config_python": "/usr/local/bin/python3",
    }


def command(operations, argv: list[str], *, cwd: Path, capture: bool = False) -> str:
    return operations.run(argv, cwd=cwd, env=environment(), capture=capture, timeout=1800)


def lock_identity(operations) -> dict:
    # The stock platform installers may rewrite JSONC spelling. Compare every
    # semantic lock field using the same pinned Bun parser, never prune records.
    source = "console.log(JSON.stringify(Bun.JSONC.parse(await Bun.file('bun.lock').text())))"
    return json.loads(
        command(
            operations,
            [str(ROOT / "bun/bun-linux-x64/bun"), "-e", source],
            cwd=ROOT / "source",
            capture=True,
        )
    )


def source_identity(expected: list[dict], lock: dict, operations) -> None:
    current = {
        item["path"]: item for item in helper("produce_codex_artifacts").inventory(ROOT / "source")
    }
    if any(current.get(item["path"]) != item for item in expected if item["path"] != "bun.lock"):
        raise ValueError("original OpenCode source changed during stock build")
    if lock_identity(operations) != lock:
        raise ValueError("complete original OpenCode lock semantics changed")


def prepare(series: dict) -> None:
    helper("prepare_vendor").prepare(RECIPE / SERIES, ROOT / "source.tgz", ROOT / "source")


def build_commands(operations) -> None:
    bun = str(ROOT / "bun/bun-linux-x64/bun")
    command(operations, [bun, "install", "--frozen-lockfile"], cwd=ROOT / "source")
    # --single selects the original Linux/x64 target. Keep all original install
    # hooks, native installers, embedded UI, workers and compile settings.
    command(
        operations,
        [bun, "run", "packages/opencode/script/build.ts", "--single"],
        cwd=ROOT / "source",
    )


def public_seed(operations) -> None:
    manifest, series, models = declarations()
    identity(manifest)
    before = pins()
    plugin_entries = plugin_input(manifest)
    native = helper("produce_native_packages")
    plugin = helper("produce_default_plugin_artifact")
    transport = helper("produce_codex_artifacts")
    for name in (
        "archives",
        "bun-cache",
        "bun-install",
        "transpiler-cache",
    ):
        (ROOT / name).mkdir(mode=0o755)
    transport.fetch({**series["archive"], "maximum_bytes": 256 * 1024**2}, ROOT / "source.tgz")
    for record in manifest["archives"]:
        native.download(record, ROOT / "archives" / (record["name"] + ".tgz"))
    plugin.fetch_tool_input(manifest["bun"], ROOT / "bun.zip", bun=True)
    plugin.extract_bun(ROOT / "bun.zip", ROOT / "bun", manifest["bun"])
    materialize_models(models)
    prepare(series)
    original = transport.inventory(ROOT / "source")
    lock = lock_identity(operations)
    build_commands(operations)
    source_identity(original, lock, operations)
    if before != pins() or plugin_input(manifest) != plugin_entries:
        raise ValueError("OpenCode producer inputs changed during public seed")
    write_json(
        ROOT / "seed.json",
        {
            "status": "NONFINAL",
            "recipe": before,
            "source": original,
            "lock": lock,
            "plugin": plugin_entries,
            "inputs": {
                name: transport.inventory(ROOT / name)
                for name in (
                    "archives",
                    "models",
                    "bun",
                    "bun-cache",
                    "bun-install",
                    "transpiler-cache",
                )
                if any((ROOT / name).iterdir())
            },
            "source_archive_sha256": digest(ROOT / "source.tgz"),
            "bun_archive_sha256": digest(ROOT / "bun.zip"),
        },
    )


def assemble(manifest: dict, series: dict, seed: dict, operations) -> None:
    artifacts = helper("runtime_artifacts")
    native = helper("produce_native_packages")
    if OUTPUT.exists() or OUTPUT.is_symlink():
        raise ValueError("new complete OpenCode output required")
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="opencode-", dir=OUTPUT.parent) as temporary:
        stage = Path(temporary) / "output"
        component = Path(temporary) / "package"
        stage.mkdir(mode=0o755)
        stage.chmod(0o755)
        for record in manifest["archives"]:
            destination = (
                component
                if record["name"] == "opencode-ai"
                else component / "node_modules/opencode-linux-x64"
            )
            native.extract(record, ROOT / "archives" / (record["name"] + ".tgz"), destination)
        original = artifacts.inventory(component)
        package = json.loads((component / "package.json").read_bytes())
        if package["bin"] != {"opencode": "./bin/opencode.exe"}:
            raise ValueError("original OpenCode launcher differs")
        built = ROOT / "source/packages/opencode/dist/opencode-linux-x64/bin/opencode"
        if built.is_symlink() or not built.is_file() or not built.stat().st_mode & 0o111:
            raise ValueError("complete stock OpenCode binary is missing")
        (component / BIN).write_bytes(built.read_bytes())
        (component / BIN).chmod(0o755)
        # Keep the original published postinstall, all optional declarations and
        # native assets; execute it against its real complete target package.
        command(operations, [str(NODE / "bin/node"), "postinstall.mjs"], cwd=component)
        current = {item["path"]: item for item in artifacts.inventory(component)}
        if any(
            current.get(item["path"]) != item
            for item in original
            if item["path"] not in {BIN, "bin/opencode.exe"}
        ):
            raise ValueError("original installed OpenCode payload changed")
        if digest(component / "bin/opencode.exe") != digest(built):
            raise ValueError("original OpenCode postinstall did not select compiled target")
        for name, source in (("source", ROOT / "source"), ("default-plugin", PLUGIN)):
            entries = artifacts.inventory(source)
            artifacts._copy(source, component / name, entries)
            if artifacts.inventory(component / name, closed=True) != entries:
                raise ValueError("independent full OpenCode input copy differs")
        entries = artifacts.inventory(component)
        closed = stage / "components/opencode"
        artifacts._copy(component, closed, entries)
        if artifacts.inventory(closed, closed=True) != entries:
            raise ValueError("complete independent installed OpenCode copy differs")
        if plugin_input(manifest) != seed["plugin"] or pins() != seed["recipe"]:
            raise ValueError("OpenCode recipe or plugin changed during assembly")
        helper("produce_codex_artifacts").network_none()
        write_json(
            stage / "provenance.json",
            {
                "schema": "butlers.opencode-component/v1",
                "version": manifest["version"],
                "recipe": seed["recipe"],
                "source_tree_sha256": series["source_tree_sha256"],
                "source_archive_sha256": series["archive"]["sha256"],
                "source_lock": seed["lock"],
                "compiled_binary_sha256": digest(built),
                "default_plugin_seed_sha256": digest(PLUGIN / "seed.json"),
                "tree_sha256": artifacts.tree_digest(entries),
                "scope": (
                    "Complete original stock target, embedded UI/model/logo assets, installed "
                    "workspace and default-plugin graph; native/auth/tool and final image "
                    "conformance separate."
                ),
            },
        )
        stage.rename(OUTPUT)


def build(operations) -> None:
    transport = helper("produce_codex_artifacts")
    transport.network_none()
    manifest, series, models = declarations()
    identity(manifest)
    seed = json.loads((ROOT / "seed.json").read_bytes())
    if (
        seed["status"] != "NONFINAL"
        or seed["recipe"] != pins()
        or seed["plugin"] != plugin_input(manifest)
        or seed["source_archive_sha256"] != digest(ROOT / "source.tgz")
        or seed["bun_archive_sha256"] != digest(ROOT / "bun.zip")
        or any(
            transport.inventory(ROOT / name) != entries for name, entries in seed["inputs"].items()
        )
    ):
        raise ValueError("closed OpenCode public preparation differs")
    models_identity(models)
    shutil.rmtree(ROOT / "source")
    prepare(series)
    if transport.inventory(ROOT / "source") != seed["source"]:
        raise ValueError("fresh complete OpenCode source differs")
    build_commands(operations)
    source_identity(seed["source"], seed["lock"], operations)
    assemble(manifest, series, seed, operations)
    print(
        "COMPLETE: full OpenCode workspace, UI and installed plugin graph built offline", flush=True
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("public-seed", "build"), required=True)
    args = parser.parse_args()
    plugin = helper("produce_default_plugin_artifact")
    try:
        with plugin.owner_signals():
            operations = plugin.Operations()
            if args.phase == "public-seed":
                public_seed(operations)
            else:
                build(operations)
    except plugin.OwnerSignal as error:
        # Preserve the ordinary owner signal status after every command joins.
        import signal

        signal.signal(error.number, signal.SIG_DFL)
        os.kill(os.getpid(), error.number)
        raise SystemExit(128 + error.number) from None


if __name__ == "__main__":
    main()
