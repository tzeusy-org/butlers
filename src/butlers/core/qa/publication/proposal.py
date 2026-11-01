"""Turn a finished investigation into a publication request for :class:`QaPublisher`.

The dispatcher, not the agent, chooses the target: the repository is the
publisher's fixed origin, the branch is the dispatcher-created QA branch, the
base is the trusted main commit the worktree was created from, and the head is
read once and then bound.  PR text is composed here from the finding and the
agent's ``INVESTIGATION_NOTES.md`` sections (never from ``evidence_lines``); the
publisher anonymizes and validates it before any egress.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import uuid
from pathlib import Path

from butlers.core.qa.models import QaFinding
from butlers.core.qa.publication.binding import InvalidBinding, PublicationBinding
from butlers.core.qa.publication.export import (
    ExportRefused,
    GitIdentity,
    build_sealed_export,
)
from butlers.core.qa.publication.publisher import (
    PublicationResult,
    PublicationStatus,
    QaPublisher,
)
from butlers.core.qa.repo_whitelist import RepoWhitelist

logger = logging.getLogger(__name__)

#: Export refusals that mean agent-authored git content would leak.
_LEAK_CODES = frozenset({"content_leak", "message_leak", "author_metadata"})

_NOTES_FILE = Path(".tmp/qa-agent") / "INVESTIGATION_NOTES.md"
_NOTES_HEADER_TO_KEY = {
    "root cause": "root_cause",
    "fix summary": "fix_summary",
    "test coverage": "test_coverage",
}
_NOTES_PLACEHOLDER = "*(The investigation agent did not provide this section.)*"


def load_pr_notes(worktree_path: Path) -> dict[str, str]:
    """Parse the Root Cause / Fix Summary / Test Coverage sections (``{}`` if unreadable)."""
    try:
        raw = (worktree_path / _NOTES_FILE).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {}
    sections: dict[str, str] = {}
    key: str | None = None
    buffer: list[str] = []
    for line in [*raw.splitlines(), "## "]:
        if line.startswith("## "):
            if key is not None and (body := "\n".join(buffer).strip()):
                sections[key] = body
            key, buffer = _NOTES_HEADER_TO_KEY.get(line[3:].strip().lower()), []
        elif key is not None:
            buffer.append(line)
    return sections


def compose_pr_text(
    finding: QaFinding,
    attempt_id: uuid.UUID,
    notes: dict[str, str],
    dashboard_base_url: str | None,
) -> tuple[str, str]:
    """Raw PR title and body; the publisher sanitizes both before egress."""
    fp_short = finding.fingerprint[:12]
    link = ""
    if dashboard_base_url:
        url = f"{dashboard_base_url.rstrip('/')}/qa/investigations/{attempt_id}"
        link = f"\n\n[View investigation details]({url})"
    title = f"fix(qa): {finding.exception_type} in {finding.call_site} [{fp_short}]"
    body = f"""\
## QA Investigation Fix: {fp_short}

**Butler:** {finding.source_butler}
**Error:** {finding.exception_type}
**Call site:** {finding.call_site}
**Fingerprint:** `{finding.fingerprint}`
**Attempt ID:** `{attempt_id}`
**Discovery source:** {finding.source_type}
**Occurrences:** {finding.occurrence_count}

### Root Cause
{notes.get("root_cause", _NOTES_PLACEHOLDER)}

### Fix Summary
{notes.get("fix_summary", _NOTES_PLACEHOLDER)}

### Test Coverage
{notes.get("test_coverage", _NOTES_PLACEHOLDER)}

---
*Automated fix proposed by QA Staffer. Review carefully before merging.*{link}
"""
    return title, body


_SHA_RE = re.compile(rb"[0-9a-f]{40}")
_REF_COMPONENT_RE = re.compile(r"[A-Za-z0-9_][A-Za-z0-9._-]*")
_OPEN_FLAGS = os.O_RDONLY | os.O_NOFOLLOW | getattr(os, "O_CLOEXEC", 0)
_MAX_REF_FILE_BYTES = 64 * 1024 * 1024


def _branch_components(branch_name: str) -> list[str]:
    parts = branch_name.split("/")
    for part in parts:
        if not _REF_COMPONENT_RE.fullmatch(part) or part.endswith((".lock", ".")) or ".." in part:
            raise ExportRefused("branch_unreadable")
    return parts


def _read_no_follow(root: Path, parts: list[str]) -> bytes | None:
    """Read ``root/parts...`` refusing a symlink at any component; ``None`` when absent."""
    fds: list[int] = []
    try:
        fds.append(os.open(root, _OPEN_FLAGS | os.O_DIRECTORY))
        for part in parts[:-1]:
            fds.append(os.open(part, _OPEN_FLAGS | os.O_DIRECTORY, dir_fd=fds[-1]))
        # O_NONBLOCK: a planted FIFO must not block the open (and the event loop) forever;
        # it is a no-op for the regular file the S_ISREG check below insists on.
        fd = os.open(parts[-1], _OPEN_FLAGS | os.O_NONBLOCK, dir_fd=fds[-1])
        fds.append(fd)
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ExportRefused("branch_unreadable")
        with os.fdopen(os.dup(fd), "rb") as handle:
            data = handle.read(_MAX_REF_FILE_BYTES + 1)
        if len(data) > _MAX_REF_FILE_BYTES:
            raise ExportRefused("branch_unreadable")
        return data
    except FileNotFoundError:
        return None
    except OSError as exc:  # ELOOP (symlink), ENOTDIR, EACCES, ...
        raise ExportRefused("branch_unreadable") from exc
    finally:
        for fd in fds:
            os.close(fd)


def _packed_head(packed: bytes, refname: bytes) -> bytes | None:
    for line in packed.splitlines():
        if line.startswith((b"#", b"^")):
            continue
        sha, _, name = line.partition(b" ")
        if name == refname:
            return sha
    return None


def resolve_branch_head(repo_root: Path, branch_name: str) -> tuple[Path, str]:
    """Return the clone's object directory and the branch head, read once from ref files.

    No git process runs here: ``repo_root``'s ``.git`` is shared with the agent's linked
    worktree, so its config, hooks and includes are agent-writable.  The loose ref, then
    ``packed-refs``, is parsed directly; symlinks anywhere on the path, symbolic refs and
    anything but a 40-hex object id refuse.  The head is not trusted to be a commit here:
    :func:`build_sealed_export` re-hashes and type-checks every object it walks.
    """
    parts = _branch_components(branch_name)
    git_dir = repo_root / ".git"
    raw = _read_no_follow(git_dir, ["refs", "heads", *parts])
    if raw is not None:
        head = raw.strip()
    else:
        packed = _read_no_follow(git_dir, ["packed-refs"])
        refname = "/".join(["refs", "heads", *parts]).encode()
        head = _packed_head(packed, refname) if packed is not None else None
    if head is None or not _SHA_RE.fullmatch(head):
        raise ExportRefused("branch_unreadable")
    return git_dir / "objects", head.decode()


async def publish_investigation(
    publisher: QaPublisher,
    *,
    repo_root: Path,
    worktree_path: Path,
    attempt_id: uuid.UUID,
    finding: QaFinding,
    branch_name: str,
    base_sha: str | None,
    identity: GitIdentity | None,
    labels: list[str],
    whitelist: RepoWhitelist | None,
    dashboard_base_url: str | None,
) -> PublicationResult:
    """Seal the branch, bind it, and hand it to the publisher.  Never raises."""
    if base_sha is None or identity is None:
        # Without a trusted base or a bound author identity the export cannot be proven.
        return PublicationResult(PublicationStatus.REFUSED, "unbound_export")
    try:
        objects_dir, head_sha = resolve_branch_head(repo_root, branch_name)
        export = await build_sealed_export(
            objects_dir, base_sha=base_sha, head_sha=head_sha, identity=identity
        )
        binding = PublicationBinding(
            repository=publisher.repository,
            branch=branch_name,
            base_ref="main",
            head_sha=head_sha,
        )
    except ExportRefused as exc:
        status = (
            PublicationStatus.ANONYMIZATION_FAILED
            if exc.code in _LEAK_CODES
            else PublicationStatus.REFUSED
        )
        return PublicationResult(status, exc.code)
    except (InvalidBinding, OSError, TimeoutError):
        logger.warning("QA publication could not bind attempt=%s", attempt_id, exc_info=True)
        return PublicationResult(PublicationStatus.REFUSED, "unbound_export")

    title, body = compose_pr_text(
        finding, attempt_id, load_pr_notes(worktree_path), dashboard_base_url
    )
    try:
        return await publisher.publish(
            attempt_id=attempt_id,
            binding=binding,
            export=export,
            title=title,
            body=body,
            labels=labels,
            whitelist=whitelist,
        )
    except Exception:  # noqa: BLE001 — post-egress errors are recorded inside the publisher
        logger.warning("QA publisher raised before egress (attempt=%s)", attempt_id, exc_info=True)
        return PublicationResult(PublicationStatus.REFUSED, "publisher_error")
