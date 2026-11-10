"""Close and install reviewed complete prebuilt runtimes without executing them.

No resolver, package manager, lifecycle hook, download, or replacement launcher runs.
All destinations are planned before writes; callers own an exclusive stable prefix.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path, PurePosixPath

DESTINATIONS = {
    "node": "usr/local",
    "uv": "usr/local/bin",
    "claude": "usr/local/lib/node_modules/@anthropic-ai/claude-code",
    "codex": "usr/local/lib/node_modules/@openai/codex",
    "gemini": "usr/local/lib/node_modules/@google/gemini-cli",
    "opencode": "usr/local/lib/node_modules/opencode-ai",
}
LAUNCHERS = {
    "claude": "../lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe",
    "codex": "../lib/node_modules/@openai/codex/bin/codex.js",
    "gemini": "../lib/node_modules/@google/gemini-cli/bundle/gemini.js",
    "opencode": "../lib/node_modules/opencode-ai/bin/opencode.exe",
}
ENTRYPOINTS = {
    "node": ("bin/node", "bin/npm", "bin/npx"),
    "uv": ("uv", "uvx"),
    "claude": ("bin/claude.exe",),
    "codex": ("bin/codex.js",),
    "gemini": ("bundle/gemini.js",),
    "opencode": ("bin/opencode.exe",),
}
VERSIONS = {
    "node": "v22.23.4",
    "npm": "10.9.9",
    "uv": "0.12.22",
    "claude": "2.1.309",
    "codex": "0.159.2",
    "gemini": "0.66.0",
    "opencode": "1.17.7",
}
TARGET = {"python": "3.12.13", "platform": "linux-amd64", "libc": "glibc2.41"}


def encoded(value: object) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _regular(path: Path):
    """Open regular bytes without following a changed leaf link."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError("runtime input is not a regular file")
        return os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise


def digest(path: Path) -> str:
    with _regular(path) as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _safe_path(value: str, *, link: bool = False) -> None:
    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or any(ord(c) < 32 or ord(c) == 127 for c in value)
        or (not link and any(p in {"", ".", ".."} for p in parts))
    ):
        raise ValueError("unsafe runtime relative path")


def _parents_real(path: Path) -> None:
    """Missing parents are allowed; every existing ancestor must be a real directory."""
    if ".." in path.parts:
        raise ValueError("unsafe runtime parent traversal")
    absolute = path.absolute()
    for parent in reversed((absolute, *absolute.parents)):
        try:
            mode = parent.lstat().st_mode
        except FileNotFoundError:
            continue
        if not stat.S_ISDIR(mode):
            raise ValueError("runtime parent is not a real directory")


def _directory(path: Path) -> None:
    _parents_real(path)
    if not path.is_dir():
        raise ValueError("runtime root must be a real directory")


def _mkdir(path: Path) -> None:
    """Change modes only on directories created by this operation."""
    _parents_real(path)
    missing = []
    cursor = path
    while not cursor.exists():
        missing.append(cursor)
        cursor = cursor.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o755)
        directory.chmod(0o755)  # Defeat inherited setgid and restrictive umasks.


def inventory(root: Path, *, closed: bool = False) -> list[dict]:
    """Canonical complete tree: directories0755; files0644 or0755; bounded relative links.

    Source access bits are normalized; set-id/sticky regular files are refused.
    A closed tree must already have exact canonical modes, not merely the same x bits.
    """
    _directory(root)
    if closed and stat.S_IMODE(root.stat().st_mode) != 0o755:
        raise ValueError("invalid runtime component root mode")
    result = []
    for path in sorted(root.rglob("*")):
        name = path.relative_to(root).as_posix()
        _safe_path(name)
        mode = path.lstat().st_mode
        permissions = stat.S_IMODE(mode)
        if stat.S_ISLNK(mode):
            target = os.readlink(path)
            _safe_path(target, link=True)
            try:
                resolved = path.resolve(strict=True)
                if not resolved.is_relative_to(root.absolute()):
                    raise ValueError("runtime link leaves its complete component")
            except (OSError, RuntimeError) as exc:
                raise ValueError("dangling or cyclic runtime link") from exc
            result.append({"path": name, "type": "link", "target": target})
        elif stat.S_ISREG(mode):
            canonical = 0o755 if mode & 0o111 else 0o644
            if permissions & 0o7000 or (closed and permissions != canonical):
                raise ValueError("invalid runtime regular file mode")
            result.append({"path": name, "type": "file", "mode": canonical, "sha256": digest(path)})
        elif stat.S_ISDIR(mode):
            if closed and permissions != 0o755:
                raise ValueError("invalid runtime directory mode")
            result.append({"path": name, "type": "directory", "mode": 0o755})
        else:
            raise ValueError("runtime component contains a special filesystem object")
    if not result:
        raise ValueError("empty runtime component")
    return result


def tree_digest(entries: list[dict]) -> str:
    return hashlib.sha256(encoded(entries)).hexdigest()


def _object(pairs: list[tuple]) -> dict:
    value = {}
    for key, child in pairs:
        if key in value:
            raise ValueError("duplicate runtime metadata key")
        value[key] = child
    return value


def _reject_constant(_: str) -> None:
    raise ValueError("nonfinite runtime JSON")


def _json(data: bytes) -> dict:
    value = json.loads(
        data,
        object_pairs_hook=_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(value, dict):
        raise ValueError("runtime metadata must be an object")
    return value


def _snapshot(path: Path, *, closed: bool = False) -> bytes:
    _parents_real(path.parent)
    with _regular(path) as stream:
        if closed and stat.S_IMODE(os.fstat(stream.fileno()).st_mode) != 0o644:
            raise ValueError("invalid runtime metadata mode")
        return stream.read()


def _manifest(data: bytes) -> dict:
    value = _json(data)
    if (
        set(value) != {"schema", "target", "versions", "components", "provenance"}
        or value["schema"] != "butlers.runtime-inputs/v1"
    ):
        raise ValueError("invalid runtime input manifest")
    if (
        value["target"] != TARGET
        or value["versions"] != VERSIONS
        or not isinstance(value["components"], dict)
        or set(value["components"]) != set(DESTINATIONS)
    ):
        raise ValueError("runtime input target/version/components differ")
    for component in value["components"].values():
        if (
            not isinstance(component, dict)
            or set(component) != {"tree_sha256"}
            or not isinstance(component["tree_sha256"], str)
            or len(component["tree_sha256"]) != 64
            or any(c not in "0123456789abcdef" for c in component["tree_sha256"])
        ):
            raise ValueError("invalid runtime component digest")
    if not isinstance(value["provenance"], dict) or not value["provenance"]:
        raise ValueError("missing reviewed runtime build provenance")
    return value


def manifest(path: Path) -> dict:
    return _manifest(_snapshot(path))


def _provenance(data: bytes, expected: dict) -> dict:
    return {
        "schema": "butlers.runtime-artifacts/v1",
        "inputs_sha256": hashlib.sha256(data).hexdigest(),
        "components": expected["components"],
    }


def _components(components: dict[str, Path], expected: dict, *, closed: bool = False) -> dict:
    if set(components) != set(DESTINATIONS):
        raise ValueError("all six complete runtime components are required")
    inventories = {}
    for name, source in components.items():
        entries = inventory(source, closed=closed)
        if tree_digest(entries) != expected["components"][name]["tree_sha256"]:
            raise ValueError("runtime component differs from reviewed complete tree")
        for entry in ENTRYPOINTS[name]:
            path = source / entry
            try:
                resolved = path.resolve(strict=True)
                if (
                    not resolved.is_relative_to(source.absolute())
                    or not resolved.is_file()
                    or not resolved.stat().st_mode & 0o111
                ):
                    raise ValueError("runtime original entrypoint is missing or not executable")
            except (OSError, RuntimeError) as exc:
                raise ValueError("runtime original entrypoint is missing or unsafe") from exc
        inventories[name] = entries
    return inventories


def _write(path: Path, data: bytes) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(data)
        os.fchmod(stream.fileno(), 0o644)


def _copy(source: Path, destination: Path, entries: list[dict]) -> None:
    _mkdir(destination)
    for entry in entries:
        path = destination / entry["path"]
        _mkdir(path.parent)
        if entry["type"] == "directory":
            _mkdir(path)
        elif entry["type"] == "link":
            path.symlink_to(entry["target"])
        else:
            with _regular(source / entry["path"]) as incoming:
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
                with os.fdopen(fd, "wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                    os.fchmod(outgoing.fileno(), entry["mode"])
            if digest(path) != entry["sha256"]:
                raise ValueError("runtime file changed during copy")


def close(inputs: Path, components: dict[str, Path], output: Path) -> None:
    data = _snapshot(inputs)
    expected = _manifest(data)
    _parents_real(output.parent)
    if output.exists() or output.is_symlink():
        raise ValueError("new runtime context output is required")
    inventories = _components(components, expected)
    absolute = output.absolute()
    if any(
        absolute.is_relative_to(p.absolute()) or p.absolute().is_relative_to(absolute)
        for p in (*components.values(), inputs)
    ):
        raise ValueError("runtime context output overlaps its inputs")
    # Validate the composed install layout now as well as at install time.
    _plan(inventories)
    _mkdir(output.parent)
    with tempfile.TemporaryDirectory(prefix=".runtime-context-", dir=output.parent) as temporary:
        stage = Path(temporary) / "context"
        _mkdir(stage / "components")
        for name, entries in inventories.items():
            _copy(components[name], stage / "components" / name, entries)
        if _snapshot(inputs) != data or _components(components, expected) != inventories:
            raise ValueError("reviewed runtime inputs changed during close")
        copied = {name: stage / "components" / name for name in DESTINATIONS}
        if _components(copied, expected, closed=True) != inventories:
            raise ValueError("runtime independent copy differs")
        _write(stage / "inputs.json", data)
        _write(stage / "provenance.json", encoded(_provenance(data, expected)))
        if _snapshot(inputs) != data:
            raise ValueError("reviewed manifest bytes changed during close")
        if output.exists() or output.is_symlink():
            raise ValueError("runtime context output appeared during close")
        stage.rename(output)


def _plan(inventories: dict) -> dict[str, dict]:
    plan = {}

    def add(path: str, entry: dict) -> None:
        _safe_path(path)
        if path in plan:
            if entry["type"] == plan[path]["type"] == "directory":
                return
            raise ValueError("runtime component or launcher output collision")
        parent = PurePosixPath(path).parent
        if parent != PurePosixPath("."):
            add(parent.as_posix(), {"type": "directory", "mode": 0o755})
        plan[path] = entry

    for name, destination in DESTINATIONS.items():
        add(destination, {"type": "directory", "mode": 0o755})
        for entry in inventories[name]:
            add(str(PurePosixPath(destination, entry["path"])), entry)
    for name, target in LAUNCHERS.items():
        add(f"usr/local/bin/{name}", {"type": "link", "target": target})
    return plan


def install(inputs: Path, artifacts: Path, root: Path) -> None:
    data = _snapshot(inputs)
    expected = _manifest(data)
    _directory(artifacts)
    _directory(root)
    if stat.S_IMODE(artifacts.stat().st_mode) != 0o755:
        raise ValueError("invalid runtime context root mode")
    if {p.name for p in artifacts.iterdir()} != {"inputs.json", "provenance.json", "components"}:
        raise ValueError("runtime context has unexpected inputs")
    if _snapshot(artifacts / "inputs.json", closed=True) != data:
        raise ValueError("runtime context belongs to another reviewed manifest")
    if _json(_snapshot(artifacts / "provenance.json", closed=True)) != _provenance(data, expected):
        raise ValueError("runtime context provenance differs")
    _directory(artifacts / "components")
    if stat.S_IMODE((artifacts / "components").stat().st_mode) != 0o755:
        raise ValueError("invalid runtime components directory mode")
    if {p.name for p in (artifacts / "components").iterdir()} != set(DESTINATIONS):
        raise ValueError("runtime context components differ")
    components = {name: artifacts / "components" / name for name in DESTINATIONS}
    inventories = _components(components, expected, closed=True)
    if root.absolute().is_relative_to(artifacts.absolute()) or artifacts.absolute().is_relative_to(
        root.absolute()
    ):
        # '/' is the explicit image-build root; its immutable input context is
        # outside installed usr/local. Check actual write paths below instead.
        for destination in DESTINATIONS.values():
            target = root / destination
            if artifacts.absolute().is_relative_to(
                target.absolute()
            ) or target.absolute().is_relative_to(artifacts.absolute()):
                raise ValueError("runtime install output overlaps its context")
    plan = _plan(inventories)
    for relative, entry in plan.items():
        path = root / relative
        _parents_real(path.parent)
        try:
            existing = path.lstat().st_mode
        except FileNotFoundError:
            parent = path.parent
            while not parent.exists():
                parent = parent.parent
            if not os.access(parent, os.W_OK | os.X_OK):
                raise ValueError("runtime output parent is not writable/searchable") from None
            continue
        if entry["type"] != "directory" or not stat.S_ISDIR(existing):
            raise ValueError("runtime install output already exists or is not a real directory")
    if _snapshot(inputs) != data:
        raise ValueError("reviewed runtime manifest changed during preflight")
    for name, entries in inventories.items():
        _copy(components[name], root / DESTINATIONS[name], entries)
    for name, target in LAUNCHERS.items():
        (root / "usr/local/bin" / name).symlink_to(target)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    closer = sub.add_parser("close")
    closer.add_argument("--manifest", type=Path, required=True)
    closer.add_argument("--output", type=Path, required=True)
    for name in DESTINATIONS:
        closer.add_argument("--" + name, type=Path, required=True)
    installer = sub.add_parser("install")
    installer.add_argument("--manifest", type=Path, required=True)
    installer.add_argument("--artifacts", type=Path, required=True)
    installer.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "close":
            close(args.manifest, {name: getattr(args, name) for name in DESTINATIONS}, args.output)
        else:
            install(args.manifest, args.artifacts, args.root)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"runtime artifacts refused: {exc}\n")


if __name__ == "__main__":
    main()
