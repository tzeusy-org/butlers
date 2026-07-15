"""OwnTracks Wi-Fi SSID presence projection adapter (bu-whhll.5).

The adapter reads the optional uppercase ``SSID`` field already preserved in
``connectors.owntracks_points.raw_payload`` and rolls contiguous points on the
same mapped network into presence episodes.  It is independent of both
``owntracks.points`` movement rollups and ``owntracks.place_cluster`` GPS
dwells.

Owner configuration uses the existing per-butler JSONB state store.  State key
``chronicler/owntracks/ssid_places`` holds an exact, case-sensitive JSON object
mapping SSID names to the canonical places ``home`` or ``work``.  The generic
``PUT /api/butlers/chronicler/state/{key}`` surface makes the mapping editable
without a migration or a tracked config file containing private network names.

Unmapped or missing SSIDs never produce episodes and explicitly break a run,
so two observations cannot bridge across an unknown network.  Raw SSID names
remain in the source evidence/state mapping only: projected payloads carry
``payload.place`` and source refs use a one-way SSID digest.

``home`` projects ``presence_episode`` (Rest lane); ``work`` projects
``occupation_presence_episode`` (Work lane) and is also a weak corroborator for
the routine-based occupation adapter.  Both are ``layer=activity``,
``precision=minute``, and ``confidence=medium`` because an owner-declared SSID
mapping is one strong canonical signal, not two independent signals.

No LLM call — deterministic Tier-1 projection only (RFC 0014 D5).
"""

from __future__ import annotations

import hashlib
import json
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
from butlers.chronicler.models import Episode, Layer, Precision, Privacy
from butlers.chronicler.storage import (
    get_carryover,
    get_checkpoint,
    mark_source_active,
    save_carryover,
    upsert_checkpoint,
    upsert_episode,
)

logger = logging.getLogger(__name__)

SOURCE_NAME = "owntracks.ssid_presence"
EPISODE_TYPE_HOME_PRESENCE = "presence_episode"
EPISODE_TYPE_WORK_PRESENCE = "occupation_presence_episode"
SSID_PLACE_STATE_KEY = "chronicler/owntracks/ssid_places"
_EVIDENCE_TABLE = "connectors.owntracks_points"
_POINT_SOURCE_NAME = "owntracks.points"
_POINT_EVENT_TYPE = "location"

DEFAULT_BATCH_LIMIT = 1000
DEFAULT_MAX_GAP_MINUTES = 60
CLOCK_SKEW_THRESHOLD_HOURS = 4
_VALID_PLACES = frozenset({"home", "work"})
_MAPPING_DIGEST_KEY = "_mapping_digest"
_SOURCE_CURSOR_KEY = "_source_cursor"


def parse_ssid_places(value: Any) -> dict[str, str]:
    """Validate the owner-editable SSID-to-place state value.

    ``None`` means no networks have been labelled yet.  SSID matching remains
    exact and case-sensitive; place values are normalized to lowercase and are
    deliberately limited to ``home``/``work`` so taxonomy never guesses.
    """
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{SSID_PLACE_STATE_KEY} must be a JSON object mapping SSID to place")

    mapping: dict[str, str] = {}
    for raw_ssid, raw_place in value.items():
        if not isinstance(raw_ssid, str) or not raw_ssid.strip():
            raise ValueError(f"{SSID_PLACE_STATE_KEY} keys must be non-empty SSID strings")
        if not isinstance(raw_place, str) or raw_place.strip().lower() not in _VALID_PLACES:
            raise ValueError(f"{SSID_PLACE_STATE_KEY}[{raw_ssid!r}] must be one of: home, work")
        mapping[raw_ssid] = raw_place.strip().lower()
    return mapping


def _mapping_digest(ssid_places: dict[str, str]) -> str:
    serialized = json.dumps(ssid_places, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode()).hexdigest()


def _ssid_ref_digest(ssid: str) -> str:
    return hashlib.sha256(ssid.encode()).hexdigest()[:16]


@dataclass
class SsidPresenceSpan:
    endpoint_identity: str
    ssid: str
    place: str
    start_at: datetime
    end_at: datetime
    point_count: int

    def source_ref(self) -> str:
        return (
            f"{_EVIDENCE_TABLE}:ssid:{self.endpoint_identity}:"
            f"{_ssid_ref_digest(self.ssid)}:{int(self.start_at.timestamp())}"
        )


def group_ssid_points(
    rows: list[dict[str, Any]],
    *,
    ssid_places: dict[str, str],
    max_gap: timedelta,
    prior_carryover: dict[str, Any] | None = None,
) -> tuple[list[SsidPresenceSpan], dict[str, Any]]:
    """Group mapped same-SSID observations per endpoint.

    Missing/unmapped SSIDs are hard boundaries.  A presence span needs at
    least two observations; a singleton remains carryover so a later batch can
    establish duration without fabricating an instantaneous presence episode.
    """
    prior_carryover = prior_carryover or {}
    by_endpoint: dict[str, list[dict[str, Any]]] = {}
    for row in sorted(rows, key=lambda item: (item["ts"], item["endpoint_identity"])):
        by_endpoint.setdefault(row["endpoint_identity"], []).append(row)

    spans: list[SsidPresenceSpan] = []
    new_carryover: dict[str, Any] = {
        endpoint: value
        for endpoint, value in prior_carryover.items()
        if endpoint not in {_MAPPING_DIGEST_KEY, _SOURCE_CURSOR_KEY}
    }

    for endpoint, endpoint_rows in by_endpoint.items():
        current = _resume_span(
            prior_carryover.get(endpoint),
            endpoint=endpoint,
            first_row=endpoint_rows[0],
            ssid_places=ssid_places,
            max_gap=max_gap,
        )
        resumed = current is not None

        for index, row in enumerate(endpoint_rows):
            ssid = _ssid_from_row(row)
            place = ssid_places.get(ssid) if isinstance(ssid, str) else None
            if place is None:
                _append_if_presence(spans, current)
                current = None
                continue

            if resumed and index == 0:
                # The first row was already incorporated by _resume_span.
                continue

            if (
                current is not None
                and current.ssid == ssid
                and timedelta(0) <= row["ts"] - current.end_at <= max_gap
            ):
                current.end_at = row["ts"]
                current.point_count += 1
                continue

            _append_if_presence(spans, current)
            current = SsidPresenceSpan(
                endpoint_identity=endpoint,
                ssid=ssid,
                place=place,
                start_at=row["ts"],
                end_at=row["ts"],
                point_count=1,
            )

        _append_if_presence(spans, current)
        if current is not None:
            new_carryover[endpoint] = {
                "ssid": current.ssid,
                "start_at": current.start_at.isoformat(),
                "end_at": current.end_at.isoformat(),
                "point_count": current.point_count,
            }
        else:
            new_carryover.pop(endpoint, None)

    return sorted(spans, key=lambda span: (span.start_at, span.endpoint_identity)), new_carryover


def _append_if_presence(spans: list[SsidPresenceSpan], span: SsidPresenceSpan | None) -> None:
    if span is not None and span.point_count >= 2 and span.end_at > span.start_at:
        spans.append(span)


def _ssid_from_row(row: dict[str, Any]) -> str | None:
    """Read SSID from either a normalized row or OwnTracks evidence row."""
    ssid = row.get("ssid")
    if isinstance(ssid, str) and ssid.strip():
        return ssid

    raw_payload = row.get("raw_payload")
    if isinstance(raw_payload, str):
        try:
            raw_payload = json.loads(raw_payload)
        except json.JSONDecodeError:
            return None
    if not isinstance(raw_payload, dict):
        return None
    raw_ssid = raw_payload.get("SSID")
    return raw_ssid if isinstance(raw_ssid, str) and raw_ssid.strip() else None


def _resume_span(
    raw: Any,
    *,
    endpoint: str,
    first_row: dict[str, Any],
    ssid_places: dict[str, str],
    max_gap: timedelta,
) -> SsidPresenceSpan | None:
    if not isinstance(raw, dict):
        return None
    try:
        ssid = raw["ssid"]
        start_at = datetime.fromisoformat(raw["start_at"])
        end_at = datetime.fromisoformat(raw["end_at"])
        point_count = int(raw["point_count"])
    except (KeyError, TypeError, ValueError):
        logger.warning("Discarding malformed SSID carryover for %s", endpoint)
        return None

    first_ssid = _ssid_from_row(first_row)
    place = ssid_places.get(ssid) if isinstance(ssid, str) else None
    if (
        place is None
        or first_ssid != ssid
        or start_at.tzinfo is None
        or end_at.tzinfo is None
        or point_count <= 0
        or not timedelta(0) <= first_row["ts"] - end_at <= max_gap
    ):
        return None
    return SsidPresenceSpan(
        endpoint_identity=endpoint,
        ssid=ssid,
        place=place,
        start_at=start_at,
        end_at=first_row["ts"],
        point_count=point_count + 1,
    )


class OwnTracksSsidPresenceAdapter(ProjectionAdapter):
    """Project mapped OwnTracks SSID observations into presence episodes."""

    def __init__(
        self,
        *,
        ssid_places: dict[str, str],
        batch_limit: int = DEFAULT_BATCH_LIMIT,
        max_gap_minutes: int = DEFAULT_MAX_GAP_MINUTES,
        clock_skew_threshold_hours: int = CLOCK_SKEW_THRESHOLD_HOURS,
    ) -> None:
        super().__init__(SOURCE_NAME)
        if batch_limit <= 0:
            raise ValueError(f"batch_limit must be positive, got {batch_limit}")
        if max_gap_minutes <= 0:
            raise ValueError(f"max_gap_minutes must be positive, got {max_gap_minutes}")
        if clock_skew_threshold_hours < 0:
            raise ValueError(
                f"clock_skew_threshold_hours must be non-negative, got {clock_skew_threshold_hours}"
            )
        self.ssid_places = parse_ssid_places(ssid_places)
        self.batch_limit = batch_limit
        self.max_gap = timedelta(minutes=max_gap_minutes)
        self.clock_skew_threshold = timedelta(hours=clock_skew_threshold_hours)

    async def run(
        self,
        *,
        pool: asyncpg.Pool,
        chronicler_pool: asyncpg.Pool,
    ) -> AdapterResult:
        """Project and advance replay state in one Chronicler transaction."""
        self._llm_probe()

        try:
            async with chronicler_pool.acquire() as conn:
                async with conn.transaction():
                    await conn.execute(
                        """
                        SELECT pg_advisory_xact_lock(
                            hashtextextended('chronicler.projection:' || $1, 0)
                        )
                        """,
                        self.source_name,
                    )
                    checkpoint = await get_checkpoint(conn, self.source_name)
                    since = checkpoint.watermark if checkpoint is not None else None
                    since_id = checkpoint.watermark_id if checkpoint is not None else None
                    source_db = conn if pool is chronicler_pool else pool
                    result = await self.project(
                        source_db,
                        chronicler_pool=conn,
                        since=since,
                        since_id=since_id,
                    )

                    if result.skipped:
                        await mark_source_active(
                            conn,
                            self.source_name,
                            active=False,
                            inactive_reason=result.skipped_reason or "adapter skipped",
                        )
                        return result

                    if not result.success:
                        raise RuntimeError(result.error or "SSID presence projection failed")

                    await mark_source_active(conn, self.source_name, active=True)
                    await upsert_checkpoint(
                        conn,
                        self.source_name,
                        watermark=result.watermark,
                        watermark_id=result.watermark_id,
                        success=result.success,
                        rows_projected=result.rows_projected,
                        error=result.error,
                    )
            return result
        except Exception as exc:  # pragma: no cover - exercised by failure injection
            logger.exception("Adapter %s failed", self.source_name)
            try:
                await upsert_checkpoint(
                    chronicler_pool,
                    self.source_name,
                    success=False,
                    error=str(exc),
                )
            except Exception:
                logger.exception(
                    "Failed recording failure checkpoint for adapter %s",
                    self.source_name,
                )
            return AdapterResult(source_name=self.source_name, error=str(exc))

    async def project(
        self,
        pool: asyncpg.Pool | asyncpg.Connection,
        *,
        chronicler_pool: asyncpg.Pool | asyncpg.Connection,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        if isinstance(chronicler_pool, asyncpg.Pool):
            async with chronicler_pool.acquire() as conn:
                return await self._project_with_connection(
                    pool,
                    chronicler_conn=conn,
                    since=since,
                    since_id=since_id,
                )
        return await self._project_with_connection(
            pool,
            chronicler_conn=chronicler_pool,
            since=since,
            since_id=since_id,
        )

    async def _project_with_connection(
        self,
        pool: asyncpg.Pool | asyncpg.Connection,
        *,
        chronicler_conn: asyncpg.Connection,
        since: datetime | None,
        since_id: int | None,
    ) -> AdapterResult:
        del since_id  # owntracks_points.id is UUID; watermark on ts only.
        result = AdapterResult(source_name=self.source_name)
        prior_carryover = await get_carryover(chronicler_conn, self.source_name)
        digest = _mapping_digest(self.ssid_places)
        previous_digest = prior_carryover.get(_MAPPING_DIGEST_KEY)
        mapping_changed = previous_digest is not None and previous_digest != digest
        effective_since = None if mapping_changed else since

        since_uuid = self._uuid_tiebreaker(prior_carryover, effective_since)
        if effective_since is not None and since_uuid is None:
            result.warnings.append(
                "SSID source cursor missing or invalid; rebuilding from source evidence"
            )
            effective_since = None
            prior_carryover = {}
        rows = await self._fetch_points(pool, effective_since, since_uuid=since_uuid)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_EVIDENCE_TABLE} not found; OwnTracks evidence surface unavailable"
            )
            return result

        if mapping_changed:
            result.warnings.append("SSID place mapping changed; replaying source evidence")
            await self._tombstone_stale_mapping_episodes(chronicler_conn)
            prior_carryover = {}

        if not rows:
            unchanged_carryover = {**prior_carryover, _MAPPING_DIGEST_KEY: digest}
            await save_carryover(chronicler_conn, self.source_name, unchanged_carryover)
            result.watermark = effective_since
            return result

        latest_watermark = effective_since
        normalized_rows: list[dict[str, Any]] = []
        for row in rows:
            ts = row["ts"]
            if isinstance(ts, datetime) and ts.tzinfo is not None:
                if latest_watermark is None or ts > latest_watermark:
                    latest_watermark = ts
            normalized, warning = self._normalize_row(row)
            if warning:
                logger.warning("%s", warning)
                result.warnings.append(warning)
            if normalized is not None:
                normalized_rows.append(normalized)

        if normalized_rows:
            entity_id = await resolve_owner_entity_id(pool)
            spans, new_carryover = group_ssid_points(
                normalized_rows,
                ssid_places=self.ssid_places,
                max_gap=self.max_gap,
                prior_carryover=prior_carryover,
            )
            for span in spans:
                await self._upsert_presence_episode(chronicler_conn, span, entity_id=entity_id)
                result.rows_projected += 1
                result.episodes_closed += 1
        else:
            new_carryover = dict(prior_carryover)

        new_carryover[_MAPPING_DIGEST_KEY] = digest
        last_row = rows[-1]
        new_carryover[_SOURCE_CURSOR_KEY] = {
            "watermark": last_row["ts"].isoformat(),
            "uuid": str(last_row["id"]),
        }
        await save_carryover(chronicler_conn, self.source_name, new_carryover)
        result.watermark = latest_watermark
        return result

    @staticmethod
    def _uuid_tiebreaker(
        carryover: dict[str, Any],
        watermark: datetime | None,
    ) -> UUID | None:
        """Decode the UUID half of a source-local composite checkpoint.

        Timestamp-only checkpoints predate the UUID cursor. They intentionally
        return ``None`` so the caller performs one deterministic full replay
        with carryover rebuilt from source evidence before switching to tuple
        comparisons. Replaying only the timestamp boundary would double-count
        rows already represented by legacy open-span carryover.

        The replay remains batch-limited. Its first successful page writes the
        composite cursor, which is also the upgrade-completion marker. If the
        cursor and relational watermark ever disagree (for example after an
        interrupted legacy write), returning ``None`` restarts the bounded
        replay instead of risking a skip. New writes commit the cursor,
        carryover, projection rows, and relational watermark together in
        :meth:`run`.
        """
        raw = carryover.get(_SOURCE_CURSOR_KEY)
        if not isinstance(raw, dict) or watermark is None:
            return None
        try:
            cursor_watermark = datetime.fromisoformat(raw["watermark"])
            cursor_uuid = UUID(raw["uuid"])
        except (KeyError, TypeError, ValueError):
            logger.warning("Ignoring malformed UUID source cursor for %s", SOURCE_NAME)
            return None
        if cursor_watermark != watermark:
            logger.warning(
                "Ignoring UUID source cursor for %s because its watermark does not match",
                SOURCE_NAME,
            )
            return None
        return cursor_uuid

    def _normalize_row(self, row: asyncpg.Record) -> tuple[dict[str, Any] | None, str | None]:
        ts = row["ts"]
        row_ref = str(row["id"] or "<unknown>")
        if not isinstance(ts, datetime) or ts.tzinfo is None:
            return None, f"Skipping malformed OwnTracks SSID row {row_ref}: invalid ts"

        recorded_at = row["recorded_at"]
        if isinstance(recorded_at, datetime) and recorded_at.tzinfo is not None:
            if abs(ts - recorded_at) > self.clock_skew_threshold:
                ts = recorded_at

        endpoint = row["endpoint_identity"]
        if not isinstance(endpoint, str) or not endpoint.strip():
            return None, f"Skipping malformed OwnTracks SSID row {row_ref}: endpoint missing"

        raw_payload = row["raw_payload"]
        if isinstance(raw_payload, str):
            try:
                raw_payload = json.loads(raw_payload)
            except json.JSONDecodeError:
                raw_payload = None
        ssid: str | None = None
        if isinstance(raw_payload, dict):
            raw_ssid = raw_payload.get("SSID")
            if isinstance(raw_ssid, str) and raw_ssid.strip():
                ssid = raw_ssid

        return {
            "id": row["id"],
            "ts": ts,
            "endpoint_identity": endpoint.strip(),
            "ssid": ssid,
        }, None

    async def _fetch_points(
        self,
        pool: asyncpg.Pool | asyncpg.Connection,
        since: datetime | None,
        *,
        since_uuid: UUID | None,
    ) -> list[asyncpg.Record] | None:
        try:
            if isinstance(pool, asyncpg.Pool):
                async with pool.acquire() as conn:
                    rows = await self._fetch_points_on_connection(
                        conn,
                        since,
                        since_uuid=since_uuid,
                    )
            else:
                rows = await self._fetch_points_on_connection(
                    pool,
                    since,
                    since_uuid=since_uuid,
                )
        except asyncpg.PostgresError:
            logger.exception("Failed reading %s for SSID presence", _EVIDENCE_TABLE)
            return None
        return rows

    async def _fetch_points_on_connection(
        self,
        conn: asyncpg.Connection,
        since: datetime | None,
        *,
        since_uuid: UUID | None,
    ) -> list[asyncpg.Record] | None:
        exists = await conn.fetchval(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = 'connectors'
                  AND table_name = 'owntracks_points'
            )
            """
        )
        if not exists:
            return None
        if since is None or since_uuid is None:
            rows = await conn.fetch(
                f"""
                SELECT id, ts, endpoint_identity, raw_payload, recorded_at
                FROM {_EVIDENCE_TABLE}
                ORDER BY ts ASC, id ASC
                LIMIT $1
                """,
                self.batch_limit,
            )
        else:
            rows = await conn.fetch(
                f"""
                SELECT id, ts, endpoint_identity, raw_payload, recorded_at
                FROM {_EVIDENCE_TABLE}
                WHERE (ts, id) > ($1, $2)
                ORDER BY ts ASC, id ASC
                LIMIT $3
                """,
                since,
                since_uuid,
                self.batch_limit,
            )
        return list(rows)

    async def _tombstone_stale_mapping_episodes(self, conn: asyncpg.Connection) -> None:
        """Retire projections that are no longer valid under the owner mapping."""
        digests = [_ssid_ref_digest(ssid) for ssid in self.ssid_places]
        places = list(self.ssid_places.values())
        await conn.execute(
            """
            UPDATE episodes AS episode
            SET tombstone_at = now(),
                tombstone_reason = 'owner SSID mapping changed',
                updated_at = now()
            WHERE episode.source_name = $1
              AND episode.tombstone_at IS NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM unnest($2::text[], $3::text[]) AS valid(ssid_digest, place)
                  WHERE episode.source_ref ~ (':' || valid.ssid_digest || ':[0-9]+$')
                    AND episode.payload ->> 'place' = valid.place
              )
            """,
            self.source_name,
            digests,
            places,
        )

    async def _upsert_presence_episode(
        self,
        conn: asyncpg.Connection,
        span: SsidPresenceSpan,
        *,
        entity_id: UUID | None,
    ) -> Episode:
        episode_type = (
            EPISODE_TYPE_WORK_PRESENCE if span.place == "work" else EPISODE_TYPE_HOME_PRESENCE
        )
        evidence_ids = await self._fetch_location_point_event_ids(conn, span)
        episode = await upsert_episode(
            conn,
            Episode(
                source_name=self.source_name,
                source_ref=span.source_ref(),
                episode_type=episode_type,
                start_at=span.start_at,
                end_at=span.end_at,
                precision=Precision.MINUTE,
                title=f"At {span.place}",
                payload={
                    "place": span.place,
                    "point_count": span.point_count,
                    "endpoint_identity": span.endpoint_identity,
                },
                privacy=Privacy.NORMAL,
                layer=Layer.ACTIVITY,
                confidence=derive_confidence(
                    [EvidenceKind(name="owner_mapped_wifi_ssid", strong=True)]
                ),
                evidence_refs=evidence_refs_from_event_ids(evidence_ids),
            ),
        )
        await upsert_owner_episode_entity(conn, episode.id, owner_id=entity_id)
        return episode

    @staticmethod
    async def _fetch_location_point_event_ids(
        conn: asyncpg.Connection, span: SsidPresenceSpan
    ) -> list[UUID]:
        rows = await conn.fetch(
            """
            SELECT id FROM point_events
            WHERE tombstone_at IS NULL
              AND source_name = $1
              AND event_type = $2
              AND occurred_at >= $3
              AND occurred_at <= $4
              AND payload ->> 'endpoint_identity' = $5
            ORDER BY occurred_at ASC, id ASC
            """,
            _POINT_SOURCE_NAME,
            _POINT_EVENT_TYPE,
            span.start_at,
            span.end_at,
            span.endpoint_identity,
        )
        return [row["id"] for row in rows]


__all__ = [
    "CLOCK_SKEW_THRESHOLD_HOURS",
    "DEFAULT_BATCH_LIMIT",
    "DEFAULT_MAX_GAP_MINUTES",
    "EPISODE_TYPE_HOME_PRESENCE",
    "EPISODE_TYPE_WORK_PRESENCE",
    "SOURCE_NAME",
    "SSID_PLACE_STATE_KEY",
    "OwnTracksSsidPresenceAdapter",
    "SsidPresenceSpan",
    "group_ssid_points",
    "parse_ssid_places",
]
