"""Turn a finished investigation into a publication request for :class:`QaPublisher`.

The dispatcher, not the agent, chooses the target: the repository is the
publisher's fixed origin, the branch is the dispatcher-created QA branch, the
base is the trusted main commit the worktree was created from, and the head is
read once and then bound.  PR text is composed here from the finding and the
agent's ``INVESTIGATION_NOTES.md`` sections (never from ``evidence_lines``); the
publisher anonymizes and validates it before any egress.
"""

from __future__ import annotations

import asyncio
import logging
import re
import uuid
from pathlib import Path

from butlers.core.git_env import isolated_git_env
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


async def _git_output(repo_root: Path, *args: str) -> str:
    proc = await asyncio.create_subprocess_exec(
        "git",
        *args,
        cwd=str(repo_root),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
        env=isolated_git_env(),
    )
    stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=60)
    if proc.returncode != 0:
        raise ExportRefused("branch_unreadable")
    return stdout.decode("utf-8", errors="replace").strip()


async def resolve_branch_head(repo_root: Path, branch_name: str) -> tuple[Path, str]:
    """Return the shared object directory and the branch head, read once with hardened git."""
    common = await _git_output(repo_root, "rev-parse", "--path-format=absolute", "--git-common-dir")
    head = await _git_output(
        repo_root, "rev-parse", "--verify", f"refs/heads/{branch_name}^{{commit}}"
    )
    if not re.fullmatch(r"[0-9a-f]{40}", head):
        raise ExportRefused("branch_unreadable")
    return Path(common) / "objects", head


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
        objects_dir, head_sha = await resolve_branch_head(repo_root, branch_name)
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
