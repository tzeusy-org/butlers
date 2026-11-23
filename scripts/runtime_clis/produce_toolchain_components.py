#!/usr/bin/env python3
"""Produce complete Node/uv runtime components as data, then verify without writes."""

from __future__ import annotations

import argparse
import hashlib
import json
import posixpath
import shutil
import stat
import tarfile
import tempfile
import time
import types
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

NODE = {
    "version": "v22.23.4",
    "url": "https://nodejs.org/dist/v22.23.4/node-v22.23.4-linux-x64.tar.xz",
    "sha256": "1805bcd35e6815559bd2eb97db438d3fdb0eb3a5e6a977b7c1badca76337e9ce",
    "platform": "linux-x64",
    "checksums_sha256": "c90c3f1478fda8308b22cc1a1b0dc536169196444e67ee8b5c582fd351d02bce",
    "binary_sha256": "94efd062ed066c1a0b332e18249859c905e920eeada5df55d24c99b919629a90",
    "version_smoke": "v22.23.4",
}
NODE_ROOT = "node-v22.23.4-linux-x64"
NODE_TOP = {"CHANGELOG.md", "LICENSE", "README.md", "bin", "include", "lib", "share"}
MAX_ARCHIVE = 128 * 1024**2
MAX_EXPANDED = 1024**3
MAX_ENTRIES = 100000


def helper(name: str) -> types.ModuleType:
    if name not in {"fetch_uv_toolchain", "runtime_artifacts"}:
        raise ValueError("undeclared toolchain helper")
    source = Path(__file__).absolute().with_name(name + ".py")
    if source.is_symlink() or not source.is_file():
        raise ValueError("toolchain helper must be a regular declared sibling")
    module = types.ModuleType("_toolchain_components_" + name)
    module.__file__ = str(source)
    exec(compile(source.read_bytes(), str(source), "exec", dont_inherit=True), module.__dict__)
    return module


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def regular(path: Path) -> bytes:
    if path.resolve() != path.absolute() or not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("toolchain metadata must be a real regular path")
    return path.read_bytes()


def declarations(toolchains: Path, inputs: Path) -> tuple[dict, dict]:
    raw = regular(toolchains)
    tools = json.loads(raw)
    uv = helper("fetch_uv_toolchain")
    uv.read_manifest(toolchains)
    runtime = json.loads(regular(inputs))
    artifacts = helper("runtime_artifacts")
    versions = {name: runtime["versions"][name] for name in ("node", "npm", "uv")}
    if (
        tools["node"] != NODE
        or tools["npm"]["version"] != "10.9.9"
        or runtime["target"] != artifacts.TARGET
        or versions != {name: artifacts.VERSIONS[name] for name in versions}
    ):
        raise ValueError("toolchain target or exact identity differs")
    expected = {name: runtime["components"][name] for name in ("node", "uv")}
    for item in expected.values():
        value = item["tree_sha256"]
        if (
            set(item) != {"tree_sha256"}
            or not isinstance(value, str)
            or len(value) != 64
            or any(c not in "0123456789abcdef" for c in value)
        ):
            raise ValueError("invalid expected toolchain tree")
    return tools, {
        "schema": "butlers.toolchain-components/v1",
        "target": runtime["target"],
        "versions": versions,
        "toolchains_sha256": hashlib.sha256(raw).hexdigest(),
        "components": expected,
    }


class NoRedirect(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        if 300 <= response.status < 400:
            response.close()
            raise ValueError("Node archive redirects are forbidden")
        return super().http_response(request, response)

    https_response = http_response


def download(record: dict, output: Path, *, opener=None) -> None:
    url = urllib.parse.urlsplit(record["url"])
    if (
        url.scheme != "https"
        or url.netloc != "nodejs.org"
        or url.query
        or url.fragment
        or record != NODE
    ):
        raise ValueError("unreviewed Node download identity")
    public = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    started = time.monotonic()
    count = 0
    with public.open(record["url"], timeout=15) as response, output.open("xb") as target:
        if response.status != 200 or response.geturl() != record["url"]:
            raise ValueError("unexpected Node archive response")
        while data := response.read(1024**2):
            count += len(data)
            if count > MAX_ARCHIVE or time.monotonic() - started > 180:
                raise ValueError("Node archive transfer exceeds its bound")
            target.write(data)
    output.chmod(0o644)
    if not count or digest(output) != record["sha256"]:
        raise ValueError("Node archive bytes differ")


def safe_name(value: str) -> None:
    if (
        not value
        or PurePosixPath(value).as_posix() != value
        or value.startswith("/")
        or any(part in {".", "..", ""} for part in value.split("/"))
        or "\\" in value
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
    ):
        raise ValueError("unsafe Node archive path")


def extract_node(archive: Path, output: Path) -> None:
    """Preflight the entire graph before writing; never follow an archive parent link."""
    with tarfile.open(archive, "r:xz") as source:
        entries = []
        expanded = 0
        for item in source:
            entries.append(item)
            expanded += item.size
            if len(entries) > MAX_ENTRIES or item.size < 0 or expanded > MAX_EXPANDED:
                raise ValueError("Node archive population exceeds its bound")
        if not entries:
            raise ValueError("Node archive population is empty")
        members = {}
        directories = {""}
        for item in entries:
            name = item.name.rstrip("/")
            safe_name(name)
            if name != NODE_ROOT and not name.startswith(NODE_ROOT + "/"):
                raise ValueError("Node archive root differs")
            relative = name[len(NODE_ROOT) :].lstrip("/")
            if (
                relative in members
                or item.mode & 0o7000
                or not (item.isdir() or item.isfile() or item.issym() or item.islnk())
            ):
                raise ValueError("Node archive duplicate/type/mode differs")
            if not relative and not item.isdir():
                raise ValueError("Node archive root must be a directory")
            members[relative] = item
            for parent in PurePosixPath(relative).parents:
                directories.add("" if parent == PurePosixPath(".") else parent.as_posix())
            if item.isdir():
                directories.add(relative)
        if "" not in members or any(
            path in members and not members[path].isdir() for path in directories
        ):
            raise ValueError("Node archive parent aliases another entry")

        # Symlinks are materialized only after real directories/files. Complete
        # runtime inventory then resolves every link, including intermediate
        # aliases, before any output name can be published.
        def hard_target(name: str, seen: frozenset[str] = frozenset()) -> tarfile.TarInfo:
            if name in seen:
                raise ValueError("cyclic Node hardlink")
            item = members.get(name)
            if item is None:
                raise ValueError("dangling Node hardlink")
            if item.isfile():
                return item
            if not item.islnk():
                raise ValueError("Node hardlink target must be regular")
            safe_name(item.linkname)
            if not item.linkname.startswith(NODE_ROOT + "/"):
                raise ValueError("Node hardlink leaves archive root")
            resolved = hard_target(item.linkname[len(NODE_ROOT) + 1 :], seen | {name})
            if bool(item.mode & 0o111) != bool(resolved.mode & 0o111):
                raise ValueError("ambiguous Node hardlink mode")
            return resolved

        for name, item in members.items():
            if item.issym():
                link = item.linkname
                if (
                    not link
                    or link.startswith("/")
                    or "\\" in link
                    or any(ord(c) < 32 or ord(c) == 127 for c in link)
                ):
                    raise ValueError("unsafe Node symlink")
                normalized = posixpath.normpath(posixpath.join(posixpath.dirname(name), link))
                if normalized == ".." or normalized.startswith("../"):
                    raise ValueError("Node symlink leaves archive root")
            elif item.islnk():
                hard_target(name)
        materialized = sum(
            hard_target(name).size
            for name, item in members.items()
            if item.isfile() or item.islnk()
        )
        if materialized > MAX_EXPANDED:
            raise ValueError("independent Node payload expansion exceeds its bound")
        if output.exists() or output.is_symlink():
            raise ValueError("Node component output already exists")
        with tempfile.TemporaryDirectory(prefix=".node-component-", dir=output.parent) as temporary:
            stage = Path(temporary) / "tree"
            stage.mkdir(mode=0o755)
            stage.chmod(0o755)
            for name in sorted(directories - {""}, key=lambda value: (value.count("/"), value)):
                (stage / name).mkdir(mode=0o755)
                (stage / name).chmod(0o755)
            for name, item in members.items():
                if item.isdir() or item.issym():
                    continue
                original = hard_target(name)
                incoming = source.extractfile(original)
                if incoming is None:
                    raise ValueError("Node regular payload is missing")
                path = stage / name
                with incoming, path.open("xb") as target:
                    shutil.copyfileobj(incoming, target, length=1024**2)
                if path.stat().st_size != original.size:
                    raise ValueError("Node payload size differs")
                path.chmod(0o755 if original.mode & 0o111 else 0o644)
            for name, item in members.items():
                if item.issym():
                    (stage / name).symlink_to(item.linkname)
            if {p.name for p in stage.iterdir()} != NODE_TOP:
                raise ValueError("complete Node top-level payload differs")
            helper("runtime_artifacts").inventory(stage, closed=True)
            helper("fetch_uv_toolchain").publish(stage, output)


def uv_identity(root: Path, tools: Path) -> None:
    uv = helper("fetch_uv_toolchain")
    record = uv.read_manifest(tools)
    if {p.name for p in root.iterdir()} != {record["filename"], "wheel", "provenance.json"}:
        raise ValueError("complete uv input population differs")
    archive = root / record["filename"]
    if digest(archive) != record["sha256"]:
        raise ValueError("uv whole archive differs")
    payload = []
    with zipfile.ZipFile(archive) as source:
        # Reuse the unchanged helper's validated, globally sorted payload plan.
        # ZIP member order is not the order of its canonical output metadata.
        for item, name, mode in uv.plan_archive(source):
            path = root / "wheel" / name
            with source.open(item) as stream:
                checksum = hashlib.sha256()
                while chunk := stream.read(1024**2):
                    checksum.update(chunk)
                expected = checksum.hexdigest()
            if digest(path) != expected or path.stat().st_size != item.file_size:
                raise ValueError("uv extracted payload differs")
            payload.append(
                {
                    "path": item.filename,
                    "mode": f"{mode:04o}",
                    "sha256": expected,
                    "size": item.file_size,
                }
            )
    expected = {
        "schema": "butlers.uv-toolchain/v1",
        "manifest_uv": record,
        "manifest_uv_sha256": uv.canonical_digest(record),
        "archive": {"filename": record["filename"], "sha256": digest(archive)},
        "binary": {"path": uv.BINARY, "sha256": digest(root / "wheel" / uv.BINARY)},
        "payload": payload,
    }
    if (
        regular(root / "provenance.json")
        != (json.dumps(expected, sort_keys=True, indent=2) + "\n").encode()
    ):
        raise ValueError("uv complete input provenance differs")
    actual_files = {
        p.relative_to(root / "wheel").as_posix()
        for p in (root / "wheel").rglob("*")
        if not p.is_dir()
    }
    if actual_files != uv.FILES:
        raise ValueError("uv extracted population differs")


def context(root: Path, toolchains: Path, inputs: Path) -> dict:
    tools, expected = declarations(toolchains, inputs)
    runtime = helper("runtime_artifacts")
    for relative in (
        "",
        "components",
        "components/node",
        "components/uv",
        "inputs",
        "inputs/node",
        "inputs/uv",
        "inputs/uv/wheel",
    ):
        path = root / relative
        if path.is_symlink() or not path.is_dir():
            raise ValueError("toolchain context roots must be real directories")
    if root.is_symlink() or {p.name for p in root.iterdir()} - {"provenance.json"} != {
        "components",
        "inputs",
    }:
        raise ValueError("toolchain context population differs")
    if {p.name for p in (root / "components").iterdir()} != {"node", "uv"} or {
        p.name for p in (root / "inputs").iterdir()
    } != {"node", "uv"}:
        raise ValueError("toolchain context component/input population differs")
    node_archive = root / "inputs/node" / PurePosixPath(tools["node"]["url"]).name
    if {p.name for p in node_archive.parent.iterdir()} != {node_archive.name} or digest(
        node_archive
    ) != tools["node"]["sha256"]:
        raise ValueError("Node whole archive differs")
    uv_identity(root / "inputs/uv", toolchains)
    for name in ("node", "uv"):
        entries = runtime.inventory(root / "components" / name, closed=True)
        if runtime.tree_digest(entries) != expected["components"][name]["tree_sha256"]:
            raise ValueError("toolchain component differs from reviewed complete tree")
    if (
        digest(root / "components/node/bin/node") != tools["node"]["binary_sha256"]
        or digest(root / "components/uv/uv") != tools["uv"]["binary_sha256"]
    ):
        raise ValueError("toolchain executable data identity differs")
    uv = helper("fetch_uv_toolchain")
    for name in ("uv", "uvx"):
        if digest(root / "components/uv" / name) != digest(
            root / "inputs/uv/wheel" / str(PurePosixPath(uv.BINARY).with_name(name))
        ):
            raise ValueError("uv independent runtime copy differs")
    entries = [
        item for item in runtime.inventory(root, closed=True) if item["path"] != "provenance.json"
    ]
    identities = set()
    for item in entries:
        if item["type"] == "file":
            metadata = (root / item["path"]).stat()
            identity = (metadata.st_dev, metadata.st_ino)
            if metadata.st_nlink != 1 or identity in identities:
                raise ValueError("toolchain regular payload aliases another input")
            identities.add(identity)
    return {
        **expected,
        "source_archives": {"node": tools["node"]["sha256"], "uv": tools["uv"]["sha256"]},
        "inventory": entries,
    }


def verify(toolchains: Path, inputs: Path, output: Path) -> dict:
    expected = context(output, toolchains, inputs)
    provenance = output / "provenance.json"
    if (
        stat.S_IMODE(provenance.lstat().st_mode) != 0o644
        or provenance.stat().st_nlink != 1
        or regular(provenance) != (json.dumps(expected, sort_keys=True, indent=2) + "\n").encode()
    ):
        raise ValueError("toolchain closed provenance differs")
    return expected


def produce(
    toolchains: Path, inputs: Path, output: Path, *, node_opener=None, uv_opener=None
) -> dict:
    source = {str(path): regular(path) for path in (toolchains, inputs)}
    tools, _expected = declarations(toolchains, inputs)
    output = output.absolute()
    if (
        output.resolve() != output
        or output.exists()
        or output.is_symlink()
        or not output.parent.is_dir()
    ):
        raise ValueError("toolchain output must be an absent real path")
    uv = helper("fetch_uv_toolchain")
    with tempfile.TemporaryDirectory(
        prefix=".toolchain-components-", dir=output.parent
    ) as temporary:
        stage = Path(temporary) / "context"
        for relative in ("", "components", "inputs", "inputs/node"):
            path = stage / relative
            path.mkdir(mode=0o755)
            path.chmod(0o755)
        archive = stage / "inputs/node" / PurePosixPath(tools["node"]["url"]).name
        download(tools["node"], archive, opener=node_opener)
        extract_node(archive, stage / "components/node")
        uv.prepare(toolchains, stage / "inputs/uv", opener=uv_opener)
        target = stage / "components/uv"
        target.mkdir(mode=0o755)
        target.chmod(0o755)
        for name in ("uv", "uvx"):
            shutil.copyfile(
                stage / "inputs/uv/wheel" / str(PurePosixPath(uv.BINARY).with_name(name)),
                target / name,
            )
            (target / name).chmod(0o755)
        provenance = context(stage, toolchains, inputs)
        path = stage / "provenance.json"
        path.write_text(json.dumps(provenance, sort_keys=True, indent=2) + "\n")
        path.chmod(0o644)
        verify(toolchains, inputs, stage)
        if any(regular(Path(path)) != data for path, data in source.items()):
            raise ValueError("declared toolchain inputs changed during produce")
        uv.publish(stage, output)
    return provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("produce", "verify"), required=True)
    parser.add_argument("--toolchains", type=Path, required=True)
    parser.add_argument("--runtime-inputs", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = (
        produce(args.toolchains, args.runtime_inputs, args.output)
        if args.phase == "produce"
        else verify(args.toolchains, args.runtime_inputs, args.output)
    )
    print(json.dumps({"schema": result["schema"], "components": result["components"]}))


if __name__ == "__main__":
    main()
