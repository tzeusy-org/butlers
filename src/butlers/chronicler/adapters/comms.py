"""Comms -> Social projection adapter.

Projects already-ingested inbound message activity from the five comms
connectors into ``social_episode`` activity candidates, resolving the message
participant to an entity via ``relationship.entity_facts`` (never a
chronicler-local contact store). Per the IEA reframe (tasks.md §6.2,
openspec change ``chronicler-intent-evidence-activity``, requirement
"Comms Projected Into Social").

Read surface
------------
This adapter reads ONLY:

- ``public.ingestion_events`` — the canonical, already-ingested envelope
  metadata record (id, received_at, source_channel, source_sender_identity).
  It does **not** read ``switchboard.message_inbox`` or any other surface
  that carries raw message content — per the chronicler doctrine ("never
  project raw source payloads — only stable refs"), this adapter never sees
  and never stores what a message said, only that a message arrived, when,
  over which channel, and (best-effort) from whom.
- ``relationship.entity_facts`` — participant identity resolution, joined
  the same way ``CoreSessionsAdapter._resolve_contacts`` already does for
  route-triggered session titles (bu-hjo3i). Requires the
  ``core_150_chronicler_comms_entity_facts_grant`` migration.

The five comms connectors write these ``source_channel`` values (verified
against each connector's config default / ``_build_ingest_envelope`` call
site, NOT their module filename):

============================  ================================
Connector module               ``ingestion_events.source_channel``
============================  ================================
``connectors/gmail.py``                 ``"email"``
``connectors/telegram_bot.py``          ``"telegram_bot"``
``connectors/telegram_user_client.py``  ``"telegram_user_client"``
``connectors/whatsapp_user_client.py``  ``"whatsapp_user_client"``
``connectors/discord_user.py``          ``"discord"``
============================  ================================

Burst segmentation
-------------------
Ingestion events sharing the same ``(source_channel, source_sender_identity)``
are grouped and sorted by ``received_at``; a run of events separated by no
more than ``BURST_GAP_MINUTES`` collapses into one ``social_episode`` spanning
``(first.received_at, last.received_at)``. This mirrors
``OwnTracksPointAdapter``'s movement-episode rollup, with one deliberate
simplification: **no cross-batch carryover**. A burst that straddles a batch
boundary may be fragmented into two candidate episodes. This is accepted by
design — tasks.md §7 ("Deterministic Reconciliation Core") explicitly merges
duplicate/overlapping same-lane candidates at day-close, so Tier-1 projection
does not need to be perfectly stitched to be correct in aggregate.

Participant resolution and graceful degradation
-------------------------------------------------
For each burst group, the sender identity is normalized into the same
``has-email``/``has-handle`` object encoding ``relationship`` uses when
writing ``entity_facts`` (see ``roster/relationship/tools/relationship_assert_fact.py``
and migrations 019/027/028): email addresses are reduced to a bare lowercased
address via ``butlers.identity.normalize_email_sender`` (bu-qeaou). Gmail's
``source_sender_identity`` is normalized to this bare form at ingest time
(``connectors/gmail.py``), but this adapter still applies the same
normalization defensively — it is a no-op on already-normalized rows and keeps
historical pre-bu-qeaou rows (raw ``"Name <addr>"`` headers) matching
correctly. Telegram identities are ``telegram:``-prefixed;
Discord/WhatsApp identities are matched verbatim (no channel prefix — those
two channels are not currently auto-linked by ingress, so verbatim match is a
best-effort lookup that only succeeds if the owner separately registered a
``has-handle`` fact for that identity).

When resolution succeeds, the episode names the participant and carries two
independent evidence kinds (message boundary + entity resolution) -> ``high``
confidence, and an ``episode_entities`` row is written with
``role='participant'``. When resolution fails, the episode is still emitted
(never dropped) with an unattributed participant, a single evidence kind ->
``low`` confidence, and no participant row — this is the fail-soft path the
spec requires ("Unresolved participant degrades gracefully").

No LLM call — Tier-1 deterministic projection only (RFC 0014 §D5).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from butlers.chronicler.adapters._owner_entity import (
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.confidence import (
    EvidenceKind,
    derive_confidence,
    evidence_refs_from_event_ids,
)
from butlers.chronicler.models import (
    Episode,
    Layer,
    LinkRelation,
    PointEvent,
    Precision,
    Privacy,
)
from butlers.chronicler.storage import (
    link_event_to_episode,
    upsert_episode,
    upsert_point_event,
)
from butlers.identity import normalize_email_sender as _normalize_email_sender

logger = logging.getLogger(__name__)

SOURCE_NAME = "comms.message_bursts"
EVENT_TYPE_MESSAGE = "message_received"
EPISODE_TYPE_SOCIAL = "social_episode"
_EVIDENCE_TABLE = "public.ingestion_events"

DEFAULT_BATCH_LIMIT = 500

# Consecutive messages from the same (channel, sender) separated by more than
# this threshold start a new burst/episode. Deliberately shorter than
# OwnTracks' MOVEMENT_GAP_MINUTES (30) — a message conversation that has gone
# quiet for 20+ minutes reads as "a different occasion" to a human reviewer.
BURST_GAP_MINUTES = 20

# The only source_channel values this adapter reads. Every other channel
# (e.g. home_assistant sensor streams, api-triggered ingests) is out of scope.
_COMMS_CHANNELS: tuple[str, ...] = (
    "email",
    "telegram_bot",
    "telegram_user_client",
    "whatsapp_user_client",
    "discord",
)

# Channel -> human-readable label for episode/point-event titles. Public
# (also used by GET /api/chronicler/who-you-were-with to label companions'
# payload.channel values without re-deriving the same table).
CHANNEL_LABELS: dict[str, str] = {
    "email": "email",
    "telegram_bot": "Telegram",
    "telegram_user_client": "Telegram",
    "whatsapp_user_client": "WhatsApp",
    "discord": "Discord",
}

# Channels whose relationship.entity_facts objects are Telegram-prefixed
# (see roster/relationship/migrations/019_prefix_telegram_has_handle.py and
# 027_prefix_telegram_ingress_handles.py).
_TELEGRAM_CHANNELS: frozenset[str] = frozenset({"telegram_bot", "telegram_user_client"})


def channel_label_for(channel: str) -> str:
    """Human-readable label for a comms channel value, falling back to itself."""
    return CHANNEL_LABELS.get(channel, channel)


def _match_object_for(channel: str, sender_identity: str) -> str:
    """Return the ``relationship.entity_facts.object`` value to match against.

    Mirrors the encoding ``relationship`` uses when writing facts for each
    channel (see module docstring). Channels with no reliable prefix
    convention (discord, whatsapp_user_client) match verbatim.
    """
    if channel == "email":
        return _normalize_email_sender(sender_identity)
    if channel in _TELEGRAM_CHANNELS:
        stripped = sender_identity.strip()
        return stripped if stripped.startswith("telegram:") else f"telegram:{stripped}"
    return sender_identity.strip()


def _predicate_for(channel: str) -> str:
    return "has-email" if channel == "email" else "has-handle"


@dataclass(frozen=True)
class _BurstGroupKey:
    channel: str
    sender_identity: str


@dataclass(frozen=True)
class _ResolvedParticipant:
    entity_id: UUID
    display_name: str | None


class CommsSocialAdapter(ProjectionAdapter):
    """Project comms message bursts into ``social_episode`` activity candidates."""

    def __init__(self, *, batch_limit: int = DEFAULT_BATCH_LIMIT) -> None:
        super().__init__(SOURCE_NAME)
        self.batch_limit = batch_limit

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        """Fetch new comms ingestion events; emit one social_episode per burst.

        ``since_id`` is ignored: ``public.ingestion_events.id`` is a UUID7,
        while ``projection_checkpoints.watermark_id`` is an integer field for
        serial-id sources (same rationale as ``OwnTracksPointAdapter``).
        """
        del since_id
        result = AdapterResult(source_name=self.source_name)

        rows = await self._fetch_events(pool, since)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_EVIDENCE_TABLE} not found; comms evidence surface unavailable"
            )
            return result

        if not rows:
            result.watermark = since
            return result

        latest_watermark = since
        for row in rows:
            received_at = row["received_at"]
            if received_at is not None and (
                latest_watermark is None or received_at > latest_watermark
            ):
                latest_watermark = received_at

        groups = self._group_into_bursts(rows)

        # Resolve owner + participant entities once per run (not per burst).
        owner_entity_id = await resolve_owner_entity_id(pool)
        participant_map = await self._resolve_participants(pool, groups.keys())

        for key, burst_rows in groups.items():
            for segment in self._segment_burst(burst_rows):
                episode = await self._project_segment(
                    chronicler_pool,
                    key=key,
                    segment=segment,
                    owner_entity_id=owner_entity_id,
                    participant=participant_map.get(key),
                )
                if episode is None:
                    continue
                result.rows_projected += len(segment)
                result.point_events += len(segment)
                result.episodes_closed += 1

        result.watermark = latest_watermark
        return result

    # ── Fetch ────────────────────────────────────────────────────────────

    async def _fetch_events(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
    ) -> list[asyncpg.Record] | None:
        """Fetch comms ingestion events since the watermark.

        Only ``status = 'ingested'`` rows are read — filtered/failed/
        replay-pending events never represent lived message activity.
        Returns ``None`` if ``public.ingestion_events`` is missing entirely
        (degrade gracefully; this should never happen in practice since the
        table is core, but mirrors the optional-schema guard convention).
        """
        try:
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'public' AND table_name = 'ingestion_events'
                    )
                    """
                )
                if not exists:
                    return None

                if since is None:
                    rows = await conn.fetch(
                        """
                        SELECT id, received_at, source_channel, source_sender_identity
                        FROM public.ingestion_events
                        WHERE source_channel = ANY($1::text[])
                          AND status = 'ingested'
                          AND source_sender_identity IS NOT NULL
                        ORDER BY received_at ASC, id ASC
                        LIMIT $2
                        """,
                        list(_COMMS_CHANNELS),
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        """
                        SELECT id, received_at, source_channel, source_sender_identity
                        FROM public.ingestion_events
                        WHERE source_channel = ANY($1::text[])
                          AND status = 'ingested'
                          AND source_sender_identity IS NOT NULL
                          AND received_at > $2
                        ORDER BY received_at ASC, id ASC
                        LIMIT $3
                        """,
                        list(_COMMS_CHANNELS),
                        since,
                        self.batch_limit,
                    )
        except asyncpg.PostgresError:
            logger.exception("Failed reading %s for comms projection", _EVIDENCE_TABLE)
            return None
        return list(rows)

    # ── Burst grouping ──────────────────────────────────────────────────

    @staticmethod
    def _group_into_bursts(
        rows: list[asyncpg.Record],
    ) -> dict[_BurstGroupKey, list[asyncpg.Record]]:
        """Group fetched rows by ``(channel, sender_identity)``, order preserved."""
        groups: dict[_BurstGroupKey, list[asyncpg.Record]] = {}
        for row in rows:
            key = _BurstGroupKey(
                channel=row["source_channel"],
                sender_identity=row["source_sender_identity"],
            )
            groups.setdefault(key, []).append(row)
        return groups

    def _segment_burst(self, rows: list[asyncpg.Record]) -> list[list[asyncpg.Record]]:
        """Split one sender's rows into gap-bounded burst segments."""
        gap = timedelta(minutes=BURST_GAP_MINUTES)
        segments: list[list[asyncpg.Record]] = []
        current: list[asyncpg.Record] = [rows[0]]
        for row in rows[1:]:
            if row["received_at"] - current[-1]["received_at"] <= gap:
                current.append(row)
            else:
                segments.append(current)
                current = [row]
        segments.append(current)
        return segments

    # ── Participant resolution ──────────────────────────────────────────

    async def _resolve_participants(
        self,
        pool: asyncpg.Pool,
        keys: Any,
    ) -> dict[_BurstGroupKey, _ResolvedParticipant]:
        """Resolve each distinct ``(channel, sender_identity)`` to an entity.

        Joins a batched ``UNNEST`` target list against
        ``relationship.entity_facts`` -> ``public.entities`` in a single query
        (mirrors ``CoreSessionsAdapter._resolve_contacts``, batched instead of
        per-session). Degrades to an empty mapping — every burst falls
        through to the unattributed path — if ``relationship.entity_facts``
        or ``public.entities`` is absent (module not enabled on this
        deployment) or the query otherwise fails.
        """
        keys = list(keys)
        if not keys:
            return {}

        channels = [k.channel for k in keys]
        senders = [k.sender_identity for k in keys]
        match_objects = [_match_object_for(k.channel, k.sender_identity) for k in keys]
        predicates = [_predicate_for(k.channel) for k in keys]

        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    WITH targets AS (
                        SELECT * FROM UNNEST(
                            $1::text[], $2::text[], $3::text[], $4::text[]
                        ) AS t(channel, sender_identity, match_object, predicate)
                    )
                    SELECT t.channel, t.sender_identity, ef.subject AS entity_id,
                           e.canonical_name AS display_name
                    FROM targets t
                    JOIN relationship.entity_facts ef
                        ON ef.predicate = t.predicate
                       AND ef.object = t.match_object
                       AND ef.object_kind = 'literal'
                       AND ef.validity = 'active'
                    LEFT JOIN public.entities e ON e.id = ef.subject
                    """,
                    channels,
                    senders,
                    match_objects,
                    predicates,
                )
        except asyncpg.PostgresError:
            logger.debug(
                "CommsSocialAdapter: participant resolution query failed "
                "(relationship.entity_facts absent or unreachable); "
                "all bursts fall back to unattributed",
                exc_info=True,
            )
            return {}

        resolved: dict[_BurstGroupKey, _ResolvedParticipant] = {}
        for row in rows:
            key = _BurstGroupKey(channel=row["channel"], sender_identity=row["sender_identity"])
            entity_id = row["entity_id"]
            if entity_id is None:
                continue
            entity_uuid = entity_id if isinstance(entity_id, UUID) else UUID(str(entity_id))
            resolved[key] = _ResolvedParticipant(
                entity_id=entity_uuid, display_name=row["display_name"]
            )
        return resolved

    # ── Projection ───────────────────────────────────────────────────────

    async def _project_segment(
        self,
        chronicler_pool: asyncpg.Pool,
        *,
        key: _BurstGroupKey,
        segment: list[asyncpg.Record],
        owner_entity_id: UUID | None,
        participant: _ResolvedParticipant | None,
    ) -> Episode | None:
        first = segment[0]
        last = segment[-1]
        start_at: datetime = first["received_at"]
        end_at: datetime = last["received_at"]
        channel_label = channel_label_for(key.channel)

        async with chronicler_pool.acquire() as conn:
            async with conn.transaction():
                # One evidence-layer point event per ingested message.
                event_ids: list[UUID] = []
                for row in segment:
                    event = await upsert_point_event(
                        conn,
                        PointEvent(
                            source_name=self.source_name,
                            source_ref=f"public.ingestion_events:{row['id']}",
                            event_type=EVENT_TYPE_MESSAGE,
                            occurred_at=row["received_at"],
                            precision=Precision.EXACT,
                            title=f"Message via {channel_label}",
                            payload={"channel": key.channel},
                            privacy=Privacy.NORMAL,
                            layer=Layer.EVIDENCE,
                        ),
                    )
                    if event.id is not None:
                        event_ids.append(event.id)

                participant_resolved = participant is not None
                evidence_kinds = [EvidenceKind(name="message_channel")]
                if participant_resolved:
                    evidence_kinds.append(EvidenceKind(name="entity_resolution"))
                confidence = derive_confidence(evidence_kinds)
                evidence_refs = evidence_refs_from_event_ids(event_ids)

                if participant_resolved and participant.display_name:
                    title = f"Messages with {participant.display_name}"
                elif participant_resolved:
                    title = f"Messages with a resolved contact via {channel_label}"
                else:
                    title = f"Messages via {channel_label}"

                start_ts = int(start_at.timestamp())
                source_ref = (
                    f"public.ingestion_events:social:{key.channel}:{key.sender_identity}:{start_ts}"
                )

                payload = {
                    "channel": key.channel,
                    "message_count": len(segment),
                    "participant_status": "resolved" if participant_resolved else "unattributed",
                }

                episode = await upsert_episode(
                    conn,
                    Episode(
                        source_name=self.source_name,
                        source_ref=source_ref,
                        episode_type=EPISODE_TYPE_SOCIAL,
                        start_at=start_at,
                        end_at=end_at,
                        precision=Precision.EXACT,
                        title=title,
                        payload=payload,
                        privacy=Privacy.NORMAL,
                        layer=Layer.ACTIVITY,
                        confidence=confidence,
                        evidence_refs=evidence_refs,
                    ),
                )

                if episode.id is not None:
                    await upsert_owner_episode_entity(conn, episode.id, owner_id=owner_entity_id)
                    if participant is not None:
                        await conn.execute(
                            """
                            INSERT INTO episode_entities (episode_id, entity_id, role)
                            VALUES ($1, $2, 'participant')
                            ON CONFLICT (episode_id, entity_id)
                            DO UPDATE SET role = EXCLUDED.role
                            """,
                            episode.id,
                            participant.entity_id,
                        )
                    for event_id in event_ids:
                        await link_event_to_episode(
                            conn,
                            episode_id=episode.id,
                            event_id=event_id,
                            relation=LinkRelation.EVIDENCE,
                        )
        return episode


__all__ = [
    "BURST_GAP_MINUTES",
    "CHANNEL_LABELS",
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_SOCIAL",
    "EVENT_TYPE_MESSAGE",
    "SOURCE_NAME",
    "CommsSocialAdapter",
    "channel_label_for",
]
