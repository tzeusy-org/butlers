"""Roster-backed ``GuidanceSource``s (RFC 0038 D1, D2, D8): butler guidance and the lead policy.

A domain butler's ``roster/<butler>/CLAUDE.md`` and its skills
(``roster/<butler>/.claude/skills/<skill>/SKILL.md``) become that domain's guidance
documents; ``roster/switchboard/goal-policy/LEAD.md`` is the lead policy. They are
git-reviewed, owner-authored configuration, so each is served with authority ``owner`` and
untainted, under a content-addressed version (``sha256:<16 hex>`` of the served text).

Document names, per domain:

* ``CLAUDE.md``: the butler's guidance, with whole-line ``@<file>.md`` includes resolved
  (as the CLI does) and every ``Notes to self`` section removed. Those notes are written by
  sessions, not the owner (D2 moves them to an ``owner_authority`` record capability), so
  they are never served as owner text.
* ``skills/<skill>``: one skill's ``SKILL.md``.
* ``LEAD.md`` (domain ``lead``): the lead policy.

Every read is confined to the roster directory: a path is resolved (symlinks included)
and refused unless it stays inside the roster root, only ``.md`` files are read, an env or
secret-looking name is never read, and a file over the size bound is omitted, never served
cut. Names are looked up in the set this adapter enumerates, so a name can never become a
path. Reads are fresh each call, so an edit shows up as a new version; nothing is cached or
recorded.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from butlers.capability_broker.service import Broker

from .guidance import (
    LEAD_POLICY_DOMAIN,
    Guidance,
    GuidanceItem,
    GuidanceLimits,
    GuidanceReader,
)

logger = logging.getLogger(__name__)

CLAUDE_MD = "CLAUDE.md"
LEAD_POLICY = "LEAD.md"
LEAD_POLICY_PATH = Path("switchboard") / "goal-policy" / LEAD_POLICY

_BUTLER = re.compile(r"^[a-z][a-z0-9_]{0,31}$")
_SKILL = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")
_INCLUDE = re.compile(r"^@(\S+\.md)\s*$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_WORD = re.compile(r"[a-z0-9]+")
_SECRETISH = re.compile(r"(^\.env|secret|credential|token|password|\.pem$|\.key$)", re.I)
_NOT_DOMAINS = frozenset({"shared"})
_EXPANSION = 8  # raw expansion (includes, notes still in) may reach this many served bounds


@dataclass(frozen=True, slots=True)
class RosterGuidanceLimits:
    """Bounds on what one document may be; every value positive."""

    document_bytes: int = 64 * 1024
    include_depth: int = 3

    def __post_init__(self) -> None:
        if (
            type(self.document_bytes) is not int
            or type(self.include_depth) is not int
            or self.document_bytes <= 0
            or self.include_depth < 0
        ):
            raise ValueError("roster guidance limits must be positive")


class _Refused(Exception):
    """A file this adapter will not serve; the document is omitted."""


class _Reader:
    """Confined reads under one roster root."""

    def __init__(self, root: Path, limits: RosterGuidanceLimits):
        self.root = root.resolve(strict=True)
        self.limits = limits

    def path(self, candidate: Path) -> Path:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            raise _Refused("missing") from None
        if not resolved.is_relative_to(self.root) or not resolved.is_file():
            raise _Refused("outside")
        if resolved.suffix != ".md" or _SECRETISH.search(resolved.name):
            raise _Refused("kind")
        return resolved

    def text(self, path: Path) -> str:
        with path.open("rb") as handle:
            data = handle.read(self.limits.document_bytes + 1)
        if len(data) > self.limits.document_bytes:
            raise _Refused("size")
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            raise _Refused("encoding") from None

    def expanded(self, path: Path, depth: int = 0, seen: frozenset[Path] = frozenset()) -> str:
        """The file with its whole-line ``@<file>.md`` includes resolved, inside the root."""
        lines = []
        for line in self.text(path).splitlines():
            include = _INCLUDE.match(line.strip())
            if include is None:
                lines.append(line)
                continue
            if depth >= self.limits.include_depth:
                raise _Refused("include_depth")
            target = self.path(path.parent / include.group(1))
            if target in seen or target == path:
                raise _Refused("include_cycle")
            lines.append(self.expanded(target, depth + 1, seen | {path}))
        text = "\n".join(lines)
        # The served bound applies after notes are stripped (``_served``); this only stops a
        # fan-out of includes from growing without bound before then.
        if len(text.encode("utf-8")) > _EXPANSION * self.limits.document_bytes:
            raise _Refused("size")
        return text

    def served(self, text: str) -> str:
        """The bound on what is served: a document over it is omitted, never cut."""
        if len(text.encode("utf-8")) > self.limits.document_bytes:
            raise _Refused("size")
        return text


def _without_notes(text: str) -> str:
    """Drop every ``Notes to self`` section: session-written, so not owner guidance (D2)."""
    kept, skipping, fenced = [], 0, False
    for line in text.splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced  # a ``#`` line inside a fence is code, not a heading
        heading = None if fenced else _HEADING.match(line)
        if heading is not None:
            level = len(heading.group(1))
            if skipping and level <= skipping:
                skipping = 0
            if not skipping and heading.group(2).strip().lower() == "notes to self":
                skipping = level
                continue
        if not skipping:
            kept.append(line)
    return "\n".join(kept).strip() + "\n"


def _version(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _item(name: str, title: str, text: str) -> GuidanceItem:
    return GuidanceItem(name, title, _version(text), text, authority="owner", tainted=False)


def _skill_title(skill: str, text: str) -> str:
    match = re.search(r"^description:\s*(.+)$", text, re.M)
    return f"{skill}: {match.group(1).strip()}" if match else skill


def _terms(text: str) -> list[str]:
    return _WORD.findall(text.lower())


def rank(items: Iterable[GuidanceItem], query: str, k: int) -> tuple[GuidanceItem, ...]:
    """Term-frequency ranking: a name or title hit weighs three body hits; no hit, no rank."""
    terms = set(_terms(query))
    scored = []
    for item in items:
        head = _terms(f"{item.name} {item.title}")
        body = _terms(item.text)
        score = sum(3 * head.count(term) + body.count(term) for term in terms)
        if score:
            scored.append((-score, item.name, item))
    scored.sort(key=lambda entry: entry[:2])
    return tuple(item for _, _, item in scored[:k])


class _Documents:
    """A guidance source over a fixed, enumerated set of documents."""

    def documents(self) -> dict[str, GuidanceItem]:
        raise NotImplementedError

    async def search(self, query: str, k: int, reader: GuidanceReader) -> tuple[GuidanceItem, ...]:
        documents = await asyncio.to_thread(self.documents)
        return rank(documents.values(), query, k)

    async def load(self, name: str, reader: GuidanceReader) -> GuidanceItem | None:
        documents = await asyncio.to_thread(self.documents)
        return documents.get(name)  # a name is a key, never a path


class ButlerGuidance(_Documents):
    """One domain butler's ``CLAUDE.md`` and skills, read fresh on every call."""

    def __init__(
        self, roster: Path, butler: str, limits: RosterGuidanceLimits | None = None
    ) -> None:
        if not _BUTLER.match(butler) or butler == LEAD_POLICY_DOMAIN:
            raise ValueError("butler name")
        self.butler = butler
        self._reader = _Reader(roster, limits or RosterGuidanceLimits())
        self._home = self._reader.root / butler

    def documents(self) -> dict[str, GuidanceItem]:
        found: dict[str, GuidanceItem] = {}
        self._add(found, CLAUDE_MD, f"{self.butler} guidance", self._home / CLAUDE_MD)
        skills = self._home / ".claude" / "skills"
        if skills.is_dir() and skills.resolve().is_relative_to(self._reader.root):
            for entry in sorted(skills.iterdir()):
                if _SKILL.match(entry.name):
                    self._add(found, f"skills/{entry.name}", entry.name, entry / "SKILL.md")
        return found

    def _add(self, found: dict[str, GuidanceItem], name: str, title: str, path: Path) -> None:
        try:
            resolved = self._reader.path(path)
        except _Refused as refused:
            if str(refused) != "missing":  # no file is no document; anything else is odd
                logger.info("roster guidance omitted butler=%s name=%s", self.butler, name)
            return
        try:
            text = self._reader.served(_without_notes(self._reader.expanded(resolved)))
        except _Refused as refused:
            logger.info(
                "roster guidance omitted butler=%s name=%s reason=%s", self.butler, name, refused
            )
            return
        if name.startswith("skills/"):
            title = _skill_title(title, text)
        found[name] = _item(name, title, text)


class LeadPolicyGuidance(_Documents):
    """The lead policy, ``roster/switchboard/goal-policy/LEAD.md`` (D1), as ``lead:LEAD.md``."""

    def __init__(self, roster: Path, limits: RosterGuidanceLimits | None = None) -> None:
        self._reader = _Reader(roster, limits or RosterGuidanceLimits())

    def documents(self) -> dict[str, GuidanceItem]:
        try:
            path = self._reader.path(self._reader.root / LEAD_POLICY_PATH)
            text = self._reader.served(self._reader.expanded(path).strip() + "\n")
        except _Refused:
            return {}
        return {LEAD_POLICY: _item(LEAD_POLICY, "lead policy", text)}


def roster_guidance_sources(
    roster: Path, limits: RosterGuidanceLimits | None = None
) -> dict[str, ButlerGuidance]:
    """One source per domain butler under ``roster`` (a directory with a ``butler.toml``)."""
    root = Path(roster).resolve(strict=True)
    sources = {}
    for entry in sorted(root.iterdir()):
        if (
            entry.name not in _NOT_DOMAINS
            and _BUTLER.match(entry.name)
            and entry.is_dir()
            and not entry.is_symlink()
            and (entry / "butler.toml").is_file()
        ):
            sources[entry.name] = ButlerGuidance(root, entry.name, limits)
    return sources


def roster_guidance(
    broker: Broker,
    roster: Path,
    limits: GuidanceLimits | None = None,
    roster_limits: RosterGuidanceLimits | None = None,
) -> Guidance:
    """The guidance tools over every domain butler's roster guidance and the lead policy."""
    return Guidance(
        broker,
        roster_guidance_sources(roster, roster_limits),
        limits,
        lead_policy=LeadPolicyGuidance(roster, roster_limits),
    )


__all__ = [
    "CLAUDE_MD",
    "LEAD_POLICY",
    "LEAD_POLICY_PATH",
    "ButlerGuidance",
    "LeadPolicyGuidance",
    "RosterGuidanceLimits",
    "rank",
    "roster_guidance",
    "roster_guidance_sources",
]
