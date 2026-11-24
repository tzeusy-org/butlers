"""Shadow parity of the legacy context bus against perception (bu-dhloi1.21 AC4).

``public.user_context`` retires only on agreeing parity receipts
(``docs/data_and_storage/user-context-census.md``). This compares the legacy
signals with the owner situation reads for the three kinds that replace them:

* activity signals -> ``owner.activity``, compared as a set of activity names;
* ``at_home`` / ``in_space`` / ``away`` -> ``owner.place``, compared as the place
  class only (``inside`` or ``outside``);
* ``dnd`` -> ``owner.suppression``, compared as on or off.

``compare`` is pure and returns one receipt per kind in closed vocabularies: no
room name, place or locality reference, record reference, coordinate, title or
metadata is ever copied into a receipt or the log line.

``LegacyParity.run`` reads the legacy side through the context bus on the
caller's pool and the perception side as ``perception_owner_reader`` on
``BUTLERS_PERCEPTION_READER_DSN``, switching role with ``SET LOCAL ROLE``
inside one transaction exactly as ``/now`` does. It returns the receipts and
logs one content-blind line. Dark: ``flags.LEGACY_PARITY_LIVE`` is off, nothing
calls or schedules it, and it adds no role or grant. The durable receipt store
ships with the switch-time scheduler that runs it.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import asyncpg

from butlers.context_bus import ContextEntry, get_active_context
from butlers.db import register_jsonb_codec
from butlers.perception import flags
from butlers.perception.model import ACTIVITIES
from butlers.perception.situation import Audience, SituationRead, situation_read

logger = logging.getLogger(__name__)

READER_DSN_ENV = "BUTLERS_PERCEPTION_READER_DSN"
PARITY_KINDS = ("owner.activity", "owner.place", "owner.suppression")
REASONS = frozenset(
    {
        "agree",
        "value_differs",
        "legacy_only",
        "perception_only",
        "perception_unknown",
        "legacy_conflict",
        "unreadable",
    }
)
_INSIDE = frozenset({"at_home", "in_space"})


class LegacyParityDark(RuntimeError):
    """The comparator is dark until the switch."""


class LegacyParityUnavailable(RuntimeError):
    """A side could not be read; no receipt is invented."""


@dataclass(frozen=True, slots=True)
class ParityReceipt:
    """One kind's comparison, in closed vocabularies only."""

    kind: str
    legacy: tuple[str, ...]
    perception_state: str
    perception: tuple[str, ...]
    agree: bool
    reason: str


def _legacy_tokens(kind: str, signals: frozenset[str]) -> tuple[str, ...]:
    if kind == "owner.activity":
        return tuple(sorted(signals & ACTIVITIES))
    if kind == "owner.place":
        classes = set()
        if signals & _INSIDE:
            classes.add("inside")
        if "away" in signals:
            classes.add("outside")
        return tuple(sorted(classes))
    return ("on",) if "dnd" in signals else ("off",)


def _perception_tokens(kind: str, value: Any) -> tuple[str, ...]:
    """Tokens for a known value; anything off-shape raises ``ValueError``."""
    if kind == "owner.activity":
        if type(value) is not list or any(type(v) is not str or v not in ACTIVITIES for v in value):
            raise ValueError("invalid activity set")
        return tuple(sorted(set(value)))
    if kind == "owner.place":
        presence = value.get("presence") if isinstance(value, dict) else None
        if presence not in ("inside", "outside"):
            raise ValueError("invalid presence")
        return (presence,)
    active = value.get("active") if isinstance(value, dict) else None
    if type(active) is not bool:
        raise ValueError("invalid suppression")
    return ("on",) if active else ("off",)


def _asserts(tokens: tuple[str, ...]) -> bool:
    # Suppression tokens are always ("on",) or ("off",); other kinds are silent when empty.
    return bool(tokens)


def _receipt(
    kind: str, legacy: tuple[str, ...], read: SituationRead | None, as_of: datetime
) -> ParityReceipt:
    def done(state: str, perception: tuple[str, ...], reason: str) -> ParityReceipt:
        return ParityReceipt(kind, legacy, state, perception, reason == "agree", reason)

    if kind == "owner.place" and len(legacy) > 1:
        state = read.state if isinstance(read, SituationRead) else "unreadable"
        return done(state, (), "legacy_conflict")
    if not isinstance(read, SituationRead):
        return done("unreadable", (), "unreadable")
    state = read.state
    if state == "known" and read.fresh_until is not None and read.fresh_until <= as_of:
        state = "unknown"
    if state != "known":
        return done(state, (), "legacy_only" if _asserts(legacy) else "perception_unknown")
    try:
        perception = _perception_tokens(kind, read.value)
    except ValueError:
        return done("unreadable", (), "unreadable")
    if legacy == perception:
        return done(state, perception, "agree")
    if not _asserts(legacy):
        return done(state, perception, "perception_only")
    if not _asserts(perception):
        return done(state, perception, "legacy_only")
    return done(state, perception, "value_differs")


def compare(
    legacy: Sequence[ContextEntry],
    reads: Mapping[str, SituationRead | None],
    as_of: datetime,
) -> tuple[ParityReceipt, ...]:
    """One receipt per parity kind. A kind absent from ``reads`` is unreadable."""
    if as_of.tzinfo is None:
        raise ValueError("an aware instant is required")
    signals = frozenset(e.signal_type for e in legacy if e.expires_at > as_of)
    return tuple(
        _receipt(kind, _legacy_tokens(kind, signals), reads.get(kind), as_of)
        for kind in PARITY_KINDS
    )


def log_line(receipts: Sequence[ParityReceipt]) -> str:
    """Kinds, states, agreement and reasons only; never a value."""
    return "legacy parity: " + "; ".join(
        f"{r.kind} state={r.perception_state} agree={str(r.agree).lower()} reason={r.reason}"
        for r in receipts
    )


async def open_owner_reader() -> Any | None:
    """A fresh perception reader connection, or ``None`` when not activated (as ``/now``)."""
    dsn = (os.environ.get(READER_DSN_ENV) or "").strip()
    if not dsn:
        return None
    connection = await asyncpg.connect(dsn, command_timeout=15)
    await register_jsonb_codec(connection)
    return connection


class LegacyParity:
    """Dark runner: legacy bus on ``legacy_pool``, perception as the owner reader."""

    def __init__(
        self,
        legacy_pool: Any,
        *,
        subject: UUID,
        open_reader: Callable[[], Awaitable[Any | None]] = open_owner_reader,
    ) -> None:
        self.legacy_pool = legacy_pool
        self.subject = subject
        self.open_reader = open_reader

    async def run(self, *, as_of: datetime | None = None) -> tuple[ParityReceipt, ...]:
        if not flags.LEGACY_PARITY_LIVE:
            raise LegacyParityDark("the legacy parity comparator is dark")
        as_of = as_of or datetime.now(UTC)
        try:
            legacy = await get_active_context(self.legacy_pool)
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError) as exc:
            raise LegacyParityUnavailable("the legacy context bus is unreadable") from exc
        reads: dict[str, SituationRead | None] = {}
        connection = None
        try:
            connection = await self.open_reader()
            if connection is None:
                raise LegacyParityUnavailable("the perception reader is not activated")
            async with connection.transaction():
                await connection.execute("SET LOCAL ROLE perception_owner_reader")
                for kind in PARITY_KINDS:
                    try:
                        read = await situation_read(
                            connection,
                            audience=Audience.OWNER,
                            kind=kind,
                            subject=self.subject,
                            as_of=as_of,
                        )
                    except ValueError:
                        read = None
                    reads[kind] = read if isinstance(read, SituationRead) else None
        except (asyncpg.PostgresError, asyncpg.InterfaceError, OSError, PermissionError) as exc:
            raise LegacyParityUnavailable("the perception reader is unavailable") from exc
        finally:
            if connection is not None:
                await connection.close()
        receipts = compare(legacy, reads, as_of)
        logger.info(log_line(receipts))
        return receipts


__all__ = [
    "PARITY_KINDS",
    "READER_DSN_ENV",
    "REASONS",
    "LegacyParity",
    "LegacyParityDark",
    "LegacyParityUnavailable",
    "ParityReceipt",
    "compare",
    "log_line",
    "open_owner_reader",
]
