"""Generate the exact read-only runtime closure for Dashboard CLI sandboxes.

This runs while building ``Dockerfile.base`` after the pinned global npm
packages are installed.  Runtime code only reads the resulting immutable JSON
manifest; it never discovers host paths or broadens the Bubblewrap view.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import stat
import subprocess
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class RuntimeProvider:
    """One CLI binary and its pinned global npm package."""

    name: str
    binary: str
    package: str


@dataclass(frozen=True)
class RuntimeInputBinding:
    """One immutable host object mounted at its child-visible logical path."""

    source: Path
    destination: Path


_RUNTIME_PROVIDERS = (
    RuntimeProvider("codex", "codex", "@openai/codex"),
    RuntimeProvider("opencode-openai", "opencode", "opencode-ai"),
    RuntimeProvider("opencode-go", "opencode", "opencode-ai"),
)
_NETWORK_RUNTIME_FILES = (Path("/etc/resolv.conf"), Path("/etc/ssl/certs/ca-certificates.crt"))


class ManifestGenerationError(RuntimeError):
    """Raised when the image cannot prove an exact runtime input closure."""


def _safe_regular_or_directory(path: Path) -> Path:
    """Return an absolute root-owned immutable image path or fail the build."""
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise ManifestGenerationError(f"runtime input is unavailable: {path}") from exc
    if (
        not path.is_absolute()
        or stat.S_ISLNK(metadata.st_mode)
        or not (stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode))
        or metadata.st_uid != 0
        or metadata.st_mode & 0o022
    ):
        raise ManifestGenerationError(f"runtime input is unsafe: {path}")
    return path


def _runtime_input_binding(logical_path: Path) -> RuntimeInputBinding:
    """Validate a terminal image object without rewriting its child path.

    Debian's ELF loader and several shared libraries are exposed through
    logical aliases such as ``/lib64/ld-linux-...``.  The empty-root child
    must see that exact logical destination, while the host-side Bubblewrap
    source must be the terminal regular file rather than an unchecked
    symlink.  Resolving is therefore validation-only; callers mount
    ``source`` at ``destination`` and never bind a containing tree.
    """
    if not logical_path.is_absolute():
        raise ManifestGenerationError(f"runtime input is unsafe: {logical_path}")
    try:
        source = logical_path.resolve(strict=True)
    except OSError as exc:
        raise ManifestGenerationError(f"runtime input is unavailable: {logical_path}") from exc
    _safe_regular_or_directory(source)
    return RuntimeInputBinding(source=source, destination=logical_path)


def _resolve_command(binary: str) -> Path:
    """Resolve a pinned image executable to its real non-symlink source."""
    resolved = shutil.which(binary)
    if resolved is None:
        raise ManifestGenerationError(f"runtime binary is unavailable: {binary}")
    path = Path(resolved).resolve(strict=True)
    _safe_regular_or_directory(path)
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or not metadata.st_mode & stat.S_IXUSR:
        raise ManifestGenerationError(f"runtime binary is not executable: {path}")
    return path


def _global_npm_root() -> Path:
    """Return npm's immutable global package root during image construction."""
    result = subprocess.run(
        ["npm", "root", "--global"],
        check=True,
        capture_output=True,
        text=True,
    )
    rendered = result.stdout.strip()
    if not rendered:
        raise ManifestGenerationError("npm global package root is unavailable")
    return _safe_regular_or_directory(Path(rendered))


def _ldd_dependencies(path: Path) -> tuple[RuntimeInputBinding, ...]:
    """List direct shared-library sources for an executable when it is dynamic."""
    result = subprocess.run(
        ["ldd", str(path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # Scripts and static binaries legitimately have no ldd closure.
        return ()

    dependencies: list[RuntimeInputBinding] = []
    for line in result.stdout.splitlines():
        rendered = line.strip()
        if not rendered or "not a dynamic executable" in rendered:
            continue
        if "=>" in rendered:
            _, _, remainder = rendered.partition("=>")
            candidate = remainder.strip().split(" ", 1)[0]
        else:
            candidate = rendered.split(" ", 1)[0]
        if candidate.startswith("/"):
            dependencies.append(_runtime_input_binding(Path(candidate)))
    return tuple(dependencies)


def _shebang_interpreters(path: Path) -> tuple[RuntimeInputBinding, ...]:
    """Return interpreters needed by a script launcher without mounting PATH dirs."""
    try:
        first_line = path.read_bytes().splitlines()[0].decode("utf-8")
    except (IndexError, OSError, UnicodeDecodeError):
        return ()
    if not first_line.startswith("#!"):
        return ()
    words = shlex.split(first_line[2:].strip())
    if not words:
        raise ManifestGenerationError(f"runtime script has an invalid shebang: {path}")
    interpreter = Path(words[0])
    if not interpreter.is_absolute():
        resolved = shutil.which(words[0])
        if resolved is None:
            raise ManifestGenerationError(f"runtime interpreter is unavailable: {words[0]}")
        interpreter = Path(resolved)
    paths = [_runtime_input_binding(interpreter)]
    if interpreter.name == "env":
        executable_words = [word for word in words[1:] if not word.startswith("-")]
        if not executable_words:
            raise ManifestGenerationError(f"runtime env shebang has no command: {path}")
        target = shutil.which(executable_words[0])
        if target is None:
            raise ManifestGenerationError(
                f"runtime interpreter is unavailable: {executable_words[0]}"
            )
        paths.append(_runtime_input_binding(Path(target)))
    return tuple(paths)


def _runtime_closure(
    executable: Path,
    package_root: Path,
) -> tuple[RuntimeInputBinding, ...]:
    """Build the minimal explicit Bubblewrap input list for one CLI package."""
    pending = [
        *(_runtime_input_binding(path) for path in (executable, package_root)),
        *(_runtime_input_binding(path) for path in _NETWORK_RUNTIME_FILES),
    ]
    closure: list[RuntimeInputBinding] = []
    seen: dict[Path, Path] = {}
    while pending:
        candidate = pending.pop()
        previous_source = seen.get(candidate.destination)
        if previous_source is not None:
            if previous_source != candidate.source:
                raise ManifestGenerationError(
                    "runtime input closure has conflicting logical destinations"
                )
            continue
        seen[candidate.destination] = candidate.source
        closure.append(candidate)
        if not candidate.source.is_file():
            continue
        interpreters = _shebang_interpreters(candidate.source)
        dependencies = _ldd_dependencies(candidate.source)
        pending.extend(interpreters)
        pending.extend(dependencies)
    return tuple(sorted(closure, key=lambda binding: str(binding.destination)))


def build_manifest(
    *,
    npm_root: Callable[[], Path] = _global_npm_root,
    resolve_command: Callable[[str], Path] = _resolve_command,
    runtime_closure: Callable[[Path, Path], tuple[RuntimeInputBinding, ...]] = _runtime_closure,
) -> dict[str, object]:
    """Build a deterministic provider-to-image-input map without credential data."""
    package_root = npm_root()
    providers: dict[str, object] = {}
    for provider in _RUNTIME_PROVIDERS:
        executable = resolve_command(provider.binary)
        package = _safe_regular_or_directory(package_root / provider.package)
        providers[provider.name] = {
            "binary": provider.binary,
            "executable": str(executable),
            "readonly_inputs": [
                {
                    "destination": str(binding.destination),
                    "source": str(binding.source),
                }
                for binding in runtime_closure(executable, package)
            ],
        }
    return {"version": 2, "providers": providers}


def write_manifest(output: Path, document: dict[str, object]) -> None:
    """Write an immutable, value-free image asset atomically."""
    output.parent.mkdir(mode=0o755, parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o444)
    temporary.replace(output)
    os.chmod(output, 0o444)


def main(argv: Iterable[str] | None = None) -> int:
    """Generate the manifest or emit a safe build-time error."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        required=True,
        help="immutable image manifest path",
    )
    args = parser.parse_args(argv)
    try:
        write_manifest(args.output, build_manifest())
    except (ManifestGenerationError, OSError, subprocess.SubprocessError) as exc:
        print(f"runtime CLI sandbox manifest generation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
