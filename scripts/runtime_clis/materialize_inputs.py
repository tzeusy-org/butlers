#!/usr/bin/env python3
"""Copy explicitly supplied, hash-verified build artifacts into prepared vendor source.

This performs no downloads, dependency resolution or builds. Source series and
artifact inputs remain separately attested; neither can silently replace the other.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def destination(root: Path, relative: str) -> Path:
    path = Path(relative)
    if path.is_absolute() or not path.parts or any(part == ".." for part in path.parts):
        raise ValueError("invalid artifact destination")
    output = root / path
    if any(parent.is_symlink() for parent in [output, *output.parents] if parent != root):
        raise ValueError("artifact destination contains a symlink")
    if not output.resolve().is_relative_to(root.resolve()):
        raise ValueError("artifact destination escapes source")
    return output


def materialize(manifest: Path, root: Path, inputs: dict[str, Path]) -> dict:
    data = json.loads(manifest.read_text())
    if data["schema"] != "butlers.vendor-build-inputs/v1":
        raise ValueError("unsupported build-input schema")
    if root.is_symlink() or not root.is_dir():
        raise ValueError("prepared source must be a real directory")
    entries = data["artifacts"]
    if not entries or len({item["name"] for item in entries}) != len(entries):
        raise ValueError("empty or duplicate artifact names")
    if set(inputs) != {item["name"] for item in entries}:
        raise ValueError("supply exactly the declared artifact inputs")
    outputs = [destination(root, item["path"]) for item in entries]
    if len(set(outputs)) != len(outputs):
        raise ValueError("duplicate artifact destinations")
    for item, output in zip(entries, outputs, strict=True):
        source = inputs[item["name"]]
        if source.is_symlink() or not source.is_file() or digest(source) != item["sha256"]:
            raise ValueError(f"artifact input mismatch: {item['name']}")
        if item["mode"] not in [0o644, 0o755]:
            raise ValueError("invalid artifact mode")
        if output.exists() and (
            not output.is_file()
            or digest(output) != item["sha256"]
            or output.stat().st_mode & 0o777 != item["mode"]
        ):
            raise ValueError("existing artifact differs")
    staged: list[tuple[Path, Path]] = []
    try:
        for item, output in zip(entries, outputs, strict=True):
            if output.exists():
                continue
            output.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=output.parent, delete=False) as stream:
                temporary = Path(stream.name)
            staged.append((temporary, output))
            shutil.copyfile(inputs[item["name"]], temporary)
            if digest(temporary) != item["sha256"]:
                raise ValueError("artifact changed during copy")
            temporary.chmod(item["mode"])
        for temporary, output in staged:
            os.replace(temporary, output)
    finally:
        for temporary, _ in staged:
            temporary.unlink(missing_ok=True)
    return {"manifest_sha256": digest(manifest), "artifacts": entries, "build_performed": False}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--input", action="append", default=[], metavar="NAME=PATH")
    parser.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args()
    inputs = {}
    for value in args.input:
        name, separator, path = value.partition("=")
        if not separator or name in inputs:
            parser.error("each input must be a unique NAME=PATH")
        inputs[name] = Path(path)
    result = materialize(args.manifest, args.root, inputs)
    args.receipt.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
