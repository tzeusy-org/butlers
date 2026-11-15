"""Per-row projection coverage for raw OwnTracks fixes (bu-s11n0s.7, slice 2).

Raw fixes in ``connectors.owntracks_points`` may expire only after every Chronicler adapter that
reads them has genuinely finished with them. :class:`ProjectionCoverage` is the per-run ledger an
adapter fills while it projects, then flushes on its projection transaction connection, inside
the projection savepoint, so coverage commits and rolls back with the outputs and the cursor
(tables: ``chronicler_032`` to ``chronicler_034``).

A coverage record is exact: it names the raw row's content digest and the adapter's config
revision it was decided under.

* The **content digest** (:func:`raw_row_digest_sql`, ``v1:<sha256 hex>``) is computed by
  Postgres over every column of the raw row as stored, so the adapters, discovery and a later
  deleter all hash the same bytes. A row whose content changes after it was covered (an
  upstream re-write) no longer matches its record.
* The **config revision** (:func:`config_revision`) digests everything that decides the adapter's
  outputs: its thresholds, and for SSID presence the owner's network mapping. A changed revision
  makes every older record stale, so a mapping change or an owner place correction invalidates
  coverage without updating it. Records stay append-only: the current one is the record whose
  revision and digest both match.

The ledger only accepts facts the adapter observed itself:

* :meth:`observe` registers a raw row exactly as it was read (``id``, ``idempotency_key``, the
  immutable ``retention_at`` from core_340 and the ``content_digest`` the read selected). A row
  without a timezone-aware ``retention_at`` or a well-formed digest is never recorded, so a
  malformed or legacy read cannot be covered.
* :meth:`record_event` / :meth:`record_episode` take the ids returned by the adapter's own
  upserts for a row in this run. The row is one observed this run or a stored open member
  (an adapter that carried the row's id in its own checkpoint state); output for any other id
  is ignored.
* :meth:`hold` keeps a row in a named open carry at the end of the run.
  :meth:`close_carry` ends a carry from an earlier run, attaching the episode that carry
  produced to its stored members; :meth:`carry_output` attaches an episode to a carry's stored
  members while it stays open; :meth:`release_prefix` re-evaluates every stored carry whose key
  has a prefix (the adapter recomputed that whole state this run); :meth:`reset_prefix`
  discards such carries and the outputs their members gained under the previous config
  revision (a member not read again this run leaves the table uncovered).
  :meth:`void_carries_from_other_revisions` applies that reset whenever the revision an adapter
  saved beside its carries is not the current one, so an open stay, trip or span that began
  under old settings never closes into a current-revision record.

A row can sit in several carries at once (a place stay inside an away-from-home trip), so an
open member stores the set of its carry keys. :meth:`flush` then decides each row it knows (rows
observed this run plus stored open members):

* held this run, or still in a stored carry that was neither closed nor released: it stays in
  ``location_projection_open_members`` with all those keys and its accumulated output ids,
  never covered;
* otherwise, with at least one output id: a ``projected`` coverage record (first record wins,
  ``ON CONFLICT DO NOTHING``), and any open-member row is removed;
* otherwise, for an adapter whose every known row is evaluated by its run
  (``evaluates_every_row``): an ``evaluated_no_output`` record, the adapter's deliberate
  decision to emit nothing for the row (too short a stay, an unmapped network, a rejected fix);
* otherwise: no coverage. The row is simply not deletable.

``owntracks.points`` emits a point event for every accepted row and leaves rows it did not
project uncovered, so it writes only ``projected``. The place and SSID adapters evaluate every
row they observe and use ``evaluated_no_output``.

Two run-level records sit beside the ledger:

* :func:`record_attempt` keeps one committed row per adapter in
  ``location_projection_attempts``: the last outcome, the last success and failure times, the
  failure's exception class and the consecutive failure count. A failed run writes it after its
  projection savepoint rolled back, so a failure is never hidden behind an earlier success.
* :func:`discover_uncovered` is the per-row anti-join that finds settled raw rows (arrival order,
  ``(recorded_at, id)``) with no current coverage record and no open carry, below a frontier
  frozen at the transaction's start. A successful run stores the count with its attempt, so a
  row the adapter's cursor passed (a late commit, a malformed fix, a pre-coverage row) stays
  visible as a number instead of disappearing.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Protocol, TypeGuard
from uuid import UUID, uuid4

import asyncpg

COVERED_ADAPTERS = ("owntracks.points", "owntracks.place_cluster", "owntracks.ssid_presence")
DISPOSITION_PROJECTED = "projected"
DISPOSITION_EVALUATED_NO_OUTPUT = "evaluated_no_output"

#: Version prefix of :func:`raw_row_digest_sql` and :func:`config_revision`. A new algorithm
#: takes a new prefix; records under an older one never match the current digest.
DIGEST_VERSION = "v1"
_DIGEST_PATTERN = re.compile(r"^v1:[0-9a-f]{64}$")

#: Carryover key under which an adapter saves the config revision its open carries were decided
#: under, in the same transaction as the flush. See
#: :meth:`ProjectionCoverage.void_carries_from_other_revisions`.
COVERAGE_REVISION_CARRYOVER_KEY = "_coverage_revision"

#: Discovery counts settled rows only: arrivals newer than this are the next run's normal work.
DISCOVERY_SETTLE = timedelta(hours=1)
#: How far behind the frontier discovery looks. Older uncovered rows are the legacy replay's.
DISCOVERY_LOOKBACK = timedelta(days=7)
#: Discovery stops counting here and reports the count as capped.
DISCOVERY_LIMIT = 1000

_SELECT_OPEN = (
    "SELECT carry_keys, raw_source_id, logical_source_key, retention_at, content_digest, "
    "output_event_ids, output_episode_ids, joined_batch_id FROM location_projection_open_members "
    "WHERE adapter_name = $1"
)
_DELETE_OPEN = (
    "DELETE FROM location_projection_open_members "
    "WHERE adapter_name = $1 AND raw_source_id = ANY($2::uuid[])"
)
_INSERT_OPEN = (
    "INSERT INTO location_projection_open_members (adapter_name, carry_keys, raw_source_id, "
    "logical_source_key, retention_at, content_digest, output_event_ids, output_episode_ids, "
    "joined_batch_id) VALUES ($1, $2::text[], $3, $4, $5, $6, $7::uuid[], $8::uuid[], $9)"
)
_INSERT_COVERAGE = (
    "INSERT INTO location_projection_coverage (adapter_name, raw_source_id, config_revision, "
    "content_digest, logical_source_key, retention_at, disposition, output_event_ids, "
    "output_episode_ids, projection_batch_id) "
    "VALUES ($1, $2, $3, $4, $5, $6, $7, $8::uuid[], $9::uuid[], $10) "
    "ON CONFLICT (adapter_name, raw_source_id, config_revision, content_digest) DO NOTHING"
)


def raw_row_digest_sql(alias: str = "") -> str:
    """SQL for a raw ``connectors.owntracks_points`` row's content digest (``v1:<hex>``).

    Every column is hashed as stored. Timestamps enter as epoch seconds, so the session time
    zone cannot change the bytes; the JSONB array's text form is canonical in Postgres.
    """
    prefix = f"{alias}." if alias else ""
    columns = ", ".join(
        (
            f"{prefix}id::text",
            f"{prefix}idempotency_key",
            f"{prefix}endpoint_identity",
            f"extract(epoch FROM {prefix}ts)",
            f"extract(epoch FROM {prefix}recorded_at)",
            f"extract(epoch FROM {prefix}retention_at)",
            f"{prefix}lat",
            f"{prefix}lon",
            f"{prefix}accuracy",
            f"{prefix}trigger",
            f"{prefix}event",
            f"{prefix}raw_payload",
        )
    )
    return (
        f"('{DIGEST_VERSION}:' || encode(sha256(convert_to("
        f"jsonb_build_array({columns})::text, 'UTF8')), 'hex'))"
    )


def config_revision(adapter_name: str, settings: Mapping[str, Any]) -> str:
    """Digest of everything that decides an adapter's outputs (``v1:<hex>``)."""
    if adapter_name not in COVERED_ADAPTERS:
        raise ValueError(f"no location coverage contract for adapter {adapter_name!r}")
    serialized = json.dumps(
        {"adapter": adapter_name, "settings": settings},
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return f"{DIGEST_VERSION}:{hashlib.sha256(serialized.encode()).hexdigest()}"


def _is_digest(value: Any) -> TypeGuard[str]:
    return isinstance(value, str) and _DIGEST_PATTERN.fullmatch(value) is not None


class RunConnection(Protocol):
    """The slice of an asyncpg connection the run-level helpers use."""

    async def fetch(self, query: str, *args: Any) -> list[Any]: ...

    async def execute(self, query: str, *args: Any) -> str: ...


class CoverageConnection(RunConnection, Protocol):
    """The slice of an asyncpg connection :meth:`ProjectionCoverage.flush` uses."""

    async def executemany(self, command: str, args: Iterable[Sequence[Any]]) -> None: ...


@dataclass
class _Row:
    raw_source_id: UUID
    logical_source_key: str
    retention_at: datetime
    content_digest: str
    event_ids: set[UUID] = field(default_factory=set)
    episode_ids: set[UUID] = field(default_factory=set)


@dataclass(frozen=True)
class _Member:
    carry_keys: frozenset[str]
    raw_source_id: UUID
    logical_source_key: str
    retention_at: datetime
    content_digest: str
    event_ids: frozenset[UUID]
    episode_ids: frozenset[UUID]
    joined_batch_id: UUID


@dataclass(frozen=True)
class FlushSummary:
    covered: int
    held: int
    evaluated: int = 0
    discarded: int = 0


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

    def __init__(
        self,
        adapter_name: str,
        *,
        config_revision: str,
        evaluates_every_row: bool = False,
    ) -> None:
        if adapter_name not in COVERED_ADAPTERS:
            raise ValueError(f"no location coverage contract for adapter {adapter_name!r}")
        if not _is_digest(config_revision):
            raise ValueError("config_revision must come from config_revision()")
        self.adapter_name = adapter_name
        self.config_revision = config_revision
        self.evaluates_every_row = evaluates_every_row
        self.batch_id = uuid4()
        self._rows: dict[UUID, _Row] = {}
        # Outputs for ids not observed this run; applied only to stored open members.
        self._member_events: dict[UUID, set[UUID]] = {}
        self._member_episodes: dict[UUID, set[UUID]] = {}
        self._holds: dict[UUID, set[str]] = {}
        self._closed: dict[str, set[UUID]] = {}
        self._carry_outputs: dict[str, set[UUID]] = {}
        self._released_prefixes: set[str] = set()
        self._reset_prefixes: set[str] = set()

    # -- facts the adapter observed ---------------------------------------------------------

    def observe(self, row: Mapping[str, Any]) -> bool:
        """Register a raw row as read; return False when it can never be covered."""
        raw_id = _as_uuid(row.get("id"))
        key = row.get("idempotency_key")
        retention_at = row.get("retention_at")
        digest = row.get("content_digest")
        if (
            raw_id is None
            or not isinstance(key, str)
            or not key.strip()
            or not isinstance(retention_at, datetime)
            or retention_at.tzinfo is None
            or not _is_digest(digest)
        ):
            return False
        if raw_id not in self._rows:
            self._rows[raw_id] = _Row(raw_id, key.strip(), retention_at, digest)
        return True

    def record_event(self, row: Mapping[str, Any], event_id: Any) -> None:
        raw_id, event = _as_uuid(row.get("id")), _as_uuid(event_id)
        if raw_id is None or event is None:
            return
        state = self._rows.get(raw_id)
        if state is not None:
            state.event_ids.add(event)
        else:
            self._member_events.setdefault(raw_id, set()).add(event)

    def record_episode(self, rows: Iterable[Mapping[str, Any]], episode_id: Any) -> None:
        episode = _as_uuid(episode_id)
        if episode is None:
            return
        for row in rows:
            raw_id = _as_uuid(row.get("id"))
            if raw_id is None:
                continue
            state = self._rows.get(raw_id)
            if state is not None:
                state.episode_ids.add(episode)
            else:
                self._member_episodes.setdefault(raw_id, set()).add(episode)

    def hold(self, raw_source_id: Any, carry_key: str) -> None:
        """Keep a row open under ``carry_key`` at the end of this run."""
        raw_id = _as_uuid(raw_source_id)
        if raw_id is None or not carry_key:
            return
        self._holds.setdefault(raw_id, set()).add(carry_key)

    def close_carry(self, carry_key: str, episode_id: Any = None) -> None:
        """End a stored carry; its stored members gain ``episode_id`` (if any)."""
        episodes = self._closed.setdefault(carry_key, set())
        episode = _as_uuid(episode_id)
        if episode is not None:
            episodes.add(episode)

    def carry_output(self, carry_key: str, episode_id: Any) -> None:
        """Attach ``episode_id`` to a stored carry's members; the carry stays open."""
        episode = _as_uuid(episode_id)
        if episode is not None and carry_key:
            self._carry_outputs.setdefault(carry_key, set()).add(episode)

    def release_prefix(self, prefix: str) -> None:
        """Stored members of every carry with ``prefix`` stay open only if held again."""
        if prefix:
            self._released_prefixes.add(prefix)

    def reset_prefix(self, prefix: str) -> None:
        """Void every stored carry with ``prefix``: its config revision is gone.

        A stored member of such a carry loses the carry and every output it gained so far (they
        were decided under the old revision). It is decided again only from what this run
        observed and recorded for it; a member not read this run leaves the open table with no
        coverage, to be found by discovery.
        """
        if prefix:
            self._reset_prefixes.add(prefix)

    def void_carries_from_other_revisions(self, stored_revision: object, *prefixes: str) -> bool:
        """Reset ``prefixes`` unless the stored carries were decided under this revision.

        ``stored_revision`` is the revision the adapter saved beside its carries
        (``COVERAGE_REVISION_CARRYOVER_KEY``) in the run that last flushed. A missing or malformed
        value (carries saved before the revision was persisted) counts as another revision: an
        unknown decision never closes into a current-revision record. Returns whether it voided.
        """
        if not prefixes:
            raise ValueError("void_carries_from_other_revisions needs at least one carry prefix")
        if stored_revision == self.config_revision:
            return False
        for prefix in prefixes:
            self.reset_prefix(prefix)
        return True

    # -- the decision -------------------------------------------------------------------------

    def _has_work(self) -> bool:
        return bool(
            self._rows
            or self._closed
            or self._released_prefixes
            or self._reset_prefixes
            or self._holds
            or self._carry_outputs
            or self._member_events
            or self._member_episodes
        )

    async def flush(
        self,
        conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy | CoverageConnection,
    ) -> FlushSummary:
        """Write coverage and open members on the projection's own transaction connection."""
        if not self._has_work():
            return FlushSummary(covered=0, held=0)
        stored = {
            member.raw_source_id: member
            for member in (
                _Member(
                    carry_keys=frozenset(record["carry_keys"] or ()),
                    raw_source_id=record["raw_source_id"],
                    logical_source_key=record["logical_source_key"],
                    retention_at=record["retention_at"],
                    content_digest=record["content_digest"],
                    event_ids=frozenset(record["output_event_ids"] or ()),
                    episode_ids=frozenset(record["output_episode_ids"] or ()),
                    joined_batch_id=record["joined_batch_id"],
                )
                for record in await conn.fetch(_SELECT_OPEN, self.adapter_name)
            )
        }

        open_rows: list[_Member] = []
        covered: list[tuple[_Member, str]] = []
        removed: list[UUID] = []
        held = evaluated = discarded = 0
        for raw_id in sorted(set(self._rows) | set(stored), key=str):
            prior = stored.get(raw_id)
            current = self._rows.get(raw_id)
            base = current if current is not None else prior
            assert base is not None
            prior_keys = prior.carry_keys if prior else frozenset()
            voided = any(
                key.startswith(prefix) for key in prior_keys for prefix in self._reset_prefixes
            )
            # A voided member keeps nothing it gained under the old config revision.
            events = set(prior.event_ids) if prior and not voided else set()
            episodes = set(prior.episode_ids) if prior and not voided else set()
            if current is not None:
                events |= current.event_ids
                episodes |= current.episode_ids
            elif prior is not None:
                events |= self._member_events.get(raw_id, set())
                episodes |= self._member_episodes.get(raw_id, set())

            keys = set(self._holds.get(raw_id, ()))
            for key in prior_keys:
                if any(key.startswith(p) for p in self._reset_prefixes):
                    continue
                episodes |= self._carry_outputs.get(key, set())
                if key in self._closed:
                    episodes |= self._closed[key]
                elif not any(key.startswith(p) for p in self._released_prefixes):
                    keys.add(key)
            member = _Member(
                carry_keys=frozenset(keys),
                raw_source_id=raw_id,
                logical_source_key=base.logical_source_key,
                retention_at=base.retention_at,
                content_digest=base.content_digest,
                event_ids=frozenset(events),
                episode_ids=frozenset(episodes),
                joined_batch_id=prior.joined_batch_id if prior and not voided else self.batch_id,
            )
            if voided and current is None and not keys:
                # Not read again under the new revision: no decision exists for it yet.
                removed.append(raw_id)
                discarded += 1
                continue
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
                covered.append((member, DISPOSITION_PROJECTED))
            elif self.evaluates_every_row:
                covered.append((member, DISPOSITION_EVALUATED_NO_OUTPUT))
                evaluated += 1

        if removed:
            await conn.execute(_DELETE_OPEN, self.adapter_name, removed)
        if open_rows:
            await conn.executemany(
                _INSERT_OPEN,
                [
                    (
                        self.adapter_name,
                        sorted(m.carry_keys),
                        m.raw_source_id,
                        m.logical_source_key,
                        m.retention_at,
                        m.content_digest,
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
                        self.config_revision,
                        m.content_digest,
                        m.logical_source_key,
                        m.retention_at,
                        disposition,
                        sorted(m.event_ids, key=str),
                        sorted(m.episode_ids, key=str),
                        self.batch_id,
                    )
                    for m, disposition in covered
                ],
            )
        return FlushSummary(
            covered=len(covered), held=held, evaluated=evaluated, discarded=discarded
        )


# -- run-level records ------------------------------------------------------------------------

ATTEMPT_SUCCEEDED = "succeeded"
ATTEMPT_FAILED = "failed"
#: ``last_error_class`` of a run whose raw source was unavailable (the adapter skipped).
SOURCE_UNAVAILABLE = "source_unavailable"

_UPSERT_ATTEMPT = """
INSERT INTO location_projection_attempts AS a (
    adapter_name, config_revision, last_outcome, last_attempt_at, last_success_at,
    last_failure_at, last_error_class, consecutive_failures, uncovered_rows, uncovered_capped,
    uncovered_checked_at
)
VALUES (
    $1, $2, $3::text, now(),
    CASE WHEN $3::text = 'succeeded' THEN now() END,
    CASE WHEN $3::text = 'failed' THEN now() END,
    $4, CASE WHEN $3::text = 'failed' THEN 1 ELSE 0 END,
    $5, $6, CASE WHEN $5::int IS NOT NULL THEN now() END
)
ON CONFLICT (adapter_name) DO UPDATE SET
    config_revision = EXCLUDED.config_revision,
    last_outcome = EXCLUDED.last_outcome,
    last_attempt_at = EXCLUDED.last_attempt_at,
    last_success_at = COALESCE(EXCLUDED.last_success_at, a.last_success_at),
    last_failure_at = COALESCE(EXCLUDED.last_failure_at, a.last_failure_at),
    last_error_class = CASE WHEN EXCLUDED.last_outcome = 'failed'
                            THEN EXCLUDED.last_error_class ELSE a.last_error_class END,
    consecutive_failures = CASE WHEN EXCLUDED.last_outcome = 'failed'
                                THEN a.consecutive_failures + 1 ELSE 0 END,
    uncovered_rows = COALESCE(EXCLUDED.uncovered_rows, a.uncovered_rows),
    uncovered_capped = COALESCE(EXCLUDED.uncovered_capped, a.uncovered_capped),
    uncovered_checked_at = COALESCE(EXCLUDED.uncovered_checked_at, a.uncovered_checked_at)
"""


@dataclass(frozen=True)
class UncoveredScan:
    """Discovery's result: rows found (at most ``DISCOVERY_LIMIT``) and whether it stopped."""

    count: int
    capped: bool


async def record_attempt(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy | asyncpg.Pool | RunConnection,
    adapter_name: str,
    *,
    config_revision: str,
    error_class: str | None = None,
    uncovered: UncoveredScan | None = None,
) -> None:
    """Commit this run's outcome; ``error_class`` set means the run failed.

    Only the exception class is stored: a message may quote raw evidence.
    """
    if adapter_name not in COVERED_ADAPTERS:
        raise ValueError(f"no location coverage contract for adapter {adapter_name!r}")
    await conn.execute(
        _UPSERT_ATTEMPT,
        adapter_name,
        config_revision,
        ATTEMPT_FAILED if error_class is not None else ATTEMPT_SUCCEEDED,
        error_class,
        uncovered.count if uncovered is not None else None,
        uncovered.capped if uncovered is not None else None,
    )


def _discovery_sql() -> str:
    return f"""
SELECT p.id
FROM connectors.owntracks_points AS p
WHERE p.recorded_at <= now() - $3::interval
  AND p.recorded_at > now() - $3::interval - $4::interval
  AND NOT EXISTS (
      SELECT 1 FROM location_projection_coverage AS c
      WHERE c.adapter_name = $1 AND c.raw_source_id = p.id
        AND c.config_revision = $2 AND c.content_digest = {raw_row_digest_sql("p")}
  )
  AND NOT EXISTS (
      SELECT 1 FROM location_projection_open_members AS m
      WHERE m.adapter_name = $1 AND m.raw_source_id = p.id
  )
ORDER BY p.recorded_at, p.id
LIMIT $5
"""


async def discover_uncovered(
    conn: asyncpg.Connection | asyncpg.pool.PoolConnectionProxy | asyncpg.Pool | RunConnection,
    adapter_name: str,
    *,
    config_revision: str,
    settle: timedelta = DISCOVERY_SETTLE,
    lookback: timedelta = DISCOVERY_LOOKBACK,
    limit: int = DISCOVERY_LIMIT,
) -> UncoveredScan:
    """Settled raw rows with no current coverage and no open carry for ``adapter_name``.

    The frontier is ``now() - settle`` (the transaction's start, so it cannot move during the
    scan); the window reaches ``lookback`` behind it in arrival order. Matching is exact: a
    record under another config revision or for different row content does not count.
    """
    rows = await conn.fetch(
        _discovery_sql(), adapter_name, config_revision, settle, lookback, limit + 1
    )
    return UncoveredScan(count=min(len(rows), limit), capped=len(rows) > limit)


__all__ = [
    "ATTEMPT_FAILED",
    "ATTEMPT_SUCCEEDED",
    "COVERAGE_REVISION_CARRYOVER_KEY",
    "COVERED_ADAPTERS",
    "CoverageConnection",
    "DIGEST_VERSION",
    "DISCOVERY_LIMIT",
    "DISCOVERY_LOOKBACK",
    "DISCOVERY_SETTLE",
    "DISPOSITION_EVALUATED_NO_OUTPUT",
    "DISPOSITION_PROJECTED",
    "FlushSummary",
    "ProjectionCoverage",
    "RunConnection",
    "SOURCE_UNAVAILABLE",
    "UncoveredScan",
    "config_revision",
    "discover_uncovered",
    "raw_row_digest_sql",
    "record_attempt",
]
