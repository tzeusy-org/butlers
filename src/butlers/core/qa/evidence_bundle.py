"""The dispatcher-assembled evidence bundle an investigation cites (bu-ejow5p.13).

The investigator used to be told to correlate session ids it cannot read and then to write
evidence lines nobody could check. Instead the dispatcher, which holds database authority,
reads what it can observe about the finding, anonymizes it, assigns every item an id and a
digest, and hands the agent a read-only file. The agent has no database, log or tracing
access; the bundle is the only runtime data it sees.

The dispatcher keeps the authoritative copy (the manifest and bodies on
``qa_findings.structured_evidence.evidence_bundle``); the file in the agent's input area is a
convenience, never the thing a citation is later checked against.

Properties:

* Deterministic: ids are ``E1..En`` in a fixed section order and the digest covers the
  revision, every section status and every item, so the same inputs give the same digest.
* Fully anonymized: every body goes through the repository anonymizer and its validator. A
  body that cannot be made clean withholds the whole bundle (:class:`EvidenceWithheld`).
* Honest about gaps: a source that cannot be read marks its section ``unavailable``; the
  bundle never invents content. Items and total bytes are capped with an explicit marker.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import stat
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Final

import asyncpg

from butlers.core.git_env import isolated_git_env
from butlers.core.healing.anonymizer import anonymize, validate_anonymized
from butlers.core.qa.models import QaFinding
from butlers.core.qa.sources import log_scanner

logger = logging.getLogger(__name__)

BUNDLE_REVISION: Final[int] = 1
BUNDLE_FILE_NAME: Final[str] = "bundle.json"

MAX_SESSIONS: Final[int] = 5
MAX_TOOL_FAILURES: Final[int] = 10
MAX_LOG_LINES: Final[int] = 20
MAX_GIT_COMMITS: Final[int] = 10
MAX_OUTCOMES: Final[int] = 5
MAX_ITEM_CHARS: Final[int] = 1_500
#: Whole-bundle byte cap. A section that does not fit is cut and marked truncated.
MAX_BUNDLE_BYTES: Final[int] = 64 * 1024
LOG_WINDOW_PADDING: Final[timedelta] = timedelta(minutes=1)
GIT_TIMEOUT_S: Final[float] = 15.0

SECTION_ORDER: Final[tuple[str, ...]] = (
    "sessions",
    "tool_failures",
    "log_lines",
    "call_site_history",
    "recent_outcomes",
)
_TRUNCATION_MARKER = " ...[truncated]"
#: A repository-relative file path with a line number or none: ``src/butlers/x.py:123``.
_CALL_SITE_PATH = re.compile(
    r"^(?P<path>[A-Za-z0-9_][A-Za-z0-9_./-]{0,200}\.[A-Za-z0-9]{1,8})(?::\d+)?$"
)


class EvidenceWithheld(RuntimeError):
    """A body could not be anonymized cleanly, so no bundle may reach the agent."""


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    id: str
    kind: str
    text: str
    digest: str


@dataclass(frozen=True, slots=True)
class EvidenceSection:
    name: str
    status: str  # available | empty | unavailable | truncated
    items: tuple[EvidenceItem, ...] = ()


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    sections: tuple[EvidenceSection, ...]
    digest: str
    revision: int = BUNDLE_REVISION
    _by_id: dict[str, EvidenceItem] = field(default_factory=dict, compare=False, repr=False)

    @property
    def items(self) -> tuple[EvidenceItem, ...]:
        return tuple(item for section in self.sections for item in section.items)

    def item(self, ref: str) -> EvidenceItem | None:
        return {item.id: item for item in self.items}.get(ref)

    def manifest(self) -> dict[str, Any]:
        """Revision, digest and per-item ids and digests: what survives body retention."""
        return {
            "revision": self.revision,
            "digest": self.digest,
            "sections": [
                {
                    "name": section.name,
                    "status": section.status,
                    "items": [
                        {"id": item.id, "kind": item.kind, "digest": item.digest}
                        for item in section.items
                    ],
                }
                for section in self.sections
            ],
        }

    def to_dict(self) -> dict[str, Any]:
        """The full anonymized bundle, bodies included."""
        return {
            "revision": self.revision,
            "digest": self.digest,
            "sections": [
                {
                    "name": section.name,
                    "status": section.status,
                    "items": [
                        {
                            "id": item.id,
                            "kind": item.kind,
                            "digest": item.digest,
                            "text": item.text,
                        }
                        for item in section.items
                    ],
                }
                for section in self.sections
            ],
        }


def _digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _bundle_digest(sections: tuple[EvidenceSection, ...]) -> str:
    canonical = [
        [section.name, section.status, [[item.id, item.kind, item.text] for item in section.items]]
        for section in sections
    ]
    encoded = json.dumps([BUNDLE_REVISION, canonical], sort_keys=True, ensure_ascii=True)
    return _digest(encoded)


def _clean(text: str, repo_root: Path) -> str:
    """Anonymize one body, capped, or withhold the bundle if it cannot be made clean."""
    safe = anonymize(text, repo_root)
    if len(safe) > MAX_ITEM_CHARS:
        safe = safe[: MAX_ITEM_CHARS - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER
    clean, _violations = validate_anonymized(safe)
    if not clean:
        raise EvidenceWithheld("an evidence item still carried sensitive content")
    return safe


def _session_ids(finding: QaFinding) -> list[uuid.UUID]:
    raw = (finding.structured_evidence or {}).get("session_ids")
    ids: list[uuid.UUID] = []
    for value in raw if isinstance(raw, list) else []:
        try:
            ids.append(uuid.UUID(str(value)))
        except ValueError:
            continue
    return ids[:MAX_SESSIONS]


def _seconds(started: datetime | None, completed: datetime | None) -> str:
    if started is None or completed is None:
        return "unknown"
    return f"{(completed - started).total_seconds():.1f}s"


async def _sessions(pool: asyncpg.Pool, finding: QaFinding, repo_root: Path) -> list[str]:
    ids = _session_ids(finding)
    if not ids:
        return []
    rows = await pool.fetch(
        """
        SELECT source_butler, session_id, error, status, trigger_source, started_at, completed_at
        FROM public.v_qa_recent_failures
        WHERE session_id = ANY($1::uuid[])
        ORDER BY completed_at DESC, session_id
        LIMIT $2
        """,
        ids,
        MAX_SESSIONS,
    )
    return [
        _clean(
            f"session {row['session_id']} butler={row['source_butler']} status={row['status']}"
            f" trigger={row['trigger_source'] or 'unknown'}"
            f" duration={_seconds(row['started_at'], row['completed_at'])}"
            f" error={row['error'] or 'none'}",
            repo_root,
        )
        for row in rows
    ]


async def _tool_failures(pool: asyncpg.Pool, finding: QaFinding, repo_root: Path) -> list[str]:
    ids = _session_ids(finding)
    if not ids:
        return []
    rows = await pool.fetch(
        """
        SELECT source_butler, session_id, tool_name, module_name, error
        FROM public.v_qa_tool_call_failures
        WHERE session_id = ANY($1::uuid[])
        ORDER BY completed_at DESC, session_id, tool_name
        LIMIT $2
        """,
        ids,
        MAX_TOOL_FAILURES,
    )
    return [
        _clean(
            f"tool call failed in session {row['session_id']} butler={row['source_butler']}"
            f" tool={row['tool_name']} module={row['module_name'] or 'unknown'}"
            f" error={row['error'] or 'none'}",
            repo_root,
        )
        for row in rows
    ]


async def _log_lines(finding: QaFinding, repo_root: Path, log_root: Path | None) -> list[str]:
    log_stem = (finding.structured_evidence or {}).get("log_file")
    if finding.source_type != "log_scanner" or not isinstance(log_stem, str):
        return []
    start = finding.first_seen - LOG_WINDOW_PADDING
    end = finding.last_seen + LOG_WINDOW_PADDING
    lines = await asyncio.to_thread(
        log_scanner.read_fingerprint_lines,
        log_stem,
        finding.fingerprint,
        start,
        end,
        MAX_LOG_LINES,
        log_root,
    )
    return [_clean(line, repo_root) for line in lines]


def _call_site_path(call_site: str) -> str | None:
    match = _CALL_SITE_PATH.fullmatch(call_site.strip())
    if match is None:
        return None
    path = match.group("path")
    if ".." in path.split("/") or path.startswith("/"):
        return None
    return path


async def _git_history(repo_root: Path, finding: QaFinding) -> list[str]:
    """``git log -n10`` for the call-site file, run on the trusted repo with a pinned argv.

    Never a shell, never the agent's clone: ``repo_root`` is the dispatcher's own checkout.
    The environment is the hardened allowlist (no hooks, fsmonitor, credential helper or
    system/global config) with ``HOME`` pointing at an empty directory, and the log never
    runs external diff or textconv drivers.
    """
    path = _call_site_path(finding.call_site)
    if path is None or not (repo_root / path).is_file():
        return []
    with tempfile.TemporaryDirectory(prefix="qa-evidence-home-") as home:
        env = isolated_git_env()
        env["HOME"] = home
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        process = await asyncio.create_subprocess_exec(
            "git",
            "-C",
            str(repo_root),
            "log",
            f"-n{MAX_GIT_COMMITS}",
            "--no-color",
            "--no-ext-diff",
            "--no-textconv",
            "--format=%h %cs %s",
            "--",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
            env=env,
        )
        try:
            stdout, _ = await asyncio.wait_for(process.communicate(), timeout=GIT_TIMEOUT_S)
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
    if process.returncode != 0:
        raise RuntimeError("git log failed")
    return [
        f"{path}: {line}"
        for line in stdout.decode("utf-8", errors="replace").splitlines()[:MAX_GIT_COMMITS]
    ]


async def _outcomes(
    pool: asyncpg.Pool, finding: QaFinding, attempt_id: uuid.UUID, repo_root: Path
) -> list[str]:
    rows = await pool.fetch(
        """
        SELECT status, error_detail, closed_at
        FROM public.healing_attempts
        WHERE qa_patrol_id IS NOT NULL AND butler_name = $1 AND id <> $2
        ORDER BY created_at DESC
        LIMIT $3
        """,
        finding.source_butler,
        attempt_id,
        MAX_OUTCOMES,
    )
    return [
        _clean(
            f"earlier QA attempt for butler={finding.source_butler} status={row['status']}"
            f" closed={row['closed_at'].isoformat() if row['closed_at'] else 'open'}"
            f" detail={row['error_detail'] or 'none'}",
            repo_root,
        )
        for row in rows
    ]


_KINDS: Final[dict[str, str]] = {
    "sessions": "session",
    "tool_failures": "tool_failure",
    "log_lines": "log_line",
    "call_site_history": "git_commit",
    "recent_outcomes": "dispatch_outcome",
}


async def build_evidence_bundle(
    pool: asyncpg.Pool,
    finding: QaFinding,
    *,
    attempt_id: uuid.UUID,
    repo_root: Path,
    log_root: Path | None = None,
    git_history: Callable[[Path, QaFinding], Awaitable[list[str]]] = _git_history,
) -> EvidenceBundle:
    """Assemble, anonymize and digest the bundle for one investigation.

    Raises :class:`EvidenceWithheld` only when a body cannot be anonymized cleanly; every
    other source failure marks its section ``unavailable`` and the bundle still builds.
    """
    readers: dict[str, Callable[[], Awaitable[list[str]]]] = {
        "sessions": lambda: _sessions(pool, finding, repo_root),
        "tool_failures": lambda: _tool_failures(pool, finding, repo_root),
        "log_lines": lambda: _log_lines(finding, repo_root, log_root),
        "call_site_history": lambda: _anonymized(git_history(repo_root, finding), repo_root),
        "recent_outcomes": lambda: _outcomes(pool, finding, attempt_id, repo_root),
    }
    sections: list[EvidenceSection] = []
    counter = 0
    budget = MAX_BUNDLE_BYTES
    for name in SECTION_ORDER:
        try:
            texts = await readers[name]()
        except EvidenceWithheld:
            raise
        except Exception as exc:  # noqa: BLE001 — a failed source must not stop the investigation
            # The class name only: a driver or parser error can echo the content it read.
            logger.warning("Evidence bundle section %s unavailable: %s", name, type(exc).__name__)
            sections.append(EvidenceSection(name, "unavailable"))
            continue
        items: list[EvidenceItem] = []
        truncated = False
        for text in texts:
            size = len(text.encode("utf-8"))
            if size > budget:
                truncated = True
                break
            budget -= size
            counter += 1
            items.append(EvidenceItem(f"E{counter}", _KINDS[name], text, _digest(text)))
        status = "truncated" if truncated else ("available" if items else "empty")
        sections.append(EvidenceSection(name, status, tuple(items)))
    frozen = tuple(sections)
    return EvidenceBundle(sections=frozen, digest=_bundle_digest(frozen))


async def _anonymized(source: Awaitable[list[str]], repo_root: Path) -> list[str]:
    return [_clean(line, repo_root) for line in await source]


def write_bundle_file(bundle: EvidenceBundle, directory: Path) -> Path:
    """Write the bundle for the agent to read, owner-writable only (``0444`` file, ``0555`` dir).

    Where the agent runs as a different user (the Job pod) that is read-only to it. It is a
    convenience copy: the dispatcher's own record is what citations are checked against.
    """
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / BUNDLE_FILE_NAME
    descriptor, temporary = tempfile.mkstemp(dir=directory, prefix=".bundle-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(bundle.to_dict(), handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.chmod(temporary, stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
    os.chmod(
        directory,
        stat.S_IRUSR | stat.S_IXUSR | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH,
    )
    return path


async def persist_bundle(pool: asyncpg.Pool, attempt_id: uuid.UUID, bundle: EvidenceBundle) -> None:
    """Record the bundle on the attempt's finding before launch (the authoritative copy).

    The manifest (ids, kinds, digests) is permanent; ``bodies`` is the anonymized text, held
    under the same retention as the notes' evidence lines.
    """
    await pool.execute(
        """
        UPDATE public.qa_findings
        SET structured_evidence = COALESCE(structured_evidence, '{}'::jsonb)
                || jsonb_build_object('evidence_bundle', $2::jsonb)
        WHERE healing_attempt_id = $1
        """,
        attempt_id,
        {"manifest": bundle.manifest(), "bodies": bundle.to_dict()},
    )
