"""Offline process and source wiring for the small check_pyright_baseline engine.

Only explicit ``prepare`` may restore an isolated trusted-public environment.
``check`` and ``update`` never install tools or fetch history.
"""

from __future__ import annotations

import argparse
import base64
import fcntl
import hashlib
import importlib.metadata
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import time
import tomllib
from pathlib import Path

import check_pyright_baseline as account

VERSION = "1.1.414"
NODE_VERSION = b"v24.22.0\n"
TIMEOUT = 300
OUTPUT_LIMIT = 32 * 1024 * 1024
INPUT_ABI = "native-root-imports-v3"
NATIVE_SUFFIXES = {".py", ".pyi", ".pyc", ".pyd", ".so", ".dylib"}
CONFIG = dict(
    typeCheckingMode="basic",
    include=["src", "roster"],
    exclude=[],
    pythonVersion="3.12",
    venvPath=".",
    venv=".venv",
    extraPaths=["src"],
)


class Refusal(Exception):
    """Fixed category only; never a child message or source-bearing exception."""


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True, timeout=20)
    if result.returncode or len(result.stdout) > OUTPUT_LIMIT:
        raise Refusal("history")
    return result.stdout


def digest(data):
    return hashlib.sha256(data).hexdigest()


def trusted_base(root):
    if os.environ.get("GITHUB_ACTIONS") == "true":
        try:
            event = json.loads(Path(os.environ["GITHUB_EVENT_PATH"]).read_bytes())
            name = os.environ["GITHUB_EVENT_NAME"]
            if git(root, "rev-parse", "HEAD").decode().strip() != os.environ["GITHUB_SHA"]:
                raise Refusal("event-checkout")
            if name == "pull_request":
                base = event["pull_request"]["base"]["sha"]
            elif name == "merge_group":
                base = event["merge_group"]["base_sha"]
            elif name == "push" and os.environ["GITHUB_REF"] == "refs/heads/main":
                base = event["before"]
            elif (
                name in {"schedule", "workflow_dispatch"}
                and os.environ["GITHUB_REF"] == "refs/heads/main"
            ):
                base = os.environ["GITHUB_SHA"]
            else:
                raise Refusal("event-history")
        except (KeyError, ValueError, OSError):
            raise Refusal("event-history") from None
    else:
        base = git(root, "rev-parse", "origin/main^{commit}").decode().strip()
    if len(base) != 40 or any(c not in "0123456789abcdef" for c in base):
        raise Refusal("history")
    git(root, "cat-file", "-e", base + "^{commit}")
    return base


def live_group(group):
    for item in Path("/proc").iterdir():
        if item.name.isdecimal():
            try:
                fields = (item / "stat").read_text().rsplit(") ", 1)[1].split()
                if int(fields[2]) == group and fields[0] not in {"Z", "X"}:
                    return True
            except (FileNotFoundError, ProcessLookupError):
                pass
    return False


def finish_group(process):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        if not live_group(process.pid):
            break
        os.killpg(process.pid, sig)
        deadline = time.monotonic() + 5
        while live_group(process.pid) and time.monotonic() < deadline:
            time.sleep(0.02)
    process.wait(timeout=1)
    if live_group(process.pid):
        raise Refusal("cleanup")


def capture(command, *, cwd, environment, timeout=TIMEOUT, limit=OUTPUT_LIMIT):
    """Regular-file capture avoids pipe-holder hangs; kill only this child's own group."""
    with tempfile.TemporaryFile() as stdout, tempfile.TemporaryFile() as stderr:
        try:
            process = subprocess.Popen(
                command,
                cwd=cwd,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=stdout,
                stderr=stderr,
                start_new_session=True,
            )
        except OSError:
            raise Refusal("tool-unavailable") from None
        deadline = time.monotonic() + timeout
        category = None
        while process.poll() is None:
            if time.monotonic() >= deadline:
                category = "timeout"
                break
            if max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size) > limit:
                category = "output-limit"
                break
            time.sleep(0.02)
        if live_group(process.pid):
            category = category or "unfinished-child"
        finish_group(process)
        if category:
            raise Refusal(category)
        if max(os.fstat(stdout.fileno()).st_size, os.fstat(stderr.fileno()).st_size) > limit:
            raise Refusal("output-limit")
        stdout.seek(0)
        return process.returncode, stdout.read()


def environment(node):
    env = {k: v for k, v in os.environ.items() if not k.startswith("PYRIGHT_")}
    for key in ("NODE_OPTIONS", "NODE_PATH", "PYTHONPATH", "PYTHONHOME"):
        env.pop(key, None)
    env.update(
        PYRIGHT_PYTHON_FORCE_VERSION=VERSION,
        PYRIGHT_PYTHON_USE_BUNDLED_PYRIGHT="1",
        PYRIGHT_PYTHON_GLOBAL_NODE="1",
        PYRIGHT_PYTHON_NODEJS_WHEEL="0",
        PYRIGHT_PYTHON_IGNORE_WARNINGS="1",
        PATH=str(node.parent) + os.pathsep + env.get("PATH", ""),
    )
    return env


def native_inputs(root):
    """Bind import roots, namespace membership, stubs and PEP561 markers.

    Include roots select checked files, not all inputs to import resolution.
    Pyright also searches the project root and its default ``typings`` tree.
    Visible directories are conservative namespace inputs, even when empty.
    Only dot-prefixed directories cannot name Python import components.
    Cache directories can contain importable modules and are bound as well.
    """
    values = {}
    pending = [root]
    while pending:
        directory = pending.pop()
        for path in directory.iterdir():
            if path.name.startswith("."):
                continue
            relative = path.relative_to(root).as_posix()
            source = relative.split("/", 1)[0] in {"src", "roster"}
            native = path.suffix in NATIVE_SUFFIXES or path.name == "py.typed"
            if path.is_symlink():
                if native or path.is_dir() or not path.suffix:
                    raise Refusal("source-link")
                if not source:
                    continue
                data, mode = os.readlink(path).encode(), "120000"
            elif path.is_dir():
                values[relative] = ("040000", None)
                pending.append(path)
                continue
            elif path.is_file() and (native or source):
                data = path.read_bytes()
                mode = "100755" if path.stat().st_mode & 0o111 else "100644"
            else:
                continue
            oid = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
            values[relative] = (mode, oid, digest(data))
    return values


def snapshot(root, *, include_environment=False):
    values = sorted(native_inputs(root).items())
    names = [root / "pyproject.toml", root / "uv.lock"]
    if include_environment:
        for path in (root / ".venv").rglob("*"):
            if path.is_symlink() and path.is_dir():
                try:
                    target = path.resolve().relative_to(root.resolve())
                except ValueError:
                    raise Refusal("source-link") from None
                if target.parts[:1] != (".venv",) and any(p.startswith(".") for p in target.parts):
                    raise Refusal("source-link")
                values.append((path.relative_to(root).as_posix(), os.readlink(path)))
            elif path.is_dir():
                values.append((path.relative_to(root).as_posix(), "040000"))
            elif path.is_file():
                names.append(path)
    for path in sorted(names):
        if path.is_symlink() and path.suffix in {".py", ".pyi"}:
            raise Refusal("source-link")
        if path.suffix == ".pth":
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith(("#", "import ", "import\t")):
                    continue
                target = (path.parent / line).resolve()
                if target.is_dir():
                    try:
                        relative = target.relative_to(root.resolve())
                    except ValueError:
                        raise Refusal("external-import-path") from None
                    if relative.parts[:1] != (".venv",) and any(
                        p.startswith(".") for p in relative.parts
                    ):
                        raise Refusal("external-import-path")
        values.append(
            (
                path.relative_to(root).as_posix(),
                digest(path.read_bytes()),
                "100755" if path.stat().st_mode & 0o111 else "100644",
            )
        )
    return digest(json.dumps(sorted(values, key=lambda item: item[0])).encode())


def bundle_identity(dist):
    records = []
    for entry in dist.files or ():
        if str(entry).startswith("pyright/"):
            if entry.hash is None or entry.hash.mode != "sha256":
                raise Refusal("checker-record")
            actual = hashlib.sha256(Path(dist.locate_file(entry)).read_bytes()).digest()
            if base64.urlsafe_b64encode(actual).decode().rstrip("=") != entry.hash.value:
                raise Refusal("checker-record")
            records.append((str(entry), actual.hex()))
    if not records:
        raise Refusal("checker-record")
    return digest(json.dumps(sorted(records)).encode())


def prerequisites(root):
    if root.joinpath(".venv").is_symlink() or not root.joinpath(".venv/bin/python").is_file():
        raise Refusal("own-environment")
    config = tomllib.loads(root.joinpath("pyproject.toml").read_text())
    locked = tomllib.loads(root.joinpath("uv.lock").read_text())
    if [item.get("version") for item in locked["package"] if item["name"] == "pyright"] != [
        VERSION
    ]:
        raise Refusal("checker-lock")
    code, source = capture(
        [
            str(root / ".venv/bin/python"),
            "-I",
            "-B",
            "-c",
            "import importlib.util;print(importlib.util.find_spec('butlers').origin)",
        ],
        cwd=root,
        environment=os.environ.copy(),
        timeout=10,
        limit=4096,
    )
    if code or source.decode().strip() != str(root / "src/butlers/__init__.py"):
        raise Refusal("own-source")
    if config.get("tool", {}).get("pyright") != CONFIG:
        raise Refusal("scope-config")
    metadata = list(root.joinpath(".venv/lib").glob("python*/site-packages/pyright-*.dist-info"))
    if len(metadata) != 1:
        raise Refusal("checker-version")
    dist = importlib.metadata.Distribution.at(metadata[0])
    if dist.version != VERSION:
        raise Refusal("checker-version")
    bundle = Path(dist.locate_file("pyright/dist"))
    if (
        not bundle.joinpath("index.js").is_file()
        or json.loads(bundle.joinpath("package.json").read_bytes()).get("version") != VERSION
    ):
        raise Refusal("checker-bundle")
    node_path = shutil.which("node")
    if not node_path:
        raise Refusal("node-unavailable")
    node = Path(node_path).resolve()
    env = environment(node)
    code, version = capture(
        [str(node), "--version"], cwd=root, environment=env, timeout=10, limit=4096
    )
    if code or version != NODE_VERSION:
        raise Refusal("node-version")
    identity = {
        "checker": VERSION,
        "bundle": bundle_identity(dist),
        "node": digest(node.read_bytes()),
        "config": digest(root.joinpath("pyproject.toml").read_bytes()),
        "lock": digest(root.joinpath("uv.lock").read_bytes()),
    }
    return env, identity


def location(root, base):
    key = digest(
        (
            base
            + INPUT_ABI
            + digest(root.joinpath("pyproject.toml").read_bytes())
            + digest(root.joinpath("uv.lock").read_bytes())
        ).encode()
    )
    return root / ".tmp/pyright-public" / key


def initial(root, base):
    exists = (
        subprocess.run(
            ["git", "-C", str(root), "cat-file", "-e", base + ":pyright-baseline.json"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=20,
        ).returncode
        == 0
    )
    if (
        not exists
        and git(root, "log", "-1", "--format=%H", base, "--", "pyright-baseline.json").strip()
    ):
        raise Refusal("deleted-public-baseline")
    return not exists


def initial_dependencies(root, base):
    public = tomllib.loads(git(root, "show", base + ":pyproject.toml").decode())
    candidate = tomllib.loads((root / "pyproject.toml").read_text())
    candidate["tool"].pop("pyright")
    candidate["dependency-groups"]["dev"].remove("pyright==" + VERSION)
    if candidate != public:
        raise Refusal("initial-dependency-context")
    public_lock = tomllib.loads(git(root, "show", base + ":uv.lock").decode())
    candidate_lock = tomllib.loads((root / "uv.lock").read_text())
    packages = candidate_lock["package"]
    package = next(item for item in packages if item["name"] == "pyright")
    wheels = package.get("wheels", [])
    expected = "sha256:2a6b4b3298c9eec174c5ed83bd338de6eee82df2992f3e1930e6199d381be36f"
    if package["version"] != VERSION or len(wheels) != 1 or wheels[0]["hash"] != expected:
        raise Refusal("checker-lock")
    candidate_lock["package"] = [
        item for item in packages if item["name"] not in {"pyright", "nodeenv"}
    ]
    project = next(item for item in candidate_lock["package"] if item["name"] == "butlers")
    project["dev-dependencies"]["dev"].remove({"name": "pyright"})
    project["metadata"]["requires-dev"]["dev"].remove(
        {"name": "pyright", "specifier": "==" + VERSION}
    )
    if candidate_lock != public_lock:
        raise Refusal("initial-dependency-context")


def prepare(root, base, env):
    if not initial(root, base):
        return
    initial_dependencies(root, base)
    target = location(root, base)
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent) as temporary:
        stage = Path(temporary)
        with tempfile.TemporaryFile() as stream:
            result = subprocess.run(
                ["git", "-C", str(root), "archive", base], stdout=stream, timeout=30
            )
            if result.returncode:
                raise Refusal("public-source")
            stream.seek(0)
            with tarfile.open(fileobj=stream) as archive:
                archive.extractall(stage, filter="data")
        for name in ("pyproject.toml", "uv.lock"):
            shutil.copyfile(root / name, stage / name)
        code, _ = capture(["uv", "sync", "--frozen", "--dev"], cwd=stage, environment=env)
        if code:
            raise Refusal("public-environment-setup")
        # uv's editable .pth must name the final directory, not the staging path.
        for path in stage.joinpath(".venv").rglob("*.pth"):
            path.write_bytes(path.read_bytes().replace(str(stage).encode(), str(target).encode()))
        binding = dict(base=base, source=snapshot(stage, include_environment=True))
        stage.joinpath("PREPARED.json").write_text(json.dumps(binding))
        stage.rename(target)


def verify_public(root, base, public):
    expected = {}
    for entry in git(root, "ls-tree", "-rz", base).split(b"\0"):
        if entry:
            metadata, name = entry.split(b"\t", 1)
            mode, kind, oid = metadata.decode().split()
            relative = Path(name.decode())
            for parent in reversed(relative.parents):
                if parent == Path("."):
                    continue
                if any(p.startswith(".") for p in parent.parts):
                    break
                expected[parent.as_posix()] = ("040000", None)
            if any(p.startswith(".") for p in relative.parts):
                continue
            if (
                relative.parts[0] in {"src", "roster"}
                or relative.suffix in NATIVE_SUFFIXES
                or relative.name == "py.typed"
            ):
                expected[relative.as_posix()] = (mode, oid)
    actual = {name: value[:2] for name, value in native_inputs(public).items()}
    if actual != expected:
        raise Refusal("public-source-drift")
    for name in ("pyproject.toml", "uv.lock"):
        if (root / name).read_bytes() != (public / name).read_bytes():
            raise Refusal("public-config-drift")


def compiler(root, env):
    code, raw = capture(
        [
            str(root / ".venv/bin/python"),
            "-I",
            "-B",
            "-m",
            "pyright",
            "--outputjson",
            "--project",
            "pyproject.toml",
        ],
        cwd=root,
        environment=env,
    )
    return account.errors(raw, root, code)


def run(root, mode):
    env, identity = prerequisites(root)
    base = trusted_base(root)
    if mode == "prepare":
        prepare(root, base, env)
        print("pyright: trusted source preparation complete")
        return
    baseline_path = root / "pyright-baseline.json"
    if baseline_path.is_symlink():
        raise Refusal("baseline-link")
    old = baseline_path.read_bytes() if baseline_path.exists() else None
    wiring = {
        name: digest((root / "scripts" / name).read_bytes())
        for name in ("pyright_runtime.py", "check_pyright_baseline.py")
    }
    before = snapshot(root, include_environment=True)
    started = time.monotonic()
    if initial(root, base):
        initial_dependencies(root, base)
        public = location(root, base)
        prepared = json.loads(public.joinpath("PREPARED.json").read_bytes())
        if prepared != dict(base=base, source=snapshot(public, include_environment=True)):
            raise Refusal("public-source-drift")
        verify_public(root, base, public)
        if prerequisites(public)[1] != identity:
            raise Refusal("public-checker-context")
        trusted = compiler(public, env)
        verify_public(root, base, public)
        if prepared["source"] != snapshot(public, include_environment=True):
            raise Refusal("public-source-drift")
    else:
        trusted = account.baseline(git(root, "show", base + ":pyright-baseline.json"))
    current = compiler(root, env)
    committed = account.baseline(old) if old is not None else None
    try:
        account.compare(current, current if mode == "update" else committed, trusted)
        if mode == "update" and committed is not None:
            account.compare(current, current, committed)
    except ValueError:
        raise Refusal("baseline-stale-or-growth") from None
    if (
        before != snapshot(root, include_environment=True)
        or base != trusted_base(root)
        or identity != prerequisites(root)[1]
    ):
        raise Refusal("input-drift")
    if wiring != {name: digest((root / "scripts" / name).read_bytes()) for name in wiring}:
        raise Refusal("checker-input-drift")
    if old != (baseline_path.read_bytes() if baseline_path.exists() else None):
        raise Refusal("baseline-drift")
    if mode == "update":
        (root / ".tmp").mkdir(exist_ok=True)
        with (root / ".tmp/pyright-update.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refusal("concurrent-update") from None
            if old != (baseline_path.read_bytes() if baseline_path.exists() else None):
                raise Refusal("baseline-drift")
            with tempfile.NamedTemporaryFile(
                dir=root, prefix=".pyright-baseline-", delete=False
            ) as stream:
                temporary = Path(stream.name)
                payload = dict(format=1, checker=VERSION, errors=dict(current))
                stream.write((json.dumps(payload, indent=2, sort_keys=True) + "\n").encode())
                stream.flush()
                os.fsync(stream.fileno())
            try:
                if (
                    before != snapshot(root, include_environment=True)
                    or base != trusted_base(root)
                    or identity != prerequisites(root)[1]
                    or wiring
                    != {name: digest((root / "scripts" / name).read_bytes()) for name in wiring}
                    or old != (baseline_path.read_bytes() if baseline_path.exists() else None)
                ):
                    raise Refusal("input-drift")
                os.replace(temporary, baseline_path)
            finally:
                temporary.unlink(missing_ok=True)

    print(
        json.dumps(
            dict(
                checker=identity,
                source=before,
                head=git(root, "rev-parse", "HEAD").decode().strip(),
                base=base,
                current=sum(current.values()),
                trusted=sum(trusted.values()),
                baseline=sum(current.values()) if mode == "update" else sum(committed.values()),
                elapsed_seconds=round(time.monotonic() - started, 3),
            )
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check", "update", "prepare"), nargs="?", default="check")
    args = parser.parse_args()
    try:
        run(Path(__file__).resolve().parents[1], args.mode)
    except (Exception, KeyboardInterrupt) as exc:
        category = str(exc) if isinstance(exc, Refusal) else "invalid-input-or-checker-output"
        print("pyright: refused (" + category + ")", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
