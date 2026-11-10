#!/usr/bin/env python3
"""Apply an exact, finite vendor patch series to a verified public source archive.

No downloads, dependencies, credentials, toolchain installation, or builds occur here.
The output must not exist. Every touched input and output is hash-checked, and
patch rejects/context drift fail the operation rather than selecting a new source.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def checked_path(root: Path, relative: str) -> Path:
    path = root / relative
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError(f"unsafe series path: {relative}")
    return path


def tree_digest(root: Path) -> str:
    """Bind paths, modes, regular bytes and symlink targets, excluding no inputs."""
    result = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            kind, content = "link", path.readlink().as_posix()
        elif path.is_file():
            kind, content = "file", digest(path)
        elif path.is_dir():
            continue
        else:
            raise ValueError(f"unsupported source entry: {path.relative_to(root)}")
        entry = [path.relative_to(root).as_posix(), kind, path.lstat().st_mode & 0o777, content]
        result.update(json.dumps(entry, separators=(",", ":")).encode() + b"\n")
    return result.hexdigest()


def prepare(series_path: Path, archive: Path, destination: Path) -> dict:
    series = json.loads(series_path.read_text())
    if series["schema"] != "butlers.vendor-patch-series/v1":
        raise ValueError("unsupported patch-series schema")
    if destination.exists() or destination.is_symlink():
        raise ValueError("output already exists")
    archive_digest = digest(archive)
    if archive_digest != series["archive"]["sha256"]:
        raise ValueError("source archive digest mismatch")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="vendor-prepare-", dir=destination.parent) as temporary:
        extracted = Path(temporary)
        archive_root = checked_path(extracted, series["archive"]["root"])
        subdirectory = series["archive"].get("subdirectory")
        if subdirectory is not None:
            if (
                not subdirectory
                or Path(subdirectory).is_absolute()
                or ".." in Path(subdirectory).parts
            ):
                raise ValueError("unsafe source subdirectory")
            root = checked_path(archive_root, subdirectory)
            prefix = root.relative_to(extracted).as_posix() + "/"
        else:
            root = archive_root
            prefix = None
        with tarfile.open(archive, "r:gz") as source:
            # The archive digest binds the complete upstream monorepo. Extract
            # only the named complete package, retaining every package entry.
            def members():
                for member in source:
                    if prefix is None or member.name.startswith(prefix):
                        if ".." in Path(member.name).parts:
                            raise ValueError("unsafe archive member")
                        yield member

            source.extractall(extracted, members=members(), filter="data")
        if list(extracted.iterdir()) != [archive_root] or not root.is_dir():
            raise ValueError("archive root mismatch")
        for patch in series["patches"]:
            patch_path = checked_path(series_path.parent, patch["path"])
            if digest(patch_path) != patch["sha256"]:
                raise ValueError(f"patch digest mismatch: {patch['path']}")
            for item in patch["files"]:
                path = checked_path(root, item["path"])
                actual = digest(path) if path.exists() else None
                if actual != item["before_sha256"]:
                    raise ValueError(f"input context mismatch: {item['path']}")
            subprocess.run(
                ["patch", "--batch", "--fuzz=0", "-p1", "--input", str(patch_path.resolve())],
                cwd=root,
                check=True,
                stdout=subprocess.DEVNULL,
                # Bind newly added source modes independently of the caller's
                # shell. Existing archive modes are preserved by patch.
                umask=0o027,
            )
            for item in patch["files"]:
                if digest(checked_path(root, item["path"])) != item["after_sha256"]:
                    raise ValueError(f"patched output mismatch: {item['path']}")
        output_digest = tree_digest(root)
        if output_digest != series["source_tree_sha256"]:
            raise ValueError("complete patched source tree mismatch")
        shutil.move(root, destination)
    return {
        "schema": "butlers.vendor-prepared/v1",
        "vendor": series["vendor"],
        "version": series["version"],
        "commit": series["commit"],
        "series_sha256": digest(series_path),
        "archive_sha256": archive_digest,
        "source_tree_sha256": output_digest,
        "patches": len(series["patches"]),
        "build_performed": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--series", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    receipt = prepare(args.series.resolve(), args.archive.resolve(), args.output.absolute())
    args.receipt.write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, sort_keys=True))


if __name__ == "__main__":
    main()
