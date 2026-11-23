"""Daemon-side plumbing for a per-attempt isolated QA investigation (bu-ejow5p.11).

``JobIsolation`` is the dispatcher's whole view of the isolation facility: where the exchange
volume is mounted in the daemon, which Job template to render, and how to run it. With it the
dispatcher

- makes an *independent* clone for the attempt (no shared ``.git``, no hardlinks, no remote), so
  nothing the agent writes can reach the daemon's repository and nothing it pushes can leave;
- builds the typed ``QaJobLaunch`` the runtime adapter runs in a Job;
- imports the few pod-written files it needs (notes, the unfixable sentinel) into a
  daemon-owned scratch directory with no-follow, bounded reads, so later steps never touch a
  pod-controlled path; and
- removes the attempt directory when the attempt ends.

The daemon never runs git inside the pod-written clone afterwards: sealing parses its object
store in-process (``publication.export``) and no-op detection reads the branch ref directly.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import stat
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from pydantic import BaseModel, Field, field_validator

from butlers.core.git_env import isolated_git_env
from butlers.core.qa import investigator_job as ij
from butlers.core.runtimes.base import JobRunner, QaJobLaunch

logger = logging.getLogger(__name__)

CLONE_DIR = "repo"
SCRATCH_DIR = "scratch"
_ARTIFACT_CAP = 1 << 20


class AttemptJobView(Protocol):
    """Read-only view of one Job; read-only so a frozen dataclass satisfies it."""

    @property
    def name(self) -> str: ...

    @property
    def attempt_id(self) -> str | None: ...

    @property
    def terminal(self) -> bool: ...


class JobFleet(JobRunner, Protocol):
    """A runner that can also list and delete the investigator Jobs it launched."""

    async def list_attempt_jobs(self, release: str) -> Sequence[AttemptJobView]: ...

    async def delete_job(self, name: str) -> bool: ...


#: Attempts whose investigation coroutine is running in this process. Everything else found in
#: the cluster or the exchange is an orphan of an earlier run.
LIVE_ATTEMPTS: set[str] = set()


class JobIsolationSettings(BaseModel):
    """``[modules.qa.job_isolation]``: absent means in-daemon worktrees, as before."""

    image: str = Field(description="daemon image by digest: repo@sha256:<64 hex>")
    namespace: str | None = None
    release: str = "butlers"
    exchange_claim: str = "butlers-qa-exchange"
    exchange_root: Path = Path("/var/lib/butlers/qa-exchange")
    model_proxy_url: str | None = None

    @field_validator("image")
    @classmethod
    def _digest_pinned(cls, value: str) -> str:
        import re

        if not re.search(r"@sha256:[0-9a-f]{64}$", value):
            raise ValueError("image must be pinned by a sha256 digest")
        return value


@dataclass(frozen=True)
class SweepReport:
    jobs_deleted: int
    jobs_undeleted: int
    directories_removed: int


@dataclass(frozen=True)
class JobIsolation:
    """Everything the dispatcher needs to run attempts in per-attempt Jobs."""

    namespace: str
    release: str
    image: str
    exchange_claim: str
    #: Where the exchange volume is mounted in the daemon; attempts live at ``attempts/<uuid>``.
    exchange_root: Path
    runner: JobFleet
    model_proxy_url: str | None = None
    uid: int = ij.INVESTIGATOR_UID

    def attempt_dir(self, attempt_id: uuid.UUID) -> Path:
        return self.exchange_root / ij.exchange_subpath(attempt_id)

    def clone_dir(self, attempt_id: uuid.UUID) -> Path:
        return self.attempt_dir(attempt_id) / CLONE_DIR

    def scratch_dir(self, attempt_id: uuid.UUID) -> Path:
        """Daemon-owned scratch: lives beside, not inside, the pod-writable attempt directory."""
        return self.exchange_root / "scratch" / str(attempt_id)

    def launch(self, attempt_id: uuid.UUID, *, deadline_s: int) -> QaJobLaunch:
        def build(command: Sequence[str]) -> dict[str, Any]:
            return ij.build_investigator_job(
                attempt_id=attempt_id,
                release=self.release,
                namespace=self.namespace,
                image=self.image,
                exchange_claim=self.exchange_claim,
                command=command,
                deadline_s=deadline_s,
                model_proxy_url=self.model_proxy_url,
                working_dir=f"{ij.WORKSPACE_MOUNT}/{CLONE_DIR}",
            )

        return QaJobLaunch(
            attempt_id=str(attempt_id),
            staging_dir=self.attempt_dir(attempt_id),
            workspace_mount=ij.WORKSPACE_MOUNT,
            workspace_uid=self.uid,
            deadline_s=deadline_s,
            runner=self.runner,
            build_manifest=build,
            model_proxy_url=self.model_proxy_url,
        )


async def _git(*args: str, cwd: Path | None = None) -> None:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(cwd) if cwd else None,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.PIPE,
        env=isolated_git_env(),
    )
    _, stderr = await proc.communicate()
    if proc.returncode:
        raise RuntimeError(
            f"git {args[0]} failed: {stderr.decode('utf-8', errors='replace').strip()[:300]}"
        )


async def prepare_attempt_clone(
    iso: JobIsolation,
    *,
    attempt_id: uuid.UUID,
    trusted_repo: Path,
    base_sha: str,
    branch_name: str,
    author: tuple[str, str] | None,
) -> Path:
    """Create the attempt's independent clone at ``base_sha`` on ``branch_name``.

    The source is the daemon's trusted managed clone, read-only from here. ``--no-hardlinks``
    copies objects, so the pod cannot alter the source through a shared inode; the remote is
    removed so the agent has nothing to push to. Ownership then passes to the investigator UID.
    """
    attempt_dir = iso.attempt_dir(attempt_id)
    clone = iso.clone_dir(attempt_id)
    attempt_dir.mkdir(parents=True, exist_ok=False, mode=0o755)
    await _git("clone", "--no-hardlinks", "--no-checkout", str(trusted_repo), str(clone))
    await _git("-C", str(clone), "checkout", "-q", "-b", branch_name, base_sha)
    await _git("-C", str(clone), "remote", "remove", "origin")
    if author is not None:
        await _git("-C", str(clone), "config", "user.name", author[0])
        await _git("-C", str(clone), "config", "user.email", author[1])
    await asyncio.to_thread(_hand_over, attempt_dir, iso.uid)
    return clone


def _hand_over(root: Path, uid: int) -> None:
    """Give the pod user ownership of the attempt tree without following any link."""
    if os.geteuid() != 0:
        for path in (root, *root.rglob("*")):
            if not path.is_symlink():
                path.chmod(path.stat().st_mode | 0o700 | (0o077 if path.is_dir() else 0o066))
        return
    for current, dirs, files in os.walk(root, followlinks=False):
        for name in (*dirs, *files):
            os.chown(Path(current) / name, uid, uid, follow_symlinks=False)
    os.chown(root, uid, uid, follow_symlinks=False)


def read_untrusted(root: Path, parts: Sequence[str], *, cap: int = _ARTIFACT_CAP) -> bytes | None:
    """Read ``root/parts`` as untrusted data: every component opened ``O_NOFOLLOW``, bounded.

    Returns ``None`` when it is missing, is not a regular file, or any component is a link.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW
    fds: list[int] = []
    try:
        current = os.open(root, flags | os.O_DIRECTORY)
        fds.append(current)
        for part in parts[:-1]:
            current = os.open(part, flags | os.O_DIRECTORY, dir_fd=current)
            fds.append(current)
        fd = os.open(parts[-1], flags | os.O_NONBLOCK, dir_fd=current)
    except OSError:
        return None
    finally:
        for descriptor in fds:
            os.close(descriptor)
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        # A single-link regular file: a hard link to a daemon file would read as the pod's own.
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            return None
        return handle.read(cap)


def import_artifacts(
    iso: JobIsolation, attempt_id: uuid.UUID, relative_paths: Sequence[Path | str]
) -> Path:
    """Copy named pod-written files into the daemon-owned scratch directory, safely.

    Returns the scratch directory; a file that is absent, linked or not regular is simply not
    copied, which every consumer already treats as "the agent did not provide it".
    """
    scratch = iso.scratch_dir(attempt_id)
    scratch.mkdir(parents=True, exist_ok=True, mode=0o700)
    source = iso.clone_dir(attempt_id)
    for raw in relative_paths:
        relative = Path(raw)
        data = read_untrusted(source, relative.parts)
        if data is None:
            continue
        target = scratch / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    return scratch


def _open_directory(name: str | Path, dir_fd: int | None) -> int:
    """Open a real directory without following a link, even one the pod chmodded to 000.

    ``O_RDONLY`` first; a 000 directory refuses that, so fall back to ``O_PATH``, which needs no
    permission on the directory itself. The fd names the inode, so what is chmodded next cannot
    be swapped for a link by a pod that is still running.
    """
    flags = os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        return os.open(name, os.O_RDONLY | flags, dir_fd=dir_fd)
    except PermissionError:
        return os.open(name, os.O_PATH | flags, dir_fd=dir_fd)


def _reopen_directories(root: str | Path) -> None:
    """Make every real directory under ``root`` owner-accessible, by fd; never follows a link."""
    try:
        fd = _open_directory(root, None)
    except OSError:
        return
    stack = [fd]
    while stack:
        current = stack.pop()
        readable = -1
        try:
            mode = os.fstat(current).st_mode | 0o700
            try:
                os.fchmod(current, mode)
            except OSError:
                os.chmod(f"/proc/self/fd/{current}", mode)  # an O_PATH fd cannot be fchmodded
            # Re-open "." of the same inode for reading (an O_PATH fd cannot be scanned).
            readable = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC, dir_fd=current)
            with os.scandir(readable) as entries:
                names = [entry.name for entry in entries if entry.is_dir(follow_symlinks=False)]
            for name in names:
                try:
                    stack.append(_open_directory(name, readable))
                except OSError:
                    continue
        except OSError:
            continue
        finally:
            if readable >= 0:
                os.close(readable)
            os.close(current)


def remove_tree(path: str | Path) -> None:
    """Remove an untrusted tree without following links; tolerates pod-chmodded directories.

    Directories are reopened only when the first removal fails, so no tree is left more open
    than it already was while it waits for removal.
    """
    try:
        shutil.rmtree(path)
    except PermissionError:
        _reopen_directories(path)
        shutil.rmtree(path)


def cleanup_attempt(iso: JobIsolation, attempt_id: uuid.UUID) -> None:
    """Remove the attempt's exchange directory and scratch; never follows a link, never raises."""
    for path in (iso.attempt_dir(attempt_id), iso.scratch_dir(attempt_id)):
        try:
            remove_tree(path)
        except FileNotFoundError:
            pass
        except OSError:
            logger.warning("QA attempt directory cleanup failed (attempt=%s)", attempt_id)


async def active_job_count(iso: JobIsolation) -> int | None:
    """Investigator Jobs the cluster says are not terminal; ``None`` if it cannot be read.

    The dispatcher counts these next to its own rows, so a daemon restart that orphaned a running
    Job cannot be followed by a full complement of new ones. ``None`` is treated as "at cap".
    """
    try:
        jobs = await iso.runner.list_attempt_jobs(iso.release)
    except Exception:  # noqa: BLE001 - an unreadable fleet must not read as an empty one
        logger.warning("QA investigator fleet unreadable; treating the concurrency cap as reached")
        return None
    return sum(1 for job in jobs if not job.terminal)


async def sweep_orphans(iso: JobIsolation, *, live: set[str] | None = None) -> SweepReport:
    """Delete Jobs and attempt directories that no running investigation in this process owns.

    Meant for daemon startup, before any attempt runs: whatever exists then is an orphan of a
    crashed or restarted run (its coroutine, and so its seal and cleanup, is gone). A Job whose
    delete fails is counted, not retried here; the next startup sweeps it again.
    """
    owned = LIVE_ATTEMPTS if live is None else live
    deleted = undeleted = removed = 0
    try:
        jobs = list(await iso.runner.list_attempt_jobs(iso.release))
    except Exception:  # noqa: BLE001
        logger.warning("QA orphan sweep: Jobs unreadable; skipping the Job half")
        jobs = []
    for job in jobs:
        if job.attempt_id in owned:
            continue
        if await iso.runner.delete_job(job.name):
            deleted += 1
        else:
            undeleted += 1
    for root in (iso.exchange_root / "attempts", iso.exchange_root / "scratch"):
        try:
            entries = list(os.scandir(root))
        except OSError:
            continue
        for entry in entries:
            if entry.name in owned:
                continue
            try:
                if entry.is_symlink() or not entry.is_dir(follow_symlinks=False):
                    os.unlink(entry.path)
                else:
                    remove_tree(entry.path)
                removed += 1
            except OSError:
                logger.warning("QA orphan sweep: could not remove an attempt directory")
    return SweepReport(deleted, undeleted, removed)
