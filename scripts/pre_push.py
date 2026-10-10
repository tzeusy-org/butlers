#!/usr/bin/env python3
"""Opt-in composed, read-only push refusal; see REQ-testing-052.

The common Git configuration changes only through explicit install/uninstall.
The canonical checkout is never configured by importing/running checks. Guard
output stays in RAM: failures report fixed target names and numeric statuses.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

HOOKS = ("pre-commit", "prepare-commit-msg", "post-checkout", "post-merge", "pre-push")
STATE = "butlers-pre-push.json"
DISPATCHER = "butlers-pre-push-hooks"
DISPATCH_FILES = (*HOOKS, "pre_push.py", "pre_push_sandbox.py")
CHECK_TIMEOUT = 300
OID = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")
# Existing tracked assets are not regenerated or replaced by this installer.
MANAGED = {
    "pre-commit": "82b4e881b60cdc9c04325eedd444c779fe0542703bdbe6978da1a72c0b7578b4",
    "prepare-commit-msg": "305780a3937f9fb5a0af1a68329637c21e4e7853bcc4a7464a149b24ec657c1d",
    "post-checkout": "39adfc06edca91815bd1543e2e62d34debfe39cac362f90a2e106c1dac0fb065",
    "post-merge": "582cfca972ac8c41ebad90a888dd294a653670e9dc1224ec712454c1cb3ca2f9",
    "pre-push": "fb80208d2f9921f4ce2854fb9b3867f367215b7369c175e004a619c2ffdd7c41",
}
BD_SHA256 = "a8f48d771b9e11eccfced4aed72923b59eda746f7a9b425e7ca1af7a51251653"


class Refusal(Exception):
    """Fixed categorical refusal, never a child exception/operand dump."""

    def __init__(self, category: str, code: int = 1):
        super().__init__(category)
        self.code = code


def git(root: Path, *args: str) -> bytes:
    result = subprocess.run(["git", "-C", str(root), *args], capture_output=True)
    if result.returncode:
        raise Refusal("git-input-unavailable")
    return result.stdout


def common_dir(root: Path) -> Path:
    return Path(
        git(root, "rev-parse", "--path-format=absolute", "--git-common-dir").decode().strip()
    )


def body_digest(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise Refusal("missing-or-linked-asset")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def asset_binding(root: Path) -> dict:
    if any(x.name not in HOOKS for x in (root / ".githooks").iterdir()) or any(
        (root / ".beads/hooks").glob("*.old")
    ):
        raise Refusal("uncomposable-hook-chain")
    files = [f".githooks/{name}" for name in HOOKS]
    files += [f".beads/hooks/{name}" for name in HOOKS]
    files += ["scripts/pre_push.py", "scripts/pre_push_sandbox.py"]
    result = {}
    for name in files:
        path = root / name
        result[name] = {"sha256": body_digest(path), "mode": path.stat().st_mode & 0o777}
        if name.startswith((".githooks/", ".beads/hooks/")) and not os.access(path, os.X_OK):
            raise Refusal("hook-not-executable")
    for name, expected in MANAGED.items():
        if result[f".beads/hooks/{name}"]["sha256"] != expected:
            raise Refusal("unsupported-managed-source")
    bd = shutil.which("bd")
    if not bd:
        raise Refusal("bd-unavailable")
    if hashlib.sha256(Path(bd).read_bytes()).hexdigest() != BD_SHA256:
        raise Refusal("unsupported-bd-binary")
    version = subprocess.run([bd, "--version"], capture_output=True, timeout=5)
    if version.returncode or not version.stdout.startswith(b"bd version 1.3.1 (c1c4b642a)"):
        raise Refusal("unsupported-bd-delegate")
    result["bd"] = {
        "path": str(Path(bd).resolve()),
        "sha256": hashlib.sha256(Path(bd).read_bytes()).hexdigest(),
    }
    return result


def config_origins(root: Path) -> bytes:
    result = subprocess.run(
        [
            "git",
            "-C",
            str(root),
            "config",
            "--show-origin",
            "--show-scope",
            "--null",
            "--get-all",
            "core.hooksPath",
        ],
        capture_output=True,
    )
    if result.returncode not in (0, 1):
        raise Refusal("config-unavailable")
    # Git reports the same common config relatively from its root and
    # absolutely from a worktree. Keep scope/value exact, bind file identity.
    fields = result.stdout.split(b"\0")
    if fields == [b""]:
        return b""
    if fields[-1] or (len(fields) - 1) % 3:
        raise Refusal("config-origin-malformed")
    normalized = []
    for index in range(0, len(fields) - 1, 3):
        scope, origin, value = fields[index : index + 3]
        if origin.startswith(b"file:"):
            path = Path(os.fsdecode(origin[5:]))
            origin = b"file:" + os.fsencode((root / path).resolve())
        normalized.extend((scope, origin, value))
    return b"\0".join(normalized) + b"\0"


@contextmanager
def installation_lock(common: Path):
    # Lock the existing directory inode; rollback leaves no new lock carrier.
    fd = os.open(common, os.O_RDONLY | os.O_DIRECTORY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


@contextmanager
def recovery_signals():
    # An inverse cannot be interrupted by a second catchable termination.
    prior = signal.pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT, signal.SIGTERM, signal.SIGHUP})
    try:
        yield
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, prior)


def dispatcher_bodies(directory: Path) -> dict:
    if directory.is_symlink() or not directory.is_dir():
        raise Refusal("installed-dispatcher-unavailable")
    if {p.name for p in directory.iterdir()} != set(DISPATCH_FILES):
        raise Refusal("installed-dispatcher-drift")
    result = {}
    for name in DISPATCH_FILES:
        path = directory / name
        body_digest(path)
        result[name] = {"body_hex": path.read_bytes().hex(), "mode": path.stat().st_mode & 0o777}
    return result


def write_dispatcher(directory: Path, bodies: dict) -> None:
    directory.mkdir(exist_ok=True)
    for name, body in bodies.items():
        path = directory / name
        path.write_bytes(bytes.fromhex(body["body_hex"]))
        path.chmod(body["mode"])


def remove_dispatcher(directory: Path) -> None:
    for name in DISPATCH_FILES:
        (directory / name).unlink(missing_ok=True)
    directory.rmdir()


def install(root: Path, *, uninstall: bool = False) -> None:
    common = common_dir(root)
    config = common / "config"
    state_path = common / STATE
    dispatcher = common / DISPATCHER
    with installation_lock(common):
        origins = config_origins(root)
        if state_path.exists():
            state_body = state_path.read_bytes()
            state_mode = state_path.stat().st_mode & 0o777
            try:
                state = json.loads(state_body)
                installed = bytes.fromhex(state["installed_config_hex"])
                prior = bytes.fromhex(state["prior_config_hex"])
                if (
                    config.read_bytes() != installed
                    or state["schema"] != 2
                    or origins.decode() != state["installed_origins"]
                    or dispatcher_bodies(dispatcher) != state["dispatcher"]
                ):
                    raise Refusal("installation-state-drift")
            except (KeyError, ValueError, TypeError):
                raise Refusal("installation-state-malformed") from None
            if uninstall:
                temporary = common / (STATE + ".restore")
                if temporary.exists():
                    raise Refusal("installation-temporary-state-present")
                try:
                    temporary.write_bytes(prior)
                    temporary.chmod(state["prior_config_mode"])
                    temporary.replace(config)
                    remove_dispatcher(dispatcher)
                    state_path.unlink()
                except BaseException:
                    with recovery_signals():
                        config.write_bytes(installed)
                        config.chmod(state["installed_config_mode"])
                        write_dispatcher(dispatcher, state["dispatcher"])
                        state_path.write_bytes(state_body)
                        state_path.chmod(state_mode)
                        temporary.unlink(missing_ok=True)
                    raise
            elif asset_binding(root) != state["assets"]:
                raise Refusal("installation-asset-drift")
            return
        if uninstall:
            raise Refusal("installation-state-missing")
        if origins:
            raise Refusal("unsupported-hook-configuration")
        if (
            subprocess.run(
                ["git", "-C", str(root), "config", "--get", "extensions.worktreeConfig"],
                capture_output=True,
            ).stdout.strip()
            == b"true"
        ):
            raise Refusal("worktree-configuration-unsupported")
        hooks = common / "hooks"
        if hooks.exists() and any(
            x.name.endswith(".old")
            or (x.is_file() and os.access(x, os.X_OK) and not x.name.endswith(".sample"))
            for x in hooks.iterdir()
        ):
            raise Refusal("uncomposable-default-hook")
        if any((root / ".beads/hooks").glob("*.old")):
            raise Refusal("uncomposable-managed-chain")
        assets = asset_binding(root)
        before = config.read_bytes()
        mode = config.stat().st_mode & 0o777
        temporary = state_path.with_suffix(".tmp")
        if temporary.exists() or dispatcher.exists() or dispatcher.is_symlink():
            raise Refusal("installation-temporary-state-present")
        bodies = {
            name: {
                "body_hex": (root / ".githooks" / name).read_bytes().hex(),
                "mode": (root / ".githooks" / name).stat().st_mode & 0o777,
            }
            for name in HOOKS
        }
        for name in ("pre_push.py", "pre_push_sandbox.py"):
            path = root / "scripts" / name
            bodies[name] = {
                "body_hex": path.read_bytes().hex(),
                "mode": path.stat().st_mode & 0o777,
            }
        try:
            # Common absolute dispatch keeps hooks present even when another
            # owning worktree predates these assets. Its wrapper refuses missing
            # owning code/runtime instead of Git silently skipping a hook.
            write_dispatcher(dispatcher, bodies)
            result = subprocess.run(
                ["git", "-C", str(root), "config", "--local", "core.hooksPath", str(dispatcher)],
                capture_output=True,
            )
            if result.returncode:
                raise Refusal("installation-config-write-failed")
            state = {
                "schema": 2,
                "prior_config_hex": before.hex(),
                "prior_config_mode": mode,
                "prior_origins": origins.decode(),
                "installed_config_hex": config.read_bytes().hex(),
                "installed_config_mode": config.stat().st_mode & 0o777,
                "installed_origins": config_origins(root).decode(),
                "assets": assets,
                "dispatcher": bodies,
            }
            temporary.write_text(json.dumps(state, sort_keys=True) + "\n")
            temporary.chmod(mode)
            temporary.replace(state_path)
        except BaseException:
            with recovery_signals():
                config.write_bytes(before)
                config.chmod(mode)
                temporary.unlink(missing_ok=True)
                state_path.unlink(missing_ok=True)
                if dispatcher.exists():
                    remove_dispatcher(dispatcher)
            raise


def validate_updates(raw: bytes, head: str) -> bytes:
    if not raw or not raw.endswith(b"\n"):
        raise Refusal("ref-input-malformed")
    for line in raw.splitlines():
        try:
            local, oid, remote, old = line.decode("ascii").split(" ")
        except (ValueError, UnicodeError):
            raise Refusal("ref-input-malformed") from None
        if not OID.fullmatch(oid) or not OID.fullmatch(old) or not remote.startswith("refs/heads/"):
            raise Refusal("unsupported-ref")
        if set(oid) == {"0"}:
            if local != "(delete)":
                raise Refusal("unsupported-ref")
        elif (local != "HEAD" and not local.startswith("refs/heads/")) or oid != head:
            raise Refusal("unchecked-ref-tree")
    return raw


def collection_required(paths: list[str]) -> bool:
    # Imports/parametrization may read docs, specs or frontend assets. Until a
    # complete executing dependency proof earns a skip, every push recollects.
    # This conservative envelope does not claim the hard under-30s metric.
    return True


def guard_plan(paths: list[str], *, root: Path | None = None) -> list[tuple[str, list[str]]]:
    python = sys.executable
    plan = [("lock", ["uv", "lock", "--check", "--offline"])]
    py = [
        name for name in paths if name.endswith(".py") and (root is None or (root / name).is_file())
    ]
    if py:
        plan += [
            ("ruff-check", [python, "-m", "ruff", "check", "--no-cache", *py]),
            ("ruff-format", [python, "-m", "ruff", "format", "--check", "--no-cache", *py]),
        ]
    for name, script in (
        ("em-dashes", "check-no-em-dashes.py"),
        ("spec-overwrites", "check_spec_overwrites.py"),
        ("countable-tasks", "check_countable_tasks.py"),
        ("duplicate-names", "check_duplicate_toplevel_names.py"),
        ("archived-landed", "check_archived_requirements_landed.py"),
        ("owner-emails", "check_owner_emails.py"),
        ("cited-requirements", "check_cited_requirements_resolve.py"),
        ("for-update-joins", "check_for_update_joins.py"),
    ):
        plan.append((name, [python, f"scripts/{script}"]))
    plan.append(("openspec-strict", ["openspec", "validate", "--all", "--strict"]))
    if collection_required(paths):
        plan.append(
            ("fresh-inventory-partition-budget", [python, "scripts/check_ci_test_shards.py"])
        )
    return plan


def run_checked(
    name: str,
    command: list[str],
    root: Path,
    *,
    timeout: float = CHECK_TIMEOUT,
    stdin=None,
    confined: bool = False,
) -> int:
    executable = shutil.which(command[0])
    if not executable:
        raise Refusal(f"{name}:tool-unavailable")
    command = [executable, *command[1:]]
    if confined:
        common = common_dir(root)
        launcher = (
            common / DISPATCHER / "pre_push_sandbox.py"
            if (common / STATE).exists()
            else root / "scripts/pre_push_sandbox.py"
        )
        command = [sys.executable, str(launcher), *command]
    environment = {
        **os.environ,
        "PYTHONDONTWRITEBYTECODE": "1",
        "UV_OFFLINE": "1",
        "OPENSPEC_TELEMETRY": "0",
    }
    process = subprocess.Popen(
        command,
        cwd=root,
        env=environment,
        stdin=stdin,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    try:
        process.communicate(timeout=timeout)
        if process.returncode:
            raise Refusal(
                f"{name}:exit-{process.returncode}",
                process.returncode if name == "managed-pre-push" else 1,
            )
        return 0
    except subprocess.TimeoutExpired:
        raise Refusal(f"{name}:timeout") from None
    finally:
        # Descendants with inherited pipes/DEVNULL must not outlive any outcome.
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
        for stream in (process.stdout, process.stderr):
            if stream:
                stream.close()


def tree_bodies(root: Path) -> dict:
    result = {}
    for name in git(root, "ls-files", "-z").decode().split("\0"):
        if name:
            path = root / name
            body = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
            result[name] = (body, path.lstat().st_mode & 0o777)
    return result


def check(root: Path, base: str, *, expected_head: str | None = None) -> dict:
    if git(root, "status", "--porcelain", "--untracked-files=no").strip():
        raise Refusal("uncommitted-tracked-source")
    extras = git(root, "ls-files", "--others", "--exclude-standard", "-z").decode().split("\0")
    if any(name and name != ".beads.gate.lock" for name in extras):
        raise Refusal("uncommitted-untracked-source")
    if (root / ".venv").is_symlink() or not (root / ".venv/bin/python").is_file():
        raise Refusal("own-python-environment-unavailable")
    head = git(root, "rev-parse", "HEAD").decode().strip()
    if expected_head is not None and head != expected_head:
        raise Refusal("checked-ref-head-changed")
    base_oid = git(root, "rev-parse", "--verify", f"{base}^{{commit}}").decode().strip()
    # No deleted .py is passed to ruff; global predicates/collection still run.
    paths = [
        name
        for name in git(root, "diff", "--name-only", "-z", base_oid, head).decode().split("\0")
        if name
    ]
    before = tree_bodies(root)
    try:
        for name, command in guard_plan(paths, root=root):
            run_checked(name, command, root, confined=True)
            print(f"pre-push checked: {name}", flush=True)
        run_checked(
            "session-links",
            [
                sys.executable,
                "scripts/session_link_guard.py",
                "--commit-range",
                f"{base_oid}..{head}",
            ],
            root,
            confined=True,
        )
    finally:
        if tree_bodies(root) != before or git(root, "rev-parse", "HEAD").decode().strip() != head:
            raise Refusal("guard-source-side-effect")
    return before


def hook(root: Path, args: list[str]) -> None:
    state_path = common_dir(root) / STATE
    try:
        state = json.loads(state_path.read_text())
        if (
            state["schema"] != 2
            or state["assets"] != asset_binding(root)
            or state["dispatcher"] != dispatcher_bodies(common_dir(root) / DISPATCHER)
        ):
            raise Refusal("installed-assets-changed")
    except (OSError, ValueError, KeyError, TypeError):
        raise Refusal("installation-state-unavailable") from None
    expected_head = git(root, "rev-parse", "HEAD").decode().strip()
    raw = validate_updates(sys.stdin.buffer.read(), expected_head)
    checked_source = check(root, "origin/main", expected_head=expected_head)

    def unchanged():
        return (
            git(root, "rev-parse", "HEAD").decode().strip() == expected_head
            and tree_bodies(root) == checked_source
        )

    if not unchanged():
        raise Refusal("pre-delegate-source-changed")
    # The original exact stream is replayed once to the actual managed asset.
    # Temporary storage is outside the tracked checkout and closes on signals.
    with tempfile.TemporaryFile() as carrier:
        carrier.write(raw)
        carrier.seek(0)
        try:
            status = run_checked(
                "managed-pre-push",
                [str(root / ".beads/hooks/pre-push"), *args],
                root,
                stdin=carrier,
                confined=True,
            )
            assert status == 0
        finally:
            if not unchanged():
                raise Refusal("managed-delegate-source-changed")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("install", "uninstall", "check", "hook"))
    parser.add_argument("--base", default="origin/main")
    parser.add_argument("hook_args", nargs="*")
    args = (
        parser.parse_args()
        if sys.argv[1:2] != ["hook"]
        else argparse.Namespace(mode="hook", base="origin/main", hook_args=sys.argv[2:])
    )
    root = Path(git(Path.cwd(), "rev-parse", "--show-toplevel").decode().strip())

    def interrupted(signum, frame):
        raise Refusal("interrupted")

    for sig in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, interrupted)
    try:
        if args.mode in ("install", "uninstall"):
            install(root, uninstall=args.mode == "uninstall")
        elif args.mode == "check":
            check(root, args.base)
        else:
            hook(root, args.hook_args)
        return 0
    except (Refusal, OSError) as error:
        print(
            f"pre-push refused: {error if isinstance(error, Refusal) else 'input-unavailable'}",
            file=sys.stderr,
        )
        return error.code if isinstance(error, Refusal) else 1


if __name__ == "__main__":
    raise SystemExit(main())
