#!/usr/bin/env python3
"""Produce every closed application build context from the reviewed public recipes.

Docker access is required. No daemon configuration, credential, deployment or
runtime activation is performed. Each producer retains its own complete graph
and real networkless final phase; a missing or mismatched output refuses closure.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

CACHE_SCHEMA = "butlers.image-context-cache/v1"


class Runner(Protocol):
    def run(
        self, argv: list[str], *, cwd: Path, timeout: int = 1800, stream: bool = False
    ) -> str: ...


class Commands:
    """Use the existing owner-signal and process-group settlement boundary."""

    def __init__(self, project: Path, work: Path):
        source = project / "scripts/runtime_clis/produce_default_plugin_artifact.py"
        module = types.ModuleType("_image_context_command_boundary")
        module.__file__ = str(source)
        exec(compile(source.read_bytes(), str(source), "exec", dont_inherit=True), module.__dict__)
        self.boundary = module
        self.operations = module.Operations()
        self.work = work
        self.sequence = 0
        config = work / "docker-config"
        config.mkdir(mode=0o755)
        self.environment = {
            "HOME": os.environ["HOME"],
            "PATH": os.environ.get("PATH", os.defpath),
            "LANG": "C.UTF-8",
            "DOCKER_CONFIG": str(config),
            "DOCKER_BUILDKIT": "1",
        }

    def run(self, argv: list[str], *, cwd: Path, timeout: int = 1800, stream: bool = False) -> str:
        """Run one command; ``stream`` sends its output to this process's stdout live.

        Streamed output (the long buildx runs) lands in the caller's log as it happens,
        so a slow or stalled producer is visible; captured output stays in the work dir.
        """
        self.sequence += 1
        print(json.dumps({"command": self.sequence, "argv": argv}), flush=True)
        started = time.monotonic()

        def finished(exit_code: int) -> None:
            seconds = round(time.monotonic() - started, 1)
            line = {"command": self.sequence, "exit_code": exit_code, "seconds": seconds}
            print(json.dumps(line), flush=True)

        try:
            result = self.operations.run(
                argv, cwd=cwd, env=self.environment, capture=not stream, timeout=timeout
            )
        except subprocess.CalledProcessError as error:
            finished(error.returncode)
            write_json(
                self.work / f"command-{self.sequence:02d}.json",
                {"argv": argv, "exit_code": error.returncode, "completed": False},
            )
            raise
        finished(0)
        (self.work / f"command-{self.sequence:02d}.log").write_text(result)
        write_json(
            self.work / f"command-{self.sequence:02d}.json",
            {"argv": argv, "exit_code": 0, "completed": True},
        )
        return result


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
    path.chmod(0o644)


def checked_paths(project: Path, work: Path, output: Path, cache: Path | None = None) -> None:
    paths = (project, work, output) + ((cache,) if cache is not None else ())
    if any(path.absolute() != path.resolve() for path in paths):
        raise ValueError("project/work/output/cache require real absolute paths")
    if any(
        left == right or left.is_relative_to(right) or right.is_relative_to(left)
        for index, left in enumerate(paths)
        for right in paths[index + 1 :]
    ):
        raise ValueError("project/work/output/cache must be disjoint")
    if not project.is_dir() or any(path.exists() or path.is_symlink() for path in (work, output)):
        raise ValueError("project must exist and producer outputs must be absent")


def tracked_inputs(project: Path, commands: Runner) -> dict[str, dict[str, str]]:
    files = commands.run(["git", "ls-files", "--stage", "-z"], cwd=project)
    result = {}
    for entry in files.split("\0"):
        if not entry:
            continue
        header, name = entry.split("\t", 1)
        mode, blob, stage = header.split()
        relative = Path(name)
        path = project / relative
        if (
            stage != "0"
            or mode not in {"100644", "100755", "120000"}
            or relative.is_absolute()
            or ".." in relative.parts
            or "\\" in name
            or any(ord(character) < 32 or ord(character) == 127 for character in name)
            or any(part == ".git" for part in relative.parts)
        ):
            raise ValueError("checkout requires finite ordinary tracked inputs")
        if relative.name == ".env" or (
            relative.name.startswith(".env.") and relative.name != ".env.example"
        ):
            raise ValueError("environment files cannot enter an image build context")
        if relative.name in {".env.example", ".envrc"}:
            continue
        if any(
            parent.is_symlink()
            for parent in path.parents
            if parent != project and parent.is_relative_to(project)
        ):
            raise ValueError("tracked source parent must be a real contained directory")
        if mode == "120000":
            if not path.is_symlink():
                raise ValueError("tracked source link changed type")
            value = os.readlink(path)
            try:
                contained = (path.parent / value).resolve().is_relative_to(project)
            except RuntimeError:
                contained = False
            if (
                Path(value).is_absolute()
                or "\\" in value
                or any(ord(character) < 32 or ord(character) == 127 for character in value)
                or not contained
            ):
                raise ValueError("tracked source link escapes its closed checkout")
        else:
            if (
                path.is_symlink()
                or not path.is_file()
                or not path.resolve().is_relative_to(project)
            ):
                raise ValueError("tracked source must be a regular contained file")
            value = digest(path)
        result[name] = {"mode": mode, "blob": blob, "content": value}
    if not result:
        raise ValueError("empty source checkout")
    return result


def freeze(project: Path, source: Path, commands: Runner) -> dict:
    """Copy tracked current bytes only; never copy .git, .env or ambient caches."""
    before = tracked_inputs(project, commands)
    source.mkdir(mode=0o755)
    source.chmod(0o755)
    for name, entry in before.items():
        target = source / name
        target.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
        for parent in target.parents:
            if parent == source:
                break
            parent.chmod(0o755)
        if entry["mode"] == "120000":
            target.symlink_to(entry["content"])
        else:
            shutil.copyfile(project / name, target)
            target.chmod(0o755 if entry["mode"] == "100755" else 0o644)
    if tracked_inputs(project, commands) != before:
        raise ValueError("source changed while freezing the producer context")
    return before


def verify_frozen(source: Path, expected: dict[str, dict[str, str]]) -> None:
    files = {
        path.relative_to(source).as_posix()
        for path in source.rglob("*")
        if path.is_symlink() or path.is_file()
    }
    if files != set(expected):
        raise ValueError("frozen producer source population changed")
    for name, entry in expected.items():
        path = source / name
        if entry["mode"] == "120000":
            valid = path.is_symlink() and os.readlink(path) == entry["content"]
        else:
            valid = (
                not path.is_symlink()
                and path.is_file()
                and digest(path) == entry["content"]
                and path.stat().st_mode & 0o7777 == (0o755 if entry["mode"] == "100755" else 0o644)
            )
        if not valid:
            raise ValueError("frozen producer source bytes/modes changed")


@dataclass(frozen=True)
class Build:
    name: str
    dockerfile: str
    contexts: tuple[tuple[str, str], ...] = ()
    arguments: tuple[str, ...] = ()


def builds() -> tuple[Build, ...]:
    common = (("system-artifacts", "system"), ("codex-compiler-artifacts", "compiler"))
    node = (("toolchain-artifacts", "toolchains"),)
    return (
        Build("toolchains", "Dockerfile.toolchain-artifacts"),
        Build("codex", "Dockerfile.codex-artifacts", common),
        Build("google-auth", "Dockerfile.npm-artifacts", common, ("VENDOR=google-auth-library",)),
        Build("genai", "Dockerfile.npm-artifacts", common, ("VENDOR=js-genai",)),
        Build(
            "plugin",
            "Dockerfile.default-plugin-artifacts",
            (("system-artifacts", "system"),) + node,
        ),
        Build(
            "gemini",
            "Dockerfile.gemini-artifacts",
            common
            + node
            + (("google-auth-artifacts", "google-auth"), ("genai-sdk-artifacts", "genai")),
        ),
        Build(
            "opencode",
            "Dockerfile.opencode-artifacts",
            common + node + (("default-plugin-artifacts", "plugin"),),
        ),
        Build(
            "native",
            "Dockerfile.native-package-artifacts",
            (("system-artifacts", "system"),) + node + (("codex-artifacts", "codex"),),
        ),
        Build("python", "Dockerfile.python-artifacts", (("python-source", "python-source"),)),
        Build("go", "Dockerfile.go-artifacts"),
    )


# --- Input-fingerprint cache -------------------------------------------------------------
#
# A producer's output is reused only when everything its build can read is byte-identical
# to what the cached output was built from: its Dockerfile, every checkout path the
# Dockerfile COPYs, its build arguments, and the exact bytes of every named context it
# consumes. Base images and network fetches are already pinned by digest inside those
# files. Any recipe whose reads cannot be enumerated (ADD, a context bind mount, a cache
# mount, a glob or JSON/heredoc COPY) is never cached. A cached tree is re-digested on
# every use; a mismatch evicts it and the producer runs.

_STATELESS_MOUNTS = {"tmpfs", "secret", "ssh"}


def copied_sources(recipe: str) -> list[str] | None:
    """Checkout paths a Dockerfile reads, or None when they cannot be enumerated."""
    sources: list[str] = []
    for line in re.sub(r"\\\n", " ", recipe).splitlines():
        words = line.split()
        if not words or words[0].startswith("#"):
            continue
        instruction = words[0].upper()
        if instruction == "ADD" or "<<" in line:
            return None
        if instruction == "RUN":
            for word in words[1:]:
                if not word.startswith("--mount="):
                    continue
                options = dict(
                    option.split("=", 1) if "=" in option else (option, "")
                    for option in word.removeprefix("--mount=").split(",")
                )
                if "from" not in options and options.get("type", "bind") not in _STATELESS_MOUNTS:
                    return None
        if instruction != "COPY":
            continue
        arguments = words[1:]
        if any(word.startswith("--from=") for word in arguments):
            continue
        paths = [word for word in arguments if not word.startswith("--")]
        if not paths or paths[0].startswith("["):
            return None
        for source in paths[:-1]:
            if any(character in source for character in "*?[") or ".." in source.split("/"):
                return None
            sources.append(source.removeprefix("./"))
    return sources


def tree_digest(root: Path) -> str:
    """Digest of every relative path, file type, permission bits and content below root."""
    entries = hashlib.sha256()
    for directory, names, files in os.walk(root):
        names.sort()
        for name in sorted(names + files):
            path = Path(directory) / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode):
                kind, value = "link", os.readlink(path)
            elif stat.S_ISDIR(info.st_mode):
                kind, value = "dir", ""
            elif stat.S_ISREG(info.st_mode):
                kind, value = "file", digest(path)
            else:
                raise ValueError("context holds a special file")
            relative = path.relative_to(root).as_posix()
            entry = [relative, kind, stat.S_IMODE(info.st_mode), value]
            entries.update(json.dumps(entry).encode() + b"\n")
    return entries.hexdigest()


def fingerprint(
    build: Build, identity: dict[str, dict[str, str]], source: Path, stage: Path
) -> tuple[str, dict] | None:
    """The producer's complete input record and its digest, or None if uncacheable.

    File entries come from the frozen-source identity, which ``verify_frozen`` re-proves
    before any context is cached or published.
    """
    entry = identity.get(build.dockerfile)
    if entry is None or entry["mode"] == "120000":
        return None
    sources = copied_sources((source / build.dockerfile).read_text())
    if sources is None:
        return None
    files = {build.dockerfile: entry}
    for source in sources:
        prefix = source.rstrip("/")
        matched = {
            name: value
            for name, value in identity.items()
            if prefix in {"", "."} or name == prefix or name.startswith(prefix + "/")
        }
        if not matched:
            return None
        files.update(matched)
    record = {
        "schema": CACHE_SCHEMA,
        "producer": build.name,
        "dockerfile": build.dockerfile,
        "platform": "linux/amd64",
        "target": "artifacts",
        "arguments": list(build.arguments),
        "files": {name: [value["mode"], value["content"]] for name, value in sorted(files.items())},
        "contexts": {name: tree_digest(stage / dependency) for name, dependency in build.contexts},
    }
    encoded = json.dumps(record, sort_keys=True).encode()
    return hashlib.sha256(encoded).hexdigest(), record


def _link_tree(source: Path, target: Path) -> None:
    def link(origin: str, destination: str) -> None:
        try:
            os.link(origin, destination)
        except OSError:
            shutil.copy2(origin, destination)

    shutil.copytree(source, target, symlinks=True, copy_function=link)


def reuse(cache: Path, name: str, key: str, target: Path) -> bool:
    """Materialize a cached context at target only if it still digests as recorded."""
    entry = cache / name / key
    try:
        recorded = json.loads((entry / "entry.json").read_text())
        _link_tree(entry / "context", target)
    except (OSError, ValueError):
        recorded = None
    if (
        recorded is not None
        and recorded.get("schema") == CACHE_SCHEMA
        and recorded.get("fingerprint") == key
        and target.is_dir()
        and tree_digest(target) == recorded.get("context_sha256")
    ):
        return True
    shutil.rmtree(target, ignore_errors=True)
    shutil.rmtree(entry, ignore_errors=True)
    return False


def store(cache: Path, name: str, key: str, record: dict, context: Path) -> None:
    """Record a freshly built context; keep only this one entry per producer."""
    producer_cache = cache / name
    producer_cache.mkdir(parents=True, exist_ok=True)
    partial = producer_cache / f".partial-{key}-{os.getpid()}"
    shutil.rmtree(partial, ignore_errors=True)
    partial.mkdir()
    _link_tree(context, partial / "context")
    write_json(
        partial / "entry.json",
        {
            "schema": CACHE_SCHEMA,
            "fingerprint": key,
            "inputs": record,
            "context_sha256": tree_digest(context),
        },
    )
    for stale in producer_cache.iterdir():
        if stale != partial:
            shutil.rmtree(stale)
    partial.rename(producer_cache / key)


def produce(
    project: Path,
    work: Path,
    output: Path,
    commands: Runner | None = None,
    *,
    prune_build_cache: bool = False,
    cache: Path | None = None,
) -> dict:
    """Produce every context; ``prune_build_cache`` drops each finished producer's build cache.

    No producer reads another's cache (each later build takes the extracted context
    directories), so pruning after each extract only frees disk. The nightly runner needs it:
    the finished producers' cache filled its single root disk (run 43554266906).

    ``cache`` keeps each producer's context across runs, keyed by its input fingerprint
    (see ``fingerprint``); an unchanged producer is reused instead of rebuilt. Producers
    still run serially: the owner-signal boundary is main-thread only.
    """
    checked_paths(project, work, output, cache)
    work.mkdir(mode=0o755)
    commands = commands or Commands(project, work)
    if isinstance(commands, Commands):
        try:
            with commands.boundary.owner_signals():
                return _produce(project, work, output, commands, prune_build_cache, cache)
        except commands.boundary.OwnerSignal as interrupted:
            signal.signal(interrupted.number, signal.SIG_DFL)
            os.kill(os.getpid(), interrupted.number)
            raise SystemExit(128 + interrupted.number) from None
    return _produce(project, work, output, commands, prune_build_cache, cache)


def _produce(
    project: Path,
    work: Path,
    output: Path,
    commands: Runner,
    prune_build_cache: bool,
    cache: Path | None,
) -> dict:
    source = work / "source"
    identity = freeze(project, source, commands)
    write_json(work / "source-inputs.json", identity)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="image-contexts-", dir=output.parent) as temporary:
        stage = Path(temporary) / "contexts"
        stage.mkdir(mode=0o755)
        stage.chmod(0o755)
        for name, manifest in (
            ("system", "deploy/runtime-clis/debian-system-inputs.json"),
            ("compiler", "deploy/runtime-clis/codex/compiler-inputs.json"),
        ):
            commands.run(
                [
                    sys.executable,
                    "-I",
                    "-B",
                    str(source / "scripts/runtime_clis/fetch_system_artifacts.py"),
                    "--manifest",
                    str(source / manifest),
                    "--output",
                    str(stage / name),
                ],
                cwd=source,
                timeout=1800,
            )
        commands.run(
            [
                sys.executable,
                "-I",
                "-B",
                str(source / "scripts/runtime_clis/produce_python_artifacts.py"),
                "--phase",
                "snapshot",
                "--project",
                str(project),
                "--work",
                str(work / "snapshot-work"),
                "--output",
                str(stage / "python-source"),
                "--backend-lock",
                str(source / "deploy/runtime-clis/python-artifacts/build-lock.toml"),
            ],
            cwd=source,
        )
        if tracked_inputs(project, commands) != identity:
            raise ValueError("checkout changed while capturing the Python source snapshot")
        reused = []
        for build in builds():
            started = time.monotonic()
            for _name, dependency in build.contexts:
                if not (stage / dependency).is_dir():
                    raise ValueError("producer dependency has no completed context")
            key = fingerprint(build, identity, source, stage) if cache is not None else None
            if key is not None and reuse(cache, build.name, key[0], stage / build.name):
                reused.append(build.name)
                outcome = "hit"
            else:
                _build(build, source, stage, Path(temporary), commands, prune_build_cache)
                outcome = "off" if cache is None else "uncacheable" if key is None else "built"
                if key is not None:
                    # The build read the frozen source; prove it still matches the identity
                    # the fingerprint was taken from before recording the output under it.
                    verify_frozen(source, identity)
                    store(cache, build.name, key[0], key[1], stage / build.name)
            summary = {
                "producer": build.name,
                "cache": outcome,
                "fingerprint": key[0] if key else None,
                "seconds": round(time.monotonic() - started, 1),
            }
            print(json.dumps(summary), flush=True)
        components = {
            "node": stage / "toolchains/components/node",
            "uv": stage / "toolchains/components/uv",
            "claude": stage / "native/components/claude",
            "codex": stage / "native/components/codex",
            "gemini": stage / "gemini/components/gemini",
            "opencode": stage / "opencode/components/opencode",
        }
        argv = [
            sys.executable,
            "-I",
            "-B",
            str(source / "scripts/runtime_clis/runtime_artifacts.py"),
            "close",
            "--manifest",
            str(source / "deploy/runtime-clis/runtime-inputs.json"),
            "--output",
            str(stage / "runtime"),
        ]
        for name, component in components.items():
            argv += ["--" + name, str(component)]
        commands.run(argv, cwd=source, timeout=1800)
        runtime_manifest = stage / "runtime/inputs.json"
        if (
            not runtime_manifest.is_file()
            or runtime_manifest.is_symlink()
            or runtime_manifest.read_bytes()
            != (source / "deploy/runtime-clis/runtime-inputs.json").read_bytes()
        ):
            raise ValueError("complete six-component runtime did not close")
        verify_frozen(source, identity)
        shutil.copyfile(work / "source-inputs.json", stage / "source-inputs.json")
        result = {
            "schema": "butlers.image-contexts/v1",
            "status": "CLOSED_CONTEXTS",
            "runtime_inputs_sha256": digest(source / "deploy/runtime-clis/runtime-inputs.json"),
            "source_inputs_sha256": digest(work / "source-inputs.json"),
            "producers": [build.name for build in builds()],
            "reused": reused,
            "scope": (
                "Actual producer execution and closed contexts; no final image/deployment claim."
            ),
        }
        write_json(stage / "verification.json", result)
        stage.rename(output)
    return result


def _build(
    build: Build,
    source: Path,
    stage: Path,
    temporary: Path,
    commands: Runner,
    prune_build_cache: bool,
) -> None:
    # One tar stream, then a local extract. The per-file local exporter
    # stalled indefinitely on the Python producer's ~190k-file output;
    # the same graph exported as a tarball finishes in about a minute.
    tarball = temporary / (build.name + ".tar")
    argv = [
        "docker",
        "buildx",
        "build",
        "--builder",
        "default",
        "--platform",
        "linux/amd64",
        "--progress",
        "plain",
        "--target",
        "artifacts",
        "--file",
        str(source / build.dockerfile),
        "--output",
        "type=tar,dest=" + str(tarball),
    ]
    for name, dependency in build.contexts:
        argv += ["--build-context", name + "=" + str(stage / dependency)]
    for argument in build.arguments:
        argv += ["--build-arg", argument]
    commands.run(argv + [str(source)], cwd=source, timeout=10800, stream=True)
    if not tarball.is_file() or tarball.is_symlink():
        raise ValueError("producer did not export its complete context")
    (stage / build.name).mkdir(mode=0o755)
    commands.run(
        [
            "tar",
            "--extract",
            "--no-same-owner",
            "--file",
            str(tarball),
            "--directory",
            str(stage / build.name),
        ],
        cwd=source,
    )
    tarball.unlink()
    if not any((stage / build.name).iterdir()):
        raise ValueError("producer did not export its complete context")
    if prune_build_cache:
        commands.run(
            ["docker", "buildx", "prune", "--builder", "default", "--all", "--force"],
            cwd=source,
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("project", "work", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument(
        "--prune-build-cache",
        action="store_true",
        help="drop each producer's build cache once its context is extracted",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        help="reuse each producer's context across runs while its input fingerprint matches",
    )
    args = parser.parse_args()
    result = produce(
        args.project.absolute(),
        args.work.absolute(),
        args.output.absolute(),
        prune_build_cache=args.prune_build_cache,
        cache=args.cache.absolute() if args.cache else None,
    )
    print(json.dumps(result))


if __name__ == "__main__":
    main()
