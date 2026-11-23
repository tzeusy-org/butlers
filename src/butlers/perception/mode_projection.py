"""The ``owner.mode`` projection of the owner's mode entries (bu-dhloi1.21 PR6).

``owner.mode`` is the single versioned projection (``public.owner_modes``) of
which modes are active. Its only source is ``switchboard.owner_mode_entries``
(sw_077), the owner's own entries: no mode is derived from place, clock,
activity or any other signal, and an entry whose cause is not the owner's
refuses rather than projecting.

``project_modes`` is pure. The set is the active entries, under the rule SQL
``owner_mode_entry_active`` states (``modes.active_entry``: exited, or ending
at or before ``now``, is not active). The effective end is the earliest finite
end among them, capped at ``now + max_age``, so an open-ended entry still
projects a finite span; the projection must be written again by that end.

``ModeProjector`` writes it through ``PerceptionStore`` in one transaction,
only as ``perception_context_writer`` (the ``context.mode`` source's writer),
on the database's clock. It needs the ``context.mode`` source profile: the
caller passes the configured subject, evidence reference and freshness
maximum, and without them it refuses. The store refuses too while the
privileged profile is unconfigured, which it is until activation.

Dark: ``flags.MODE_PROJECTION_LIVE`` is off, nothing calls or schedules this,
``perception_context_writer`` has no login and no privilege on the entries.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from butlers.perception import flags
from butlers.perception.model import MODES, EventRequest
from butlers.perception.modes import OWNER_CAUSES, active_entry, mode_event
from butlers.perception.store import PerceptionStore

WRITER = "perception_context_writer"
#: The perception store's own bound on a source's freshness maximum.
MAX_AGE_BOUND = timedelta(seconds=34_560_000)
# The identity perception_write authenticates: the active role, else the login.
_ACTOR = "SELECT COALESCE(NULLIF(current_setting('role', true), 'none'), session_user)"
_NOW = "SELECT now()"
_ENTRIES = "SELECT mode, since, until, cause, exited_at FROM switchboard.owner_mode_entries"


class ModeProjectionDark(RuntimeError):
    """The owner.mode projection is not live."""


class ModeProjectionUnconfigured(RuntimeError):
    """The context.mode source profile is not configured."""


class ModeProjectionUnavailable(RuntimeError):
    """Not the context writer, or the owner's entries cannot be read."""


class ModeProjectionConflict(RuntimeError):
    """The projection moved again while it was being written."""


@dataclass(frozen=True, slots=True)
class ModeProjectionProfile:
    """The configured ``context.mode`` source profile the projection writes under."""

    subject: UUID
    evidence_ref: UUID
    max_age: timedelta

    def __post_init__(self) -> None:
        seconds = self.max_age // timedelta(seconds=1)
        if (
            seconds <= 0
            or timedelta(seconds=seconds) != self.max_age
            or self.max_age > MAX_AGE_BOUND
        ):
            raise ValueError("a source freshness maximum is whole seconds within the store bound")


@dataclass(frozen=True, slots=True)
class ModeProjection:
    modes: frozenset[str]
    end: datetime


def project_modes(
    rows: Iterable[Mapping[str, Any]], now: datetime, *, max_age: timedelta
) -> ModeProjection:
    """The active modes and their effective end, from the owner's entries alone."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("an aware now is required")
    if max_age <= timedelta(0):
        raise ValueError("a positive freshness maximum is required")
    seen: set[str] = set()
    active: set[str] = set()
    end = now + max_age
    for row in rows:
        mode = row["mode"]
        if mode not in MODES or mode in seen:
            raise ValueError("one entry per mode, of the eight modes only")
        seen.add(mode)
        if row["cause"] not in OWNER_CAUSES:
            raise ValueError("only the owner's entries project")
        entry = active_entry(mode, row, now)
        if entry is None:
            continue
        active.add(mode)
        if entry.until is not None:
            end = min(end, entry.until)
    return ModeProjection(frozenset(active), end)


class ModeProjector:
    """Writes ``owner.mode`` from the owner's entries; no caller or schedule yet."""

    def __init__(self, connection: Any, profile: ModeProjectionProfile | None):
        self.connection = connection
        self.profile = profile

    async def project(self, expected_generation: int = 0) -> dict[str, Any]:
        """Project the active entries now, in one transaction; returns the store's receipt.

        A generation conflict is written again once at the store's current
        generation (the projection is re-derived from the same entries); a
        second conflict raises.
        """
        if not flags.MODE_PROJECTION_LIVE:
            raise ModeProjectionDark("the owner.mode projection is dark")
        if self.profile is None:
            raise ModeProjectionUnconfigured("the context.mode source profile is unconfigured")
        profile = self.profile
        async with self.connection.transaction():
            if await self.connection.fetchval(_ACTOR) != WRITER:
                raise ModeProjectionUnavailable("owner.mode is written only by its context writer")
            now = await self.connection.fetchval(_NOW)
            if not isinstance(now, datetime):
                raise ModeProjectionUnavailable("database clock unavailable")
            try:
                rows = await self.connection.fetch(_ENTRIES)
            except (
                asyncpg.InvalidSchemaNameError,
                asyncpg.UndefinedTableError,
                asyncpg.InsufficientPrivilegeError,
            ) as exc:
                raise ModeProjectionUnavailable("the owner's mode entries are unreadable") from exc
            projection = project_modes(rows, now, max_age=profile.max_age)
            store = PerceptionStore(self.connection)
            generation = expected_generation
            for _attempt in range(2):
                body = mode_event(
                    projection.modes,
                    subject=profile.subject,
                    evidence_ref=profile.evidence_ref,
                    generation=generation,
                    at=now,
                    until=projection.end,
                )
                try:
                    receipt = await store.write(EventRequest(body))
                except asyncpg.InsufficientPrivilegeError as exc:
                    # The store refuses a missing or unconfigured source profile.
                    raise ModeProjectionUnconfigured(
                        "the context.mode source profile is unconfigured"
                    ) from exc
                if not (isinstance(receipt, dict) and receipt.get("status") == "conflict"):
                    return receipt
                generation = receipt["generation"]
            raise ModeProjectionConflict("owner.mode moved while it was being projected")


__all__ = [
    "MAX_AGE_BOUND",
    "WRITER",
    "ModeProjection",
    "ModeProjectionConflict",
    "ModeProjectionDark",
    "ModeProjectionProfile",
    "ModeProjectionUnavailable",
    "ModeProjectionUnconfigured",
    "ModeProjector",
    "project_modes",
]
