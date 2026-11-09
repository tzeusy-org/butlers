#!/usr/bin/env python3
"""Seed the complete locked Codex graph, then build it in a networkless layer.

The fixed builder paths are part of the artifact identity. Public seeding is
nonfinal; only build publishes an executable and content-only provenance.
Run this entrypoint inside Dockerfile.codex-artifacts, never an ambient checkout.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tempfile
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path("/opt/build")
RECIPE = Path("/recipe")
CMAKE = Path("/opt/cmake")
PUBLIC_HOSTS = {"codeload.github.com", "files.pythonhosted.org"}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, sort_keys=True, indent=2) + "\n")
    path.chmod(0o644)


class RefuseRedirects(urllib.request.HTTPErrorProcessor):
    def http_response(self, request, response):
        if 300 <= response.status < 400:
            response.close()
            raise ValueError("build input redirects are forbidden")
        return super().http_response(request, response)

    https_response = http_response


def fetch(item: dict, destination: Path) -> None:
    url = urllib.parse.urlsplit(item["url"])
    if (
        url.scheme != "https"
        or url.netloc not in PUBLIC_HOSTS
        or url.query
        or url.fragment
        or not url.path.startswith("/")
        or destination.exists()
        or destination.is_symlink()
    ):
        raise ValueError("invalid public build input")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), RefuseRedirects())
    # Publish only a complete hash-matching archive. Failed transfers do not
    # leave a filename that a subsequent extraction could mistake for an input.
    with tempfile.TemporaryDirectory(prefix="codex-input-", dir=destination.parent) as temporary:
        staged = Path(temporary) / "archive"
        size = 0
        with opener.open(item["url"], timeout=60) as response, staged.open("wb") as stream:
            while chunk := response.read(1024 * 1024):
                size += len(chunk)
                if size > item["maximum_bytes"]:
                    raise ValueError("public build input exceeds its bound")
                stream.write(chunk)
        if (
            digest(staged) != item["sha256"]
            or ("size" in item and size != item["size"])
            or not size
        ):
            raise ValueError("public build input bytes differ")
        staged.chmod(0o644)
        staged.rename(destination)


def extract_cmake(archive: Path, output: Path) -> None:
    if output.exists() or output.is_symlink():
        raise ValueError("CMake output already exists")
    with zipfile.ZipFile(archive) as source:
        entries = source.infolist()
        names = [entry.filename.rstrip("/") for entry in entries]
        if not entries or len(names) != len(set(names)) or len(entries) > 20000:
            raise ValueError("invalid CMake archive population")
        if sum(entry.file_size for entry in entries) > 512 * 1024 * 1024:
            raise ValueError("CMake expanded archive exceeds its bound")
        for entry, name in zip(entries, names, strict=True):
            path = PurePosixPath(name)
            mode = entry.external_attr >> 16
            if (
                not name
                or path.is_absolute()
                or path.as_posix() != name
                or any(part in {".", ".."} for part in path.parts)
                or "\\" in name
                or any(ord(character) < 32 for character in name)
                or mode & 0o7000
                or stat.S_IFMT(mode) not in {0, stat.S_IFREG, stat.S_IFDIR}
                or (stat.S_IFMT(mode) == stat.S_IFDIR) != entry.is_dir()
            ):
                raise ValueError("unsafe CMake archive entry")
        with tempfile.TemporaryDirectory(prefix="cmake-", dir=output.parent) as temporary:
            stage = Path(temporary) / "tree"
            stage.mkdir(mode=0o755)
            for entry, name in zip(entries, names, strict=True):
                path = stage / name
                if entry.is_dir():
                    path.mkdir(parents=True, exist_ok=True)
                else:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with source.open(entry) as incoming, path.open("xb") as outgoing:
                        shutil.copyfileobj(incoming, outgoing)
                    path.chmod(0o755 if entry.external_attr >> 16 & 0o111 else 0o644)
            for path in stage.rglob("*"):
                if path.is_dir():
                    path.chmod(0o755)
            stage.rename(output)


def inventory(root: Path) -> list[dict]:
    result = []
    for path in sorted(root.rglob("*")):
        mode = stat.S_IMODE(path.lstat().st_mode)
        item = {"path": path.relative_to(root).as_posix(), "mode": mode}
        if path.is_symlink():
            item.update(kind="link", target=os.readlink(path))
        elif path.is_file():
            item.update(kind="file", sha256=digest(path))
        elif path.is_dir():
            item.update(kind="directory")
        else:
            raise ValueError("unexpected build input entry")
        result.append(item)
    return result


def network_none() -> None:
    interfaces = [
        line.split(":")[0].strip() for line in Path("/proc/net/dev").read_text().splitlines()[2:]
    ]
    routes = Path("/proc/net/route").read_text().splitlines()[1:]
    ipv6 = Path("/proc/net/ipv6_route").read_text().splitlines()
    if interfaces != ["lo"] or routes or any(line.split()[-1] != "lo" for line in ipv6):
        raise ValueError("actual networkless builder layer is required")


def inputs() -> tuple[dict, dict]:
    if platform.python_version() != "3.12.13" or platform.libc_ver() != ("glibc", "2.41"):
        raise ValueError("pinned Python builder runtime is required")
    if platform.machine() != "x86_64" or Path.cwd() != ROOT:
        raise ValueError("fixed Linux/amd64 build path is required")
    home = Path(os.environ.get("HOME", "/missing-builder-home"))
    if (
        not home.is_absolute()
        or home.is_symlink()
        or not home.is_dir()
        or home.stat().st_uid != os.getuid()
        or any(home.iterdir())
    ):
        raise ValueError("builder requires its empty owned home backing")
    manifest = json.loads((RECIPE / "deploy/runtime-clis/codex/build-inputs.json").read_text())
    series = json.loads((RECIPE / "deploy/runtime-clis/codex/series.json").read_text())
    if manifest["schema"] != "butlers.codex-build-inputs/v1":
        raise ValueError("unsupported Codex builder inputs")
    if manifest["source"]["sha256"] != series["archive"]["sha256"]:
        raise ValueError("Codex source identity differs from its patch series")
    compiler = json.loads((RECIPE / "deploy/runtime-clis/codex/compiler-inputs.json").read_text())
    if digest(RECIPE / compiler["predecessor"]["path"]) != compiler["predecessor"]["sha256"]:
        raise ValueError("compiler supplement predecessor differs")
    if digest(Path("/var/lib/dpkg/status")) != compiler["output_installed_dpkg_status_sha256"]:
        raise ValueError("installed compiler package inventory differs")
    for key, value in {
        "SOURCE_DATE_EPOCH": manifest["identity"]["source_date_epoch"],
        "ZERO_AR_DATE": manifest["identity"]["zero_ar_date"],
        "RUSTFLAGS": manifest["identity"]["rustflags"],
        "CFLAGS": manifest["identity"]["cflags"],
        "CXXFLAGS": manifest["identity"]["cxxflags"],
        "STABLE_GIT_COMMIT": series["commit"],
        "CARGO_HOME": "/opt/build/cargo-home",
        "CARGO_TARGET_DIR": "/opt/build/target",
    }.items():
        if os.environ.get(key) != value:
            raise ValueError(f"fixed build identity differs: {key}")
    return manifest, series


def environment(*, offline: bool) -> dict[str, str]:
    # The Docker recipe supplies the validated identity values. Pass only these
    # and the builder-owned home, never ambient credentials, proxies, wrappers,
    # Cargo registries or dynamic loader configuration to compiler subprocesses.
    keys = (
        "HOME",
        "SOURCE_DATE_EPOCH",
        "ZERO_AR_DATE",
        "RUSTFLAGS",
        "CFLAGS",
        "CXXFLAGS",
        "STABLE_GIT_COMMIT",
        "CARGO_HOME",
        "CARGO_TARGET_DIR",
    )
    return {
        **{key: os.environ[key] for key in keys},
        "PATH": "/opt/rust/bin:/opt/cmake/cmake/data/bin:/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "CARGO_BUILD_JOBS": "2",
        "CARGO_INCREMENTAL": "0",
        "CARGO_NET_GIT_FETCH_WITH_CLI": "false",
        "CARGO_NET_OFFLINE": str(offline).lower(),
        "CARGO_HTTP_TIMEOUT": "60",
    }


def recipe_pins() -> dict[str, str]:
    return {
        name: digest(RECIPE / name)
        for name in (
            "Dockerfile.codex-artifacts",
            "scripts/runtime_clis/produce_codex_artifacts.py",
            "scripts/runtime_clis/prepare_vendor.py",
            "scripts/runtime_clis/install_system_artifacts.py",
            "deploy/runtime-clis/builders.json",
            "deploy/runtime-clis/debian-system-inputs.json",
            "deploy/runtime-clis/codex/build-inputs.json",
            "deploy/runtime-clis/codex/compiler-inputs.json",
            "deploy/runtime-clis/codex/series.json",
        )
    }


def public_seed() -> None:
    manifest, _series = inputs()
    (ROOT / "inputs").mkdir()
    for name in ("source", "cmake"):
        fetch(manifest[name], ROOT / "inputs" / name)
    subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            str(RECIPE / "scripts/runtime_clis/prepare_vendor.py"),
            "--series",
            str(RECIPE / "deploy/runtime-clis/codex/series.json"),
            "--archive",
            str(ROOT / "inputs/source"),
            "--output",
            str(ROOT / "source"),
            "--receipt",
            str(ROOT / "prepared.json"),
        ],
        check=True,
        env=environment(offline=False),
        stdin=subprocess.DEVNULL,
    )
    extract_cmake(ROOT / "inputs/cmake", CMAKE)
    (ROOT / "cargo-home").mkdir()
    before = inventory(ROOT / "source")
    with (ROOT / "vendor-config.toml").open("wb") as config:
        subprocess.run(
            ["cargo", "vendor", "--locked", "--versioned-dirs", str(ROOT / "vendor")],
            cwd=ROOT / "source/codex-rs",
            stdout=config,
            check=True,
            env=environment(offline=False),
            stdin=subprocess.DEVNULL,
        )
    (ROOT / "vendor-config.toml").rename(ROOT / "cargo-home/config.toml")
    if inventory(ROOT / "source") != before:
        raise ValueError("locked source changed during public acquisition")
    write_json(
        ROOT / "seed.json",
        {
            "status": "NONFINAL",
            "source": before,
            "vendor": inventory(ROOT / "vendor"),
            "cmake": inventory(CMAKE),
            "config_sha256": digest(ROOT / "cargo-home/config.toml"),
            "build_inputs_sha256": digest(RECIPE / "deploy/runtime-clis/codex/build-inputs.json"),
            "series_sha256": digest(RECIPE / "deploy/runtime-clis/codex/series.json"),
            "recipe": recipe_pins(),
        },
    )


def build() -> None:
    network_none()
    manifest, series = inputs()
    seed = json.loads((ROOT / "seed.json").read_text())
    if (
        seed["status"] != "NONFINAL"
        or inventory(ROOT / "source") != seed["source"]
        or inventory(ROOT / "vendor") != seed["vendor"]
        or inventory(CMAKE) != seed["cmake"]
        or digest(ROOT / "cargo-home/config.toml") != seed["config_sha256"]
        or digest(RECIPE / "deploy/runtime-clis/codex/build-inputs.json")
        != seed["build_inputs_sha256"]
        or digest(RECIPE / "deploy/runtime-clis/codex/series.json") != seed["series_sha256"]
        or recipe_pins() != seed["recipe"]
    ):
        raise ValueError("complete seeded build inputs changed")
    argv = [
        "cargo",
        "build",
        "--locked",
        "--offline",
        "--release",
        "--manifest-path",
        "codex-rs/Cargo.toml",
        "-p",
        "codex-cli",
        "--bin",
        "codex",
        "--target",
        "x86_64-unknown-linux-gnu",
        "-j2",
    ]
    subprocess.run(
        argv,
        cwd=ROOT / "source",
        check=True,
        env=environment(offline=True),
        stdin=subprocess.DEVNULL,
    )
    network_none()
    if inventory(ROOT / "source") != seed["source"]:
        raise ValueError("source changed during compilation")
    output = Path("/artifacts/codex")
    if output.exists() or output.is_symlink():
        raise ValueError("artifact output already exists")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="codex-", dir=output.parent) as temporary:
        stage = Path(temporary) / "output"
        stage.mkdir(mode=0o755)
        shutil.copyfile(ROOT / "target/x86_64-unknown-linux-gnu/release/codex", stage / "codex")
        (stage / "codex").chmod(0o755)
        write_json(
            stage / "provenance.json",
            {
                "schema": "butlers.codex-artifact/v1",
                "series_sha256": seed["series_sha256"],
                "build_inputs_sha256": seed["build_inputs_sha256"],
                "source_tree_sha256": series["source_tree_sha256"],
                "recipe": seed["recipe"],
                "seed_sha256": digest(ROOT / "seed.json"),
                "identity": manifest["identity"],
                "binary_sha256": digest(stage / "codex"),
                "binary_bytes": (stage / "codex").stat().st_size,
                "target": "x86_64-unknown-linux-gnu",
                "default_features": True,
                "release_profile": "unchanged upstream release profile, debug and strip settings",
            },
        )
        stage.rename(output)
    print("COMPLETE: offline Codex artifact published", flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase", choices=("public-seed", "build"), required=True)
    arguments = parser.parse_args()
    if arguments.phase == "public-seed":
        public_seed()
    else:
        build()


if __name__ == "__main__":
    main()
