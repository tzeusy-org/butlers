#!/usr/bin/env python3
"""Prepare public Python inputs, then certify four cold profiles without a route.

Only offline-verify publishes contexts. snapshot and public-seed are preparation;
none of these phases is evidence of an image build or application conformance.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import tomllib
import urllib.parse
import urllib.request
import zipfile
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

HELPERS = tuple(
    f"scripts/runtime_clis/{name}.py"
    for name in ("close_python_artifacts", "python_environment", "python_entrypoint")
)
TARGET = {"python": "3.12.13", "platform": "linux-amd64", "libc": "glibc2.41"}
BACKEND_DIGEST = "d5371c1e76f4dda1bbc68f96b0e21f2db7fa83b65073b24249c62ff4a478619e"
PROFILES = (
    ("default", "production"),
    ("default", "development"),
    ("live-listener", "production"),
    ("live-listener", "development"),
)
PUBLIC_HOSTS = {"files.pythonhosted.org", "download.pytorch.org", "download-r2.pytorch.org"}
SOURCE_SCHEMA = "butlers.python-source/v1"
SEED_SCHEMA = "butlers.python-seed/v1"


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()
    ).hexdigest()


def safe_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not isinstance(value, str)
        or not value
        or path.is_absolute()
        or path.as_posix() != value
        or any(p in (".", "..") for p in path.parts)
        or "\\" in value
        or "\x00" in value
    ):
        raise ValueError("unsafe relative input path")
    return value


def regular(path: Path) -> None:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("input must be a regular file")


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    path.chmod(0o644)


def record(root: Path, relative: str) -> dict:
    path = root / safe_path(relative)
    # lstat every component: a regular leaf must not hide a directory symlink.
    current = root
    for part in PurePosixPath(relative).parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("source links are unsupported")
    regular(path)
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o7000:
        raise ValueError("special source mode")
    return {"path": relative, "mode": mode, "sha256": digest(path)}


def packaging_paths(project: Path, tracked: set[str]) -> list[str]:
    record(project, "pyproject.toml")
    config = tomllib.loads((project / "pyproject.toml").read_text())
    if config.get("build-system") != {
        "requires": ["hatchling"],
        "build-backend": "hatchling.build",
    }:
        raise ValueError("unsupported build backend or backend-path")
    if config.get("tool", {}).get("hatch") or config["project"].get("dynamic"):
        raise ValueError("unsupported packaging hooks or dynamic metadata")
    paths = {"pyproject.toml", "uv.lock", *HELPERS}
    paths.update(p for p in tracked if p.startswith("src/butlers/"))
    for field in ("readme", "license"):
        value = config["project"].get(field)
        name = (
            value
            if isinstance(value, str) and field == "readme"
            else (value.get("file") if isinstance(value, dict) else None)
        )
        if name:
            paths.add(safe_path(name))
    patterns = config["project"].get("license-files", [])
    for pattern in patterns:
        safe_path(pattern)
        paths.update(p for p in tracked if PurePosixPath(p).match(pattern))
    forbidden = {".git", ".env", ".venv", "__pycache__", ".mypy_cache", ".ruff_cache"}
    if any(forbidden.intersection(PurePosixPath(name).parts) for name in paths):
        raise ValueError("packaging inputs include ambient or credential paths")
    if not paths <= tracked or "src/butlers/__init__.py" not in paths:
        raise ValueError("tracked packaging inputs are incomplete")
    return sorted(paths)


def git_output(project: Path, *args: str) -> bytes:
    return subprocess.run(
        ["git", "-C", str(project), *args], check=True, capture_output=True, timeout=15
    ).stdout


def source_manifest(project: Path) -> dict:
    if project.is_symlink() or not project.is_dir():
        raise ValueError("source must be a real directory")
    manifest = project / "source-inputs.json"
    if manifest.exists() or manifest.is_symlink():
        regular(manifest)
        value = json.loads(manifest.read_text())
        if (
            set(value) != {"schema", "identity", "inputs"}
            or value["schema"] != SOURCE_SCHEMA
            or set(value["identity"]) != {"head", "content"}
            or not re.fullmatch(r"[0-9a-f]{40}", value["identity"]["head"])
            or not isinstance(value["inputs"], list)
        ):
            raise ValueError("invalid source declaration")
        names = []
        for item in value["inputs"]:
            if (
                set(item) != {"path", "mode", "sha256"}
                or not isinstance(item["mode"], int)
                or item != record(project, item["path"])
            ):
                raise ValueError("source input drift")
            names.append(item["path"])
        if (
            names != sorted(set(names))
            or canonical(value["inputs"]) != value["identity"]["content"]
        ):
            raise ValueError("source inventory identity mismatch")
        actual = set()
        directories = set()
        for path in project.rglob("*"):
            if path.is_symlink():
                raise ValueError("source contains a link")
            relative = path.relative_to(project).as_posix()
            if path.is_dir():
                directories.add(relative)
            else:
                regular(path)
                actual.add(relative)
        expected_directories = {
            str(parent)
            for name in names
            for parent in PurePosixPath(name).parents
            if str(parent) != "."
        }
        if actual != {*names, "source-inputs.json"} or directories != expected_directories:
            raise ValueError("source population mismatch")
        if packaging_paths(project, set(names)) != names:
            raise ValueError("unsupported snapshot population")
        return value
    top = Path(git_output(project, "rev-parse", "--show-toplevel").decode().strip())
    if top.resolve() != project.resolve():
        raise ValueError("project must be a checkout root")
    tracked = set(git_output(project, "ls-files", "-z").decode().split("\0")) - {""}
    inputs = [record(project, name) for name in packaging_paths(project, tracked)]
    return {
        "schema": SOURCE_SCHEMA,
        "identity": {
            "head": git_output(project, "rev-parse", "HEAD").decode().strip(),
            "content": canonical(inputs),
        },
        "inputs": inputs,
    }


def copy_source(project: Path, destination: Path, expected: dict) -> None:
    if source_manifest(project) != expected:
        raise ValueError("source changed before copy")
    destination.mkdir()
    for item in expected["inputs"]:
        target = destination / item["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(project / item["path"], target)
        target.chmod(item["mode"])
    write_json(destination / "source-inputs.json", expected)
    if source_manifest(destination) != expected or source_manifest(project) != expected:
        raise ValueError("source changed during copy")


def snapshot(project: Path, output: Path) -> dict:
    if (project / "source-inputs.json").exists():
        raise ValueError("snapshot requires a genuine checkout")
    expected = source_manifest(project)
    absent(output)
    with tempfile.TemporaryDirectory(prefix="python-source-", dir=output.parent) as temporary:
        stage = Path(temporary) / "source"
        copy_source(project, stage, expected)
        absent(output)
        stage.rename(output)
    return expected


def absent(path: Path) -> None:
    if path.exists() or path.is_symlink():
        raise ValueError("output already exists")
    if not path.parent.is_dir() or path.parent.is_symlink():
        raise ValueError("output parent must be an existing real directory")


def backend_lock(path: Path) -> dict:
    regular(path)
    value = tomllib.loads(path.read_text())
    if canonical(value) != BACKEND_DIGEST:
        raise ValueError("backend lock differs from the reviewed nine-wheel closure")
    return value


def public_artifact(item: dict) -> tuple[str, str, int | None]:
    url = urllib.parse.urlsplit(item["url"])
    name = urllib.parse.unquote(PurePosixPath(url.path).name)
    if (
        url.scheme != "https"
        or url.hostname not in PUBLIC_HOSTS
        or url.port not in (None, 443)
        or url.username
        or url.password
        or url.query
        or url.fragment
        or not name
        or "/" in name
        or "\\" in name
        or name in (".", "..")
        or not re.fullmatch(r"[0-9a-f]{64}", item["hashes"]["sha256"])
    ):
        raise ValueError("unsupported public artifact authority or identity")
    size = item.get("size")
    if size is not None and (type(size) is not int or not 0 < size <= 4 * 1024**3):
        raise ValueError("unsupported artifact size")
    return name, item["hashes"]["sha256"], size


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("artifact redirects are forbidden")


def download(item: dict, destination: Path) -> Path:
    name, expected_hash, expected_size = public_artifact(item)
    target = destination / name
    if target.exists():
        regular(target)
        if digest(target) != expected_hash:
            raise ValueError("conflicting artifact filename")
        if expected_size is not None and target.stat().st_size != expected_size:
            raise ValueError("artifact size mismatch")
        return target
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    started = time.monotonic()
    with tempfile.NamedTemporaryFile(dir=destination, delete=False) as temporary:
        temporary_path = Path(temporary.name)
        try:
            total = 0
            with opener.open(item["url"], timeout=60) as response:
                if response.status != 200 or response.geturl() != item["url"]:
                    raise ValueError("artifact response identity mismatch")
                while block := response.read(1024 * 1024):
                    total += len(block)
                    if total > (expected_size or 4 * 1024**3) or time.monotonic() - started > 600:
                        raise ValueError("artifact transfer bound exceeded")
                    temporary.write(block)
            temporary.flush()
            if (expected_size is not None and total != expected_size) or digest(
                temporary_path
            ) != expected_hash:
                raise ValueError("artifact hash or size mismatch")
            temporary_path.chmod(0o644)
            temporary_path.rename(target)
        finally:
            temporary_path.unlink(missing_ok=True)
    return target


def load_packaging(wheel: Path, artifact: dict):
    name, expected, size = public_artifact(artifact)
    regular(wheel)
    if (
        wheel.name != name
        or digest(wheel) != expected
        or wheel.stat().st_size != size
        or name != "packaging-26.3-py3-none-any.whl"
    ):
        raise ValueError("packaging ZIP identity mismatch")
    if any(key == "packaging" or key.startswith("packaging.") for key in sys.modules):
        raise ValueError("ambient packaging module already loaded")
    with zipfile.ZipFile(wheel) as archive:
        metadata = archive.read("packaging-26.3.dist-info/METADATA").decode()
        if "Name: packaging\n" not in metadata or "Version: 26.3\n" not in metadata:
            raise ValueError("packaging wheel metadata mismatch")
    sys.path.insert(0, str(wheel))
    try:
        import packaging.markers
        import packaging.tags
        import packaging.utils
        import packaging.version

        if not str(packaging.__file__).startswith(str(wheel) + "/"):
            raise ValueError("packaging did not load from the verified ZIP")
        return packaging
    finally:
        sys.path.pop(0)


def select_artifacts(path: Path, packaging) -> tuple[list[dict], dict[str, str]]:
    value = tomllib.loads(path.read_text())
    if (
        value.get("lock-version") != "1.0"
        or value.get("created-by") != "uv"
        or not isinstance(value.get("packages"), list)
    ):
        raise ValueError("unsupported pylock export")
    platforms = [f"manylinux_2_{v}_x86_64" for v in range(41, 4, -1)] + [
        "manylinux2014_x86_64",
        "manylinux2010_x86_64",
        "manylinux1_x86_64",
        "linux_x86_64",
    ]
    tags = list(packaging.tags.cpython_tags((3, 12), platforms=platforms))
    tags += list(packaging.tags.compatible_tags((3, 12), interpreter="cp312", platforms=platforms))
    rank = {tag: index for index, tag in reversed(list(enumerate(tags)))}
    environment = {
        "implementation_name": "cpython",
        "implementation_version": "3.12.13",
        "os_name": "posix",
        "platform_machine": "x86_64",
        "platform_release": "",
        "platform_system": "Linux",
        "platform_version": "",
        "python_full_version": "3.12.13",
        "platform_python_implementation": "CPython",
        "python_version": "3.12",
        "extra": "",
    }
    artifacts, inventory = [], {}
    for package in value["packages"]:
        if set(package) - {"name", "version", "marker", "index", "wheels", "sdist"}:
            raise ValueError("unsupported package source")
        if package.get("marker") and not packaging.markers.Marker(package["marker"]).evaluate(
            environment
        ):
            continue
        name = packaging.utils.canonicalize_name(package["name"])
        version = str(packaging.version.Version(package["version"]))
        if name in inventory:
            raise ValueError("duplicate target package")
        inventory[name] = version
        choices = []
        for wheel in package.get("wheels", []):
            filename, _, _ = public_artifact(wheel)
            wheel_name, wheel_version, _, wheel_tags = packaging.utils.parse_wheel_filename(
                filename
            )
            if wheel_name != name or str(wheel_version) != version:
                raise ValueError("wheel package identity mismatch")
            eligible = [rank[tag] for tag in wheel_tags if tag in rank]
            if eligible:
                choices.append((min(eligible), filename, wheel))
        if choices:
            artifacts.append(min(choices, key=lambda choice: choice[:2])[2])
        elif package.get("sdist"):
            sdist = package["sdist"]
            filename, _, _ = public_artifact(sdist)
            source_name, source_version = packaging.utils.parse_sdist_filename(filename)
            if source_name != name or str(source_version) != version:
                raise ValueError("sdist package identity mismatch")
            artifacts.append(sdist)
        else:
            raise ValueError("package has no target artifact")
    if not inventory:
        raise ValueError("empty target inventory")
    return artifacts, inventory


def material_identity(root: Path) -> dict:
    """Bind dereferenced link contents too; rglob alone skips directory links."""
    if root.is_symlink() or not root.is_dir():
        raise ValueError("material root must be a real directory")
    accumulator = hashlib.sha256()
    count = 0

    def visit(path: Path, relative: str, ancestors: tuple[Path, ...]) -> None:
        nonlocal count
        resolved = path.resolve(strict=True)
        if not resolved.is_relative_to(root.resolve()):
            raise ValueError("seed link leaves material root")
        mode = resolved.stat().st_mode
        link = os.readlink(path) if path.is_symlink() else None
        if stat.S_ISDIR(mode):
            if resolved in ancestors:
                raise ValueError("seed directory link cycle")
            entry = [relative, "directory", stat.S_IMODE(mode), link]
        elif stat.S_ISREG(mode):
            entry = [relative, stat.S_IMODE(mode), digest(resolved), link]
        else:
            raise ValueError("unsupported seed material")
        accumulator.update(json.dumps(entry, separators=(",", ":")).encode() + b"\n")
        count += 1
        if stat.S_ISDIR(mode):
            for child in sorted(resolved.iterdir()):
                visit(child, f"{relative}/{child.name}", (*ancestors, resolved))

    for path in sorted(root.iterdir()):
        visit(path, path.name, (root.resolve(),))
    return {"entries": count, "sha256": accumulator.hexdigest()}


def helper(project: Path, name: str):
    path = project / "scripts/runtime_clis" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_producer_{name}", path)
    if spec is None:
        raise ValueError("producer helper source is unavailable")
    module = importlib.util.module_from_spec(spec)
    # Execute the declared source directly: SourceFileLoader.exec_module can
    # create a pyc inside this strictly closed snapshot even for parent imports.
    exec(compile(path.read_bytes(), str(path), "exec", dont_inherit=True), module.__dict__)
    return module


def isolated_environment(home: str) -> dict[str, str]:
    return {
        "HOME": home,
        "PATH": "/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "UV_NO_CONFIG": "1",
        "UV_KEYRING_PROVIDER": "disabled",
        "UV_PYTHON_DOWNLOADS": "never",
        "UV_DEFAULT_INDEX": "https://pypi.org/simple",
        "SOURCE_DATE_EPOCH": "0",
    }


def check_network() -> None:
    interfaces = {
        line.split(":")[0].strip() for line in Path("/proc/net/dev").read_text().splitlines()[2:]
    }
    if interfaces != {"lo"}:
        raise ValueError("offline child has a non-loopback interface")
    for line in Path("/proc/net/route").read_text().splitlines()[1:]:
        fields = line.split()
        if fields[0] != "lo" or fields[2] != "00000000":
            raise ValueError("offline child has an external IPv4 route")
    for line in Path("/proc/net/ipv6_route").read_text().splitlines():
        fields = line.split()
        if fields[-1] != "lo" or fields[4] != "0" * 32:
            raise ValueError("offline child has an external IPv6 route")


def settle_group(pgid: int) -> dict:
    """Stop this freshly owned session, then account for its remaining identities."""
    try:
        os.killpg(pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + 5
    while True:
        members = []
        for entry in Path("/proc").iterdir():
            if not entry.name.isdigit():
                continue
            try:
                fields = (entry / "stat").read_text().rsplit(")", 1)[1].split()
            except (FileNotFoundError, ProcessLookupError):
                continue
            if int(fields[2]) == pgid:
                members.append(
                    {"pid": int(entry.name), "state": fields[0], "start_ticks": fields[19]}
                )
        running = [member for member in members if member["state"] != "Z"]
        if not members or time.monotonic() >= deadline:
            return {"running": running, "zombies": [m for m in members if m["state"] == "Z"]}
        time.sleep(0.02)


class _CommandTermination(BaseException):
    """Unwind the owner only after its process handle is available for cleanup."""


@contextmanager
def command_termination():
    previous = signal.getsignal(signal.SIGTERM)
    requested = False
    armed = False
    delivered = False

    def terminate(_signum, _frame):
        nonlocal requested, delivered
        requested = True
        if armed and not delivered:
            delivered = True
            raise _CommandTermination()

    def arm():
        nonlocal armed, delivered
        armed = True
        if requested and not delivered:
            delivered = True
            raise _CommandTermination()

    def disarm():
        nonlocal armed
        armed = False

    # Respect an explicitly inherited ignore policy. Otherwise defer TERM during
    # Popen and settlement; children inherit their ordinary unblocked signal mask.
    if previous != signal.SIG_IGN:
        signal.signal(signal.SIGTERM, terminate)
    try:
        try:
            yield arm, disarm
        except _CommandTermination:
            pass
    finally:
        signal.signal(signal.SIGTERM, previous)
    if requested:
        # The command was killed/waited and its group accounted for before the
        # original owner signal policy runs. Default TERM retains signal exit -15.
        os.kill(os.getpid(), signal.SIGTERM)
        raise InterruptedError("producer command cancelled by SIGTERM")


class Operations:
    """Real process/network boundary; tests replace operations, not orchestration."""

    def __init__(self, work: Path, uv: Path | None = None, *, nested: bool = False):
        self.work = work
        self.nested = nested
        if nested and os.getpgrp() != os.getpid():
            raise ValueError("offline child must run in its own supervised session")
        self.sequence = 0
        self.home = os.environ.get("HOME", "")
        home = Path(self.home)
        if (
            not self.home
            or not home.is_absolute()
            or home.is_symlink()
            or not home.is_dir()
            or home.stat().st_uid != os.getuid()
            or any(home.iterdir())
        ):
            raise ValueError("caller must supply an empty owned HOME backing")
        self.environment = isolated_environment(self.home)
        self.uv = (uv or Path(shutil.which("uv") or "/missing-uv")).resolve(strict=True)

    def run(self, argv: list[str], project: Path, *, environment=None, timeout=900) -> str:
        with command_termination() as (arm, disarm):
            self.sequence += 1
            log = self.work / f"command-{self.sequence:02d}.log"
            started = time.time()
            with log.open("wb") as stream:
                process = subprocess.Popen(
                    argv,
                    cwd=project,
                    env=environment or self.environment,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                    start_new_session=not self.nested,
                )
                timed_out = False
                try:
                    arm()
                    code = process.wait(timeout=timeout)
                except BaseException:
                    disarm()
                    timed_out = True
                    try:
                        if self.nested:
                            process.kill()
                        else:
                            os.killpg(process.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    process.wait(timeout=15)
                    raise
                finally:
                    disarm()
                    # Never leave a child-owned process group behind, even after parent success.
                    # Child uv/build processes share the outer supervised session.
                    # Cancellation of that owner therefore reaches the whole tree.
                    settlement = (
                        {"deferred_to_outer_owner": os.getpgrp()}
                        if self.nested
                        else settle_group(process.pid)
                    )
                    write_json(
                        log.with_suffix(".json"),
                        {
                            "argv": argv,
                            "pid": process.pid,
                            "exit": process.returncode,
                            "started": started,
                            "ended": time.time(),
                            "timed_out_or_cancelled": timed_out,
                            "parent_waited": process.returncode is not None,
                            "owned_group": os.getpgrp() if self.nested else process.pid,
                            "group_settlement": settlement,
                            "log_sha256": digest(log),
                        },
                    )
            if settlement.get("running") or settlement.get("zombies"):
                raise RuntimeError("owned command group did not fully settle; see receipt")
            if code:
                raise RuntimeError(f"producer command failed; see {log.name}")
            return log.read_text()

    def target(self) -> dict:
        if (
            platform.python_version() != "3.12.13"
            or platform.system() != "Linux"
            or platform.machine() != "x86_64"
            or platform.libc_ver() != ("glibc", "2.41")
        ):
            raise ValueError("producer target runtime mismatch")
        if self.run([str(self.uv), "--version"], self.work, timeout=15).strip() not in {
            "uv 0.12.22",
            "uv 0.12.22 (x86_64-unknown-linux-gnu)",
        }:
            raise ValueError("producer uv version mismatch")
        return {
            "target": TARGET,
            "python_sha256": digest(Path(sys.executable).resolve()),
            "uv_sha256": digest(self.uv),
        }

    def fetch(self, item: dict, wheelhouse: Path) -> Path:
        return download(item, wheelhouse)

    def packaging(self, wheel: Path, artifact: dict):
        return load_packaging(wheel, artifact)

    def select(self, export: Path, packaging):
        return select_artifacts(export, packaging)

    def offline_profile(self, request: dict) -> dict:
        request_path = self.work / f"{request['variant']}-{request['profile']}-request.json"
        write_json(request_path, request)
        # The actual child checks its own namespace before verifying/installing anything.
        program = (
            "import importlib.util,sys; p=sys.argv[1]; "
            "s=importlib.util.spec_from_file_location('_python_producer_child',p); "
            "m=importlib.util.module_from_spec(s); s.loader.exec_module(m); "
            "m.verify_child(m.Path(sys.argv[2]))"
        )
        self.run(
            [
                sys.executable,
                "-I",
                "-B",
                "-c",
                program,
                str(Path(__file__).resolve()),
                str(request_path),
            ],
            self.work,
            timeout=1800,
        )
        return json.loads(Path(request["receipt"]).read_text())


def sync_argv(
    operations: Operations,
    project: Path,
    cache: Path,
    wheelhouse: Path,
    profile: str,
    extras: list[str],
    *,
    offline: bool = True,
) -> list[str]:
    return [
        str(operations.uv),
        "sync",
        "--project",
        str(project),
        "--frozen",
        *(["--offline"] if offline else []),
        "--no-index",
        "--find-links",
        str(wheelhouse),
        "--no-python-downloads",
        "--python",
        sys.executable,
        "--cache-dir",
        str(cache),
        *(["--group", "dev"] if profile == "development" else ["--no-dev"]),
        *[part for extra in extras for part in ("--extra", extra)],
    ]


def normalized_versions(inventory: dict) -> dict[str, str]:
    return {
        re.sub(r"[-_.]+", "-", item["name"]).lower(): item["version"]
        for item in inventory["distributions"]
    }


def verify_child(request_path: Path) -> None:
    check_network()
    request = json.loads(request_path.read_text())
    base = request_path.parent / f"{request['variant']}-{request['profile']}"
    base.mkdir()
    operations = Operations(base, uv=Path(request["uv"]), nested=True)
    runtime = operations.target()
    if runtime != request["runtime"]:
        raise ValueError("offline child tool identity drift")
    project, context = Path(request["source"]), Path(request["context"])
    source = source_manifest(project)
    if source != request["source_manifest"]:
        raise ValueError("offline source drift")
    extras = [] if request["variant"] == "default" else ["live-listener"]
    helper(project, "python_entrypoint").verify_build(context, project, request["profile"], extras)
    fresh_project, cache, environment = base / "project", base / "cache", base / "environment"
    copy_source(project, fresh_project, source)
    shutil.copytree(context / "uv-cache", cache)
    env = {**operations.environment, "UV_PROJECT_ENVIRONMENT": str(environment)}
    operations.run(
        sync_argv(
            operations, fresh_project, cache, context / "wheelhouse", request["profile"], extras
        ),
        fresh_project,
        environment=env,
    )
    validator = helper(fresh_project, "python_environment")
    # These APIs inspect the actual installed interpreter/metadata, not uv's summary.
    seal = validator.seal(
        environment, fresh_project, ["dev"] if request["profile"] == "development" else [], extras
    )
    manifest = environment / ".butlers-environment.json"
    write_json(manifest, seal)
    validator.validate(manifest, environment, fresh_project)
    installed = normalized_versions(seal["environment"])
    expected = request["versions"]
    if installed != expected or source_manifest(fresh_project) != source:
        raise ValueError("installed lock inventory or source mismatch")
    write_json(
        Path(request["receipt"]),
        {
            "status": "PASS",
            "variant": request["variant"],
            "profile": request["profile"],
            "runtime": runtime,
            "versions": installed,
            "source": source["identity"],
            "network": "only-loopback/no-external-route",
        },
    )


def public_seed(project: Path, work: Path, output: Path, lock: Path, operations=None) -> dict:
    absent(output)
    source = source_manifest(project)
    backend = backend_lock(lock)
    work.mkdir()
    ops = operations or Operations(work)
    runtime = ops.target()
    initial_backend, initial_producer = digest(lock), digest(Path(__file__))
    seed = work / "seed"
    seed.mkdir()
    wheelhouse, cache = seed / "wheelhouse", seed / "uv-cache"
    wheelhouse.mkdir()
    cache.mkdir()
    frozen_source = work / "source"
    copy_source(project, frozen_source, source)
    backend_wheels = [wheel for package in backend["packages"] for wheel in package["wheels"]]
    packaging_artifact = next(
        wheel
        for wheel in backend_wheels
        if public_artifact(wheel)[0] == "packaging-26.3-py3-none-any.whl"
    )
    packaging_wheel = ops.fetch(packaging_artifact, wheelhouse)
    packaging = ops.packaging(packaging_wheel, packaging_artifact)
    for wheel in backend_wheels:
        ops.fetch(wheel, wheelhouse)
    constraints = seed / "build-constraints.txt"
    constraints.write_text("".join(f"{p['name']}=={p['version']}\n" for p in backend["packages"]))
    inventories = {}
    for variant, profile in PROFILES:
        export = work / f"pylock.{variant}-{profile}.toml"
        extras = [] if variant == "default" else ["live-listener"]
        ops.run(
            [
                str(ops.uv),
                "export",
                "--project",
                str(frozen_source),
                "--frozen",
                "--offline",
                "--no-python-downloads",
                "--cache-dir",
                str(cache),
                "--format",
                "pylock.toml",
                "--no-emit-project",
                "--output-file",
                str(export),
                *(["--group", "dev"] if profile == "development" else ["--no-dev"]),
                *[part for extra in extras for part in ("--extra", extra)],
            ],
            frozen_source,
        )
        artifacts, versions = ops.select(export, packaging)
        for artifact in artifacts:
            ops.fetch(artifact, wheelhouse)
        metadata = tomllib.loads((project / "pyproject.toml").read_text())["project"]
        inventories[f"{variant}/{profile}"] = {**versions, metadata["name"]: metadata["version"]}
    # Prime UV's URL-addressed cache as well as the verified wheelhouse. Direct
    # lock URLs cannot be satisfied by find-links alone before UV caches them.
    # Index lookup stays disabled; this public preparation is not an offline check.
    environment = {**ops.environment, "UV_PROJECT_ENVIRONMENT": str(work / "seed-environment")}
    ops.run(
        sync_argv(
            ops, frozen_source, cache, wheelhouse, "development", ["live-listener"], offline=False
        ),
        frozen_source,
        environment=environment,
    )
    if (
        source_manifest(project) != source
        or source_manifest(frozen_source) != source
        or digest(lock) != initial_backend
        or digest(Path(__file__)) != initial_producer
    ):
        raise ValueError("source or backend changed during seed")
    result = {
        "schema": SEED_SCHEMA,
        "status": "NONFINAL",
        "source": source,
        "backend_sha256": initial_backend,
        "producer_sha256": initial_producer,
        "runtime": runtime,
        "versions": inventories,
        "material": material_identity(seed),
    }
    write_json(work / "seed.json", result)
    return result


def offline_verify(project: Path, work: Path, output: Path, lock: Path, operations=None) -> dict:
    absent(output)
    source = source_manifest(project)
    backend_lock(lock)
    seed = json.loads((work / "seed.json").read_text())
    material = work / "seed"
    if (
        set(seed)
        != {
            "schema",
            "status",
            "source",
            "backend_sha256",
            "producer_sha256",
            "runtime",
            "versions",
            "material",
        }
        or seed["schema"] != SEED_SCHEMA
        or seed["status"] != "NONFINAL"
        or seed["source"] != source
        or seed["backend_sha256"] != digest(lock)
        or seed["producer_sha256"] != digest(Path(__file__))
        or seed["material"] != material_identity(material)
        or set(seed["versions"]) != {f"{v}/{p}" for v, p in PROFILES}
    ):
        raise ValueError("seed identity or material drift")
    verification = work / "verification"
    verification.mkdir()
    ops = operations or Operations(verification)
    runtime = ops.target()
    if runtime != seed["runtime"]:
        raise ValueError("seed/offline tool identity drift")
    assembler = helper(project, "close_python_artifacts")
    validator = helper(project, "python_entrypoint")
    with tempfile.TemporaryDirectory(prefix="python-final-", dir=output.parent) as temporary:
        stage = Path(temporary) / "contexts"
        stage.mkdir()
        receipts = []
        for variant in ("default", "live-listener"):
            extras = [] if variant == "default" else [variant]
            context = stage / variant
            assembler.close_context(
                project=project,
                wheelhouse=material / "wheelhouse",
                cache=material / "uv-cache",
                constraints=material / "build-constraints.txt",
                output=context,
                extras=extras,
            )
            for profile in ("production", "development"):
                validator.verify_build(context, project, profile, extras)
                request = {
                    "variant": variant,
                    "profile": profile,
                    "source": str(project),
                    "source_manifest": source,
                    "uv": str(ops.uv),
                    "runtime": runtime,
                    "context": str(context),
                    "versions": seed["versions"][f"{variant}/{profile}"],
                    "receipt": str(verification / f"{variant}-{profile}-receipt.json"),
                }
                receipt = ops.offline_profile(request)
                if (
                    receipt.get("status") != "PASS"
                    or receipt.get("variant") != variant
                    or receipt.get("profile") != profile
                ):
                    raise ValueError("missing positive profile receipt")
                receipts.append(receipt)
        if (
            source_manifest(project) != source
            or seed["material"] != material_identity(material)
            or seed["backend_sha256"] != digest(lock)
            or seed["producer_sha256"] != digest(Path(__file__))
        ):
            raise ValueError("inputs changed during verification")
        result = {
            "schema": "butlers.python-producer/v1",
            "status": "VERIFIED_FOUR_PROFILES",
            "source": source["identity"],
            "seed_sha256": digest(work / "seed.json"),
            "runtime": runtime,
            "checks": receipts,
        }
        write_json(stage / "verification.json", result)
        for directory in (stage, *[p for p in stage.rglob("*") if p.is_dir()]):
            directory.chmod(0o755)
        absent(output)
        stage.rename(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--phase", choices=("snapshot", "public-seed", "offline-verify"), required=True
    )
    for name in ("project", "work", "output", "backend-lock"):
        parser.add_argument(f"--{name}", type=Path, required=True)
    arguments = parser.parse_args()
    paths = [arguments.project.absolute(), arguments.work.absolute(), arguments.output.absolute()]
    if any(path.resolve() != path for path in paths):
        parser.error("project/work/output must not contain aliases or symlink ancestors")
    if any(
        a == b or a.is_relative_to(b) or b.is_relative_to(a)
        for index, a in enumerate(paths)
        for b in paths[index + 1 :]
    ):
        parser.error("project/work/output must be disjoint")
    project, work, output = paths
    if arguments.phase == "snapshot":
        result = snapshot(project, output)
        print(json.dumps({"status": "SOURCE_SNAPSHOT", "identity": result["identity"]}))
    else:
        operation = public_seed if arguments.phase == "public-seed" else offline_verify
        result = operation(project, work, output, arguments.backend_lock.absolute())
        print(json.dumps({"status": result["status"]}))


if __name__ == "__main__":
    main()
