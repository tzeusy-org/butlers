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
import shutil
import signal
import subprocess
import sys
import tempfile
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class Runner(Protocol):
    def run(self, argv: list[str], *, cwd: Path, timeout: int = 1800) -> str: ...


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

    def run(self, argv: list[str], *, cwd: Path, timeout: int = 1800) -> str:
        self.sequence += 1
        print(json.dumps({"command": self.sequence, "argv": argv}), flush=True)
        try:
            result = self.operations.run(
                argv, cwd=cwd, env=self.environment, capture=True, timeout=timeout
            )
        except subprocess.CalledProcessError as error:
            write_json(
                self.work / f"command-{self.sequence:02d}.json",
                {"argv": argv, "exit_code": error.returncode, "completed": False},
            )
            raise
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


def checked_paths(project: Path, work: Path, output: Path) -> None:
    paths = (project, work, output)
    if any(path.absolute() != path.resolve() for path in paths):
        raise ValueError("project/work/output require real absolute paths")
    if any(
        left == right or left.is_relative_to(right) or right.is_relative_to(left)
        for index, left in enumerate(paths)
        for right in paths[index + 1 :]
    ):
        raise ValueError("project/work/output must be disjoint")
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


def produce(project: Path, work: Path, output: Path, commands: Runner | None = None) -> dict:
    checked_paths(project, work, output)
    work.mkdir(mode=0o755)
    commands = commands or Commands(project, work)
    if isinstance(commands, Commands):
        try:
            with commands.boundary.owner_signals():
                return _produce(project, work, output, commands)
        except commands.boundary.OwnerSignal as interrupted:
            signal.signal(interrupted.number, signal.SIG_DFL)
            os.kill(os.getpid(), interrupted.number)
            raise SystemExit(128 + interrupted.number) from None
    return _produce(project, work, output, commands)


def _produce(project: Path, work: Path, output: Path, commands: Runner) -> dict:
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
        for build in builds():
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
                "type=local,dest=" + str(stage / build.name),
            ]
            for name, dependency in build.contexts:
                if not (stage / dependency).is_dir():
                    raise ValueError("producer dependency has no completed context")
                argv += ["--build-context", name + "=" + str(stage / dependency)]
            for argument in build.arguments:
                argv += ["--build-arg", argument]
            commands.run(argv + [str(source)], cwd=source, timeout=10800)
            if not (stage / build.name).is_dir():
                raise ValueError("producer did not export its complete context")
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
            "scope": (
                "Actual producer execution and closed contexts; no final image/deployment claim."
            ),
        }
        write_json(stage / "verification.json", result)
        stage.rename(output)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("project", "work", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    print(
        json.dumps(produce(args.project.absolute(), args.work.absolute(), args.output.absolute()))
    )


if __name__ == "__main__":
    main()
