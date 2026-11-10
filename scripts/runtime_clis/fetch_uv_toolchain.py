#!/usr/bin/env python3
"""Acquire the exact public uv wheel as data, without installation or execution."""

from __future__ import annotations

import argparse
import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import tempfile
import time
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

PINNED = {
    "version": "0.12.22",
    "url": "https://files.pythonhosted.org/packages/7a/b0/4cceeef80da0e8627bf3afb628930d5ea8e857f9c2aacbcda8598a5b82aa/uv-0.12.22-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
    "filename": "uv-0.12.22-py3-none-manylinux_2_17_x86_64.manylinux2014_x86_64.whl",
    "sha256": "680769ef701922dd93426b73476073411941f51ac46991a6e5c5a6efbf68a713",
    "platform": "linux-x86_64-glibc",
    "binary_sha256": "96e1603cb62aebb1a804fe9866a5708ee8bba4c39d07202ad29cf256a67366c3",
    "metadata_url": "https://pypi.org/pypi/uv/0.12.22/json",
    "metadata_sha256": "04855831ebff2b6338e2e48f194b8c4d00691107cfcdb63479a46363a9bbae5e",
}
BINARY = "uv-0.12.22.data/scripts/uv"
EXECUTABLES = {BINARY, "uv-0.12.22.data/scripts/uvx"}
FILES = {
    "uv/__init__.py",
    "uv/__main__.py",
    "uv/_find_uv.py",
    "uv/py.typed",
    *EXECUTABLES,
    "uv-0.12.22.dist-info/METADATA",
    "uv-0.12.22.dist-info/WHEEL",
    "uv-0.12.22.dist-info/licenses/LICENSE-APACHE",
    "uv-0.12.22.dist-info/licenses/LICENSE-MIT",
    "uv-0.12.22.dist-info/sboms/uv.cyclonedx.json",
    "uv-0.12.22.dist-info/RECORD",
}
MAX_ARCHIVE = 64 * 1024**2
MAX_EXPANDED = 128 * 1024**2
MAX_ENTRIES = 128
READ_TIMEOUT = 15
TRANSFER_TIMEOUT = 180


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical_digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def read_manifest(path: Path) -> dict:
    if path.is_symlink() or not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("toolchain manifest must be a regular file")
    value = json.loads(path.read_text())["uv"]
    if value != PINNED or set(value) != set(PINNED):
        raise ValueError("uv manifest differs from the exact reviewed official identity")
    url = urllib.parse.urlsplit(value["url"])
    if (
        url.scheme != "https"
        or url.hostname != "files.pythonhosted.org"
        or url.port is not None
        or url.username
        or url.password
        or url.query
        or url.fragment
        or PurePosixPath(url.path).name != value["filename"]
    ):
        raise ValueError("unsupported uv public URL")
    return value


class NoRedirect(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        # Intercept every 3xx before urllib's redirect/error machinery can act.
        if 300 <= response.status < 400:
            response.close()
            raise ValueError("uv artifact redirects are forbidden")
        return super().http_response(request, response)

    https_response = http_response


def download(record: dict, target: Path, opener=None) -> None:
    public = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    started = time.monotonic()
    with public.open(record["url"], timeout=READ_TIMEOUT) as response:
        if response.status != 200 or response.geturl() != record["url"]:
            raise ValueError("uv response identity or status mismatch")
        header = response.headers.get("Content-Length")
        if header is not None and (
            not re.fullmatch(r"[0-9]+", header) or not 0 < int(header) <= MAX_ARCHIVE
        ):
            raise ValueError("uv archive length exceeds its bound")
        total = 0
        with target.open("xb") as output:
            while block := response.read(min(1024**2, MAX_ARCHIVE - total + 1)):
                total += len(block)
                if total > MAX_ARCHIVE or time.monotonic() - started > TRANSFER_TIMEOUT:
                    raise ValueError("uv archive transfer bound exceeded")
                output.write(block)
        if not total or (header is not None and total != int(header)):
            raise ValueError("uv archive length mismatch")
    if digest(target) != record["sha256"]:
        raise ValueError("uv archive hash mismatch")
    target.chmod(0o644)


def relative(name: str) -> str:
    path = PurePosixPath(name)
    if (
        not name
        or path.is_absolute()
        or path.as_posix() != name
        or "\\" in name
        or any(part in (".", "..") for part in path.parts)
        or any(ord(character) < 32 or ord(character) == 127 for character in name)
    ):
        raise ValueError("unsafe uv ZIP path")
    return name


def plan_archive(archive: zipfile.ZipFile) -> list[tuple[zipfile.ZipInfo, str, int]]:
    entries = archive.infolist()
    if not entries or len(entries) > MAX_ENTRIES:
        raise ValueError("uv ZIP population bound exceeded")
    directories = {
        parent.as_posix()
        for name in FILES
        for parent in PurePosixPath(name).parents
        if parent != PurePosixPath(".")
    }
    seen, files, plan, expanded = set(), set(), [], 0
    for item in entries:
        if item.orig_filename != item.filename:
            raise ValueError("uv ZIP filename normalization is unsupported")
        name = relative(item.filename[:-1] if item.is_dir() else item.filename)
        if name in seen:
            raise ValueError("duplicate uv ZIP path")
        seen.add(name)
        mode = item.external_attr >> 16
        if (
            item.create_system != 3
            or item.flag_bits & 1
            or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
        ):
            raise ValueError("unsupported uv ZIP entry encoding")
        if item.is_dir():
            if name not in directories or mode != (stat.S_IFDIR | 0o755) or item.file_size:
                raise ValueError("unsupported uv ZIP directory")
            continue
        expected_mode = 0o755 if name in EXECUTABLES else 0o644
        if name not in FILES or mode != (stat.S_IFREG | expected_mode):
            raise ValueError("uv ZIP payload type, mode or population mismatch")
        expanded += item.file_size
        if item.file_size > MAX_ARCHIVE or expanded > MAX_EXPANDED:
            raise ValueError("uv ZIP expanded-size bound exceeded")
        files.add(name)
        plan.append((item, name, expected_mode))
    if files != FILES:
        raise ValueError("uv ZIP is missing required complete payload")
    return sorted(plan, key=lambda entry: entry[1])


def extract(archive_path: Path, wheel: Path, record: dict) -> list[dict]:
    # Keep this check here too: direct callers cannot bypass the download boundary.
    if archive_path.stat().st_size > MAX_ARCHIVE or digest(archive_path) != record["sha256"]:
        raise ValueError("uv archive hash or size mismatch before extraction")
    with zipfile.ZipFile(archive_path) as archive:
        plan = plan_archive(archive)
        wheel.mkdir(mode=0o755)
        wheel.chmod(0o755)
        payload, expanded = [], 0
        for item, name, mode in plan:
            target = wheel / name
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(item) as source, target.open("xb") as output:
                count = 0
                while block := source.read(1024**2):
                    count += len(block)
                    expanded += len(block)
                    if count > item.file_size or expanded > MAX_EXPANDED:
                        raise ValueError("uv payload expanded-size mismatch")
                    output.write(block)
                if count != item.file_size:
                    raise ValueError("uv payload size mismatch")
            target.chmod(mode)
            payload.append(
                {"path": name, "mode": f"{mode:04o}", "sha256": digest(target), "size": count}
            )
        for path in wheel.rglob("*"):
            if path.is_dir():
                path.chmod(0o755)
        if digest(wheel / BINARY) != record["binary_sha256"]:
            raise ValueError("uv executable hash mismatch")
    return payload


def publish(stage: Path, output: Path) -> None:
    # Linux is the fixed target. RENAME_NOREPLACE keeps a concurrent output intact.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(-100, os.fsencode(stage), -100, os.fsencode(output), 1):
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            raise ValueError("uv output already exists")
        raise OSError(code, "atomic uv publication failed")


def prepare(manifest: Path, output: Path, *, opener=None) -> dict:
    record = read_manifest(manifest)
    output = output.absolute()
    if output.resolve() != output or output.exists() or output.is_symlink():
        raise ValueError("uv output must be an absent real path")
    if not output.parent.is_dir():
        raise ValueError("uv output parent must already exist")
    with tempfile.TemporaryDirectory(prefix=".uv-toolchain-", dir=output.parent) as temporary:
        root = Path(temporary)
        root.chmod(0o755)
        stage = root / "result"
        stage.mkdir(mode=0o755)
        stage.chmod(0o755)
        wheel_archive = stage / record["filename"]
        download(record, wheel_archive, opener)
        payload = extract(wheel_archive, stage / "wheel", record)
        provenance = {
            "schema": "butlers.uv-toolchain/v1",
            "manifest_uv": record,
            "manifest_uv_sha256": canonical_digest(record),
            "archive": {"filename": record["filename"], "sha256": digest(wheel_archive)},
            "binary": {"path": BINARY, "sha256": digest(stage / "wheel" / BINARY)},
            "payload": payload,
        }
        path = stage / "provenance.json"
        path.write_text(json.dumps(provenance, sort_keys=True, indent=2) + "\n")
        path.chmod(0o644)
        publish(stage, output)
    return provenance


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        value = prepare(args.manifest, args.output)
    except (ValueError, OSError, KeyError, zipfile.BadZipFile) as error:
        parser.exit(78, f"uv toolchain acquisition refused ({type(error).__name__})\n")
    print(json.dumps({"schema": value["schema"], "payload_files": len(value["payload"])}))


if __name__ == "__main__":
    main()
