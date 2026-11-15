"""Per-row projection coverage for raw OwnTracks fixes (bu-s11n0s.7, slice 2).

Raw fixes in ``connectors.owntracks_points`` may expire only after every Chronicler adapter that
reads them has genuinely finished with them. :class:`ProjectionCoverage` is the per-run ledger an
adapter fills while it projects, then flushes on its projection transaction connection, inside
the projection savepoint, so coverage commits and rolls back with the outputs and the cursor
(tables: ``chronicler_032``).

The ledger only accepts facts the adapter observed itself:

* :meth:`observe` registers a raw row exactly as it was read (``id``, ``idempotency_key`` and
  the immutable ``retention_at`` from core_340). A row without a timezone-aware
  ``retention_at`` is never recorded, so a malformed or legacy read cannot be covered.
* :meth:`record_event` / :meth:`record_episode` take the ids returned by the adapter's own
  upserts for that row in this run.
* :meth:`hold` keeps a row in a named open carry at the end of the run.
  :meth:`close_carry` ends a carry from an earlier run, attaching the episode that carry
  produced to its stored members; :meth:`release_prefix` re-evaluates every stored carry whose
  key has a prefix (the adapter recomputed that whole state this run).

:meth:`flush` then decides each row it knows (rows observed this run plus stored open members):

* held this run, or still a member of a carry that was neither closed nor released: it stays in
  ``location_projection_open_members`` with its accumulated output ids, never covered;
* otherwise, with at least one output id: a ``projected`` coverage record (first record wins,
  ``ON CONFLICT DO NOTHING``), and any open-member row is removed;
* otherwise: no coverage. The row is simply not deletable.

A row held under two carries keeps the higher-priority key (``movement:`` before others), so the
carry whose closing attaches an output is the one that finds it.

``evaluated_no_output`` is part of the table contract for the place and SSID adapters, whose
rows can be finished without output. ``owntracks.points`` always emits a point event per
accepted row, so it writes only ``projected``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID, uuid4

import asyncpg

COVERED_ADAPTERS = ("owntracks.points", "owntracks.place_cluster", "owntracks.ssid_presence")
DISPOSITION_PROJECTED = "projected"
DISPOSITION_EVALUATED_NO_OUTPUT = "evaluated_no_output"
#: Carry keys with this prefix win when a row is held by more than one carry.
PRIORITY_CARRY_PREFIX = "movement:"

_SELECT_OPEN = (
    "SELECT carry_key, raw_source_id, logical_source_key, retention_at, output_event_ids, "
    "output_episode_ids, joined_batch_id FROM location_projection_open_members "
    "WHERE adapter_name = $1"
)
_DELETE_OPEN = (
    "DELETE FROM location_projection_open_members "
    "WHERE adapter_name = $1 AND raw_source_id = ANY($2::uuid[])"
)
_INSERT_OPEN = (
    "INSERT INTO location_projection_open_members (adapter_name, carry_key, raw_source_id, "
    "logical_source_key, retention_at, output_event_ids, output_episode_ids, joined_batch_id) "
    "VALUES ($1, $2, $3, $4, $5, $6::uuid[], $7::uuid[], $8)"
)
_INSERT_COVERAGE = (
    "INSERT INTO location_projection_coverage (adapter_name, raw_source_id, logical_source_key, "
    "retention_at, disposition, output_event_ids, output_episode_ids, projection_batch_id) "
    "VALUES ($1, $2, $3, $4, $5, $6::uuid[], $7::uuid[], $8) "
    "ON CONFLICT (adapter_name, raw_source_id) DO NOTHING"
)


class CoverageConnection(Protocol):
    """The slice of an asyncpg connection :meth:`ProjectionCoverage.flush` uses."""

    async def fetch(self, query: str, *args: Any) -> list[Any]: ...

    async def execute(self, query: str, *args: Any) -> str: ...

    async def executemany(self, command: str, args: Iterable[Sequence[Any]]) -> None: ...


@dataclass
class _Row:
    raw_source_id: UUID
    logical_source_key: str
    retention_at: datetime
    event_ids: set[UUID] = field(default_factory=set)
    episode_ids: set[UUID] = field(default_factory=set)


@dataclass(frozen=True)
class _Member:
    carry_key: str
    raw_source_id: UUID
    logical_source_key: str
    retention_at: datetime
    event_ids: frozenset[UUID]
    episode_ids: frozenset[UUID]
    joined_batch_id: UUID


@dataclass(frozen=True)
class FlushSummary:
    covered: int
    held: int


def _carry_rank(key: str) -> tuple[int, str]:
    return (1 if key.startswith(PRIORITY_CARRY_PREFIX) else 0, key)


def _as_uuid(value: Any) -> UUID | None:
    if isinstance(value, UUID):
        return value
    if isinstance(value, str):
        try:
            return UUID(value)
        except ValueError:
            return None
    return None


class ProjectionCoverage:
    """One adapter run's coverage ledger. See the module docstring for the rule."""

    def __init__(self, adapter_name: str) -> None:
        if adapter_name not in COVERED_ADAPTERS:
            raise ValueError(f"no location coverage contract for adapter {adapter_name!r}")
        self.adapter_name = adapter_name
        self.batch_id = uuid4()
        self._rows: dict[UUID, _Row] = {}
        self._holds: dict[UUID, str] = {}
        self._closed: dict[str, set[UUID]] = {}
        self._released_prefixes: set[str] = set()

    # -- facts the adapter observed ---------------------------------------------------------

    def observe(self, row: Mapping[str, Any]) -> bool:
        """Register a raw row as read; return False when it can never be covered."""
        raw_id = _as_uuid(row.get("id"))
        key = row.get("idempotency_key")
        retention_at = row.get("retention_at")
        if (
            raw_id is None
            or not isinstance(key, str)
            or not key.strip()
            or not isinstance(retention_at, datetime)
            or retention_at.tzinfo is None
        ):
            return False
        if raw_id not in self._rows:
            self._rows[raw_id] = _Row(raw_id, key.strip(), retention_at)
        return True

    def record_event(self, row: Mapping[str, Any], event_id: Any) -> None:
        state = self._state(row)
        event = _as_uuid(event_id)
        if state is not None and event is not None:
            state.event_ids.add(event)

    def record_episode(self, rows: Iterable[Mapping[str, Any]], episode_id: Any) -> None:
        episode = _as_uuid(episode_id)
        if episode is None:
            return
        for row in rows:
            state = self._state(row)
            if state is not None:
                state.episode_ids.add(episode)

    def hold(self, raw_source_id: Any, carry_key: str) -> None:
        """Keep a row open under ``carry_key`` at the end of this run."""
        raw_id = _as_uuid(raw_source_id)
        if raw_id is None or not carry_key:
            return
        current = self._holds.get(raw_id)
        if current is None or _carry_rank(carry_key) > _carry_rank(current):
            self._holds[raw_id] = carry_key

    def close_carry(self, carry_key: str, episode_id: Any = None) -> None:
        """End a stored carry; its stored members gain ``episode_id`` (if any)."""
        episodes = self._closed.setdefault(carry_key, set())
        episode = _as_uuid(episode_id)
        if episode is not None:
            episodes.add(episode)

    def release_prefix(self, prefix: str) -> None:
        """Stored members of every carry with ``prefix`` stay open only if held again."""
        if prefix:
            self._released_prefixes.add(prefix)

    def _state(self, row: Mapping[str, Any]) -> _Row | None:
        raw_id = _as_uuid(row.get("id"))
        return self._rows.get(raw_id) if raw_id is not None else None

    # -- the decision -------------------------------------------------------------------------

    async def flush(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy | CoverageConnection,
    ) -> FlushSummary:
        """Write coverage and open members on the projection's own transaction connection."""
        if not self._rows and not self._closed and not self._released_prefixes:
            return FlushSummary(covered=0, held=0)
        stored = {
            member.raw_source_id: member
            for member in (
                _Member(
                    carry_key=record["carry_key"],
                    raw_source_id=record["raw_source_id"],
                    logical_source_key=record["logical_source_key"],
                    retention_at=record["retention_at"],
                    event_ids=frozenset(record["output_event_ids"] or ()),
                    episode_ids=frozenset(record["output_episode_ids"] or ()),
                    joined_batch_id=record["joined_batch_id"],
                )
                for record in await conn.fetch(_SELECT_OPEN, self.adapter_name)
            )
        }

        open_rows: list[_Member] = []
        covered: list[_Member] = []
        removed: list[UUID] = []
        held = 0
        for raw_id in sorted(set(self._rows) | set(stored), key=str):
            prior = stored.get(raw_id)
            current = self._rows.get(raw_id)
            base = current if current is not None else prior
            assert base is not None
            events = set(prior.event_ids) if prior else set()
            episodes = set(prior.episode_ids) if prior else set()
            if current is not None:
                events |= current.event_ids
                episodes |= current.episode_ids

            keys: list[str] = []
            if raw_id in self._holds:
                keys.append(self._holds[raw_id])
            if prior is not None:
                if prior.carry_key in self._closed:
                    episodes |= self._closed[prior.carry_key]
                elif not any(prior.carry_key.startswith(p) for p in self._released_prefixes):
                    keys.append(prior.carry_key)
            member = _Member(
                carry_key=max(keys, key=_carry_rank) if keys else "",
                raw_source_id=raw_id,
                logical_source_key=base.logical_source_key,
                retention_at=base.retention_at,
                event_ids=frozenset(events),
                episode_ids=frozenset(episodes),
                joined_batch_id=prior.joined_batch_id if prior else self.batch_id,
            )
            if keys:
                held += 1
                if member != prior:
                    if prior is not None:
                        removed.append(raw_id)
                    open_rows.append(member)
                continue
            if prior is not None:
                removed.append(raw_id)
            if events or episodes:
                covered.append(member)

        if removed:
            await conn.execute(_DELETE_OPEN, self.adapter_name, removed)
        if open_rows:
            await conn.executemany(
                _INSERT_OPEN,
                [
                    (
                        self.adapter_name,
                        m.carry_key,
                        m.raw_source_id,
                        m.logical_source_key,
                        m.retention_at,
                        sorted(m.event_ids, key=str),
                        sorted(m.episode_ids, key=str),
                        m.joined_batch_id,
                    )
                    for m in open_rows
                ],
            )
        if covered:
            await conn.executemany(
                _INSERT_COVERAGE,
                [
                    (
                        self.adapter_name,
                        m.raw_source_id,
                        m.logical_source_key,
                        m.retention_at,
                        DISPOSITION_PROJECTED,
                        sorted(m.event_ids, key=str),
                        sorted(m.episode_ids, key=str),
                        self.batch_id,
                    )
                    for m in covered
                ],
            )
        return FlushSummary(covered=len(covered), held=held)


__all__ = [
    "COVERED_ADAPTERS",
    "CoverageConnection",
    "DISPOSITION_EVALUATED_NO_OUTPUT",
    "DISPOSITION_PROJECTED",
    "FlushSummary",
    "PRIORITY_CARRY_PREFIX",
    "ProjectionCoverage",
]
