"""Home Assistant non-person sensor-activity projection adapter (bu-49fqa).

Mines ``connectors.filtered_events`` (rows written by the Home Assistant
connector for allow-listed non-``person`` domains — see
``docs/plans/2026-07-06-telemetry-distillation-design.md`` §3.1 and
``openspec/changes/chronicler-telemetry-distillation``) for two ambient
signal shapes, bead-1 scope only (design.md §6 item 1 — the fuller §3.1
rule table also lists a ``light``/``switch`` ``device_usage_episode`` row,
deliberately deferred to a later bead; see the module-level ``[decision]``
notes below):

- ``binary_sensor`` / ``device_class=motion`` "on" transitions cluster
  (gap-tolerant, like ``owntracks.py``'s movement clustering) into
  ``room_activity_episode`` rows.
- ``binary_sensor`` / ``device_class in {door, garage_door, opening}``
  transitions each project one instantaneous ``entry_event`` point event.

Every other domain/device_class (including ``person``, persisted separately
by ``HomeAssistantHistoryAdapter``) is left untouched — exactly today's
status quo, per the design's "unclassified allow-listed domains" scenario.

Layer semantics (spec: "Evidence promotes to activity only with a
corroborator"):
    ``room_activity_episode`` rows are projected at ``layer=evidence`` by
    default. A span is promoted to ``layer=activity, confidence=low`` only
    when it overlaps an independent corroborator — a
    ``chronicler.occupation_inferred``/``occupation_block`` episode or a
    ``spotify.session_summary``/``listening_episode`` episode. This mirrors
    ``OccupationInferredAdapter``'s own corroborator-lookup-then-emit
    pattern ("same posture as occupation_inferred" — design.md §5 Risks)
    rather than routing through ``reconciliation.py``'s day-close merge
    pass, which only runs once per day and only ever adjudicates
    ``activity``/``intent`` rows — it has no evidence-to-activity promotion
    hook today. Building one would be a `reconciliation.py` behavior change
    with system-wide blast radius, out of scope for a single adapter bead.
    ``[decision]``, recorded here per the worker skill's decision-autonomy
    protocol.

    Corroboration is evaluated twice: once when the span is first clustered
    from this run's batch, and again by the bounded retroactive re-check
    pass described below. The second evaluation exists because corroborators
    arrive late — the hourly ``chronicler.occupation_inferred`` cron tick
    routinely fires *after* a motion span has already been clustered, and
    without a re-check that span would stay ``layer=evidence`` forever
    despite having since earned promotion (bu-mul8i).

Retroactive promotion re-check (bu-mul8i):
    After clustering, every run re-evaluates ``room_activity_episode`` rows
    still at ``layer=evidence`` whose ``start_at`` falls inside the trailing
    ``promotion_lookback_hours`` window (default
    ``PROMOTION_LOOKBACK_HOURS``, overridable per-job via
    ``job_args.promotion_lookback_hours`` — see
    ``chronicler/jobs.py::run_project_home_assistant_sensor_activity``).
    The window is what keeps this bounded: it is a trailing re-check, never
    a full-history sweep, so cost stays flat as ``episodes`` grows.

    The re-check reuses the *same* corroboration predicate as the clustering
    path (``_corroborator_episode_ids``) — deliberately one function, not two
    copies of the rule, so promotion and first-projection can never drift
    apart. A qualifying span is flipped to ``layer=activity,
    confidence=low`` with the corroborator ids written to ``evidence_refs``,
    exactly as the clustering path would have done had the corroborator been
    there at the time.

    Idempotent by construction: the UPDATE is guarded on
    ``layer = 'evidence'``, so an already-promoted span is neither re-matched
    by the candidate SELECT nor re-written by a concurrent run, and
    ``updated_at`` does not churn on a no-op pass. Demotion is *not*
    implemented — nothing in the existing code demotes, and inventing a
    reverse rule here would silently contradict the clustering path.

    Observability: each pass logs promoted spans and returns the count as
    ``AdapterResult.episodes_promoted`` (persisted in the job result by
    ``chronicler/jobs.py::_adapter_result_to_dict``). ``[decision]`` The
    ``chronicles`` fleet-event payload published by ``_run_adapter`` is
    deliberately left unchanged — its shape is asserted wire-exactly by
    consumers' tests, and a promotion-only run advertising a
    freshness ping with all-zero counters would be less honest than no ping
    at all. A promotion-only tick therefore does not publish; the next
    material projection or rollup run refreshes downstream aggregates.

Lane discipline (bu-whhll.14 composition, design §1.5): ``room_activity_episode``
resolves to the new ``ambient`` category → ``rest`` lane
(``aggregations.py``), and MUST NEVER resolve to ``work``/``occupation`` —
this is what keeps ambient HA sensor evidence from re-opening the
work/occupation lane-conflation problem bu-whhll.14 fixes elsewhere.

Watermark semantics — ``[decision]``, deviation from the dispatched
meals.py precedent:
    The dispatch brief cited ``meals.py``'s ``ROW_NUMBER() OVER (...)``
    synthesized-sequence tuple-watermark as the pattern to follow for a
    UUID-keyed evidence table. ``connectors.filtered_events`` for
    ``connector_type='home_assistant'`` is multiple orders of magnitude
    larger than ``health.meals`` (observed >1.6M rows in a single
    deployment sample) and permanently growing; re-computing
    ``ROW_NUMBER()`` over the *entire* filtered set on every 15–30 minute
    cron tick would mean an ever-more-expensive full sort with no bound.
    This adapter instead follows the *other* established precedent for
    exactly this situation — ``HomeAssistantHistoryAdapter`` and
    ``OwnTracksPointAdapter``, both of which read a UUID-keyed,
    unbounded-growth connector evidence table and use a single-column
    ``WHERE received_at > $1`` watermark, leaving ``watermark_id`` unset.
    The residual risk (two rows sharing the exact same ``received_at``
    instant at a batch boundary could theoretically split across batches
    and one could be missed) is accepted: ``received_at`` carries
    microsecond ``timestamptz`` precision so exact collisions are rare, and
    this source is ambient evidence-layer signal, not exact accounting.

Retention-window awareness (design §5 / §2 principle 6 — the first
Chronicler adapter to read a table with a rolling TTL instead of a
TTL-free connector table or chronicler's own schema): after every run,
this adapter compares its new watermark against the oldest still-retained
``connectors.filtered_events_YYYYMM`` partition
(``jobs/retention.py::prune_filtered_events_partitions`` drops partitions
older than ``keep_months``, default 12) and appends an
``AdapterResult.warnings`` entry (surfaced via the job's persisted result,
``chronicler/jobs.py::_adapter_result_to_dict``) plus a ``logger.warning``
call when the watermark is within ``RETENTION_LAG_WARNING_DAYS`` of that
cutoff — i.e. the adapter is running dangerously far behind and risks
losing unprojected source days to the retention sweep before ever reading
them.

``full_payload`` decode note: some historical ``connectors.filtered_events``
rows are double-JSON-encoded (a jsonb *string* scalar rather than an
object) due to a since-fixed serialization bug predating the direct-dict
bind now used by ``FilteredEventBuffer.record()`` (see that module's
docstring, bu-dycxq). This adapter defensively unwraps both shapes, mirroring
the same ``isinstance(..., str)`` guard ``drain_replay_pending`` already
applies on the replay path.

No LLM call — Tier-0 deterministic projection only (RFC 0014 §D5).
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import asyncpg

from butlers.chronicler.adapters._owner_entity import (
    resolve_owner_entity_id,
    upsert_owner_episode_entity,
)
from butlers.chronicler.adapters.base import AdapterResult, ProjectionAdapter
from butlers.chronicler.confidence import evidence_refs_from_event_ids
from butlers.chronicler.models import Confidence, Episode, Layer, PointEvent, Precision, Privacy
from butlers.chronicler.storage import (
    get_carryover,
    save_carryover,
    upsert_episode,
    upsert_point_event,
)

logger = logging.getLogger(__name__)


def _now() -> datetime:
    """Wall-clock now, isolated for test patching (mirrors ``jobs.py``)."""
    return datetime.now(UTC)


SOURCE_NAME = "home_assistant.sensor_activity"
EPISODE_TYPE_ROOM_ACTIVITY = "room_activity_episode"
EVENT_TYPE_ENTRY = "entry_event"
_EVIDENCE_TABLE = "connectors.filtered_events"
_CONNECTOR_TYPE = "home_assistant"
DEFAULT_BATCH_LIMIT = 2000

# Gap tolerance for clustering motion "on" pings into one room_activity_episode
# span — gap-tolerant like OwnTracks' movement clustering (design §3.1), so a
# few minutes of no motion mid-activity doesn't fragment one lived block into
# many tiny episodes.
ROOM_ACTIVITY_GAP_MINUTES = 15

# device_class values this adapter classifies (bead-1 scope only — see module
# docstring on the deferred light/switch device_usage_episode row).
_MOTION_DEVICE_CLASS = "motion"
_ENTRY_DEVICE_CLASSES = frozenset({"door", "garage_door", "opening"})

# Corroborator sources gating evidence -> activity promotion for
# room_activity_episode (design §3.1 "Evidence promotes to activity only with
# a corroborator"). (source_name, episode_type) pairs, episode-shaped only —
# same shape as OccupationInferredAdapter's _CORROBORATOR_EPISODE_SOURCES.
_CORROBORATOR_EPISODE_SOURCES: tuple[tuple[str, str], ...] = (
    ("chronicler.occupation_inferred", "occupation_block"),
    ("spotify.session_summary", "listening_episode"),
)

# Trailing window for the retroactive evidence -> activity promotion re-check
# (bu-mul8i). Bounded on purpose: only spans starting within this many hours of
# now are re-evaluated, so the pass cost stays flat as `episodes` grows instead
# of degrading into a full-history sweep. 12h comfortably covers the hourly
# `chronicler.occupation_inferred` tick plus a wide margin for a backed-up
# scheduler, while staying far below the once-daily reconciliation horizon.
PROMOTION_LOOKBACK_HOURS = 12

# Safety cap on candidate spans examined per re-check pass. The lookback window
# is the real bound; this only stops a pathological burst of sensor churn from
# turning one tick into an unbounded row-by-row scan. Candidates are taken
# oldest-first (they are closest to falling out of the window), so in the
# pathological case the newest spans wait a tick — they are still inside the
# window next run. Realistic volume is far below the cap: 12h of 15-minute
# gap-tolerant clustering yields at most ~48 spans per motion entity.
PROMOTION_RECHECK_LIMIT = 1000

# Alert threshold for the retention-lag monitoring check (design §5 / §2.6):
# flag when the adapter's watermark is within this many days of the oldest
# still-retained connectors.filtered_events partition.
RETENTION_LAG_WARNING_DAYS = 30


class HomeAssistantSensorActivityAdapter(ProjectionAdapter):
    """Project non-person HA ``binary_sensor`` activity into Chronicler.

    Motion ("on" transitions) cluster into ``room_activity_episode`` spans
    (gap-tolerant, cross-batch carryover per entity). Door/garage/opening
    transitions each project one ``entry_event`` point event. Every other
    domain/device_class is left untouched (bead-1 scope).
    """

    def __init__(
        self,
        *,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
        room_activity_gap_minutes: int = ROOM_ACTIVITY_GAP_MINUTES,
        promotion_lookback_hours: int = PROMOTION_LOOKBACK_HOURS,
    ) -> None:
        super().__init__(SOURCE_NAME)
        self.batch_limit = batch_limit
        self.room_activity_gap_minutes = room_activity_gap_minutes
        self.promotion_lookback_hours = promotion_lookback_hours

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        del since_id  # UUID PK; single-column watermark only — see module docstring [decision].
        result = AdapterResult(source_name=self.source_name)

        rows = await self._fetch_rows(pool, since)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_EVIDENCE_TABLE} not found; filtered-events evidence surface unavailable"
            )
            return result

        if not rows:
            # No new source rows is the *common* case for a late-arriving
            # corroborator (the hourly occupation_inferred tick fires between
            # this adapter's ticks), so the re-check must run here too — not
            # only when there is fresh motion to cluster.
            result.watermark = since
            result.episodes_promoted += await self._recheck_evidence_promotions(chronicler_pool)
            return result

        latest_watermark = since
        motion_pings: dict[str, list[datetime]] = {}
        entry_rows: list[dict[str, Any]] = []

        for row in rows:
            received_at: datetime | None = row["received_at"]
            if received_at is not None and (
                latest_watermark is None or received_at > latest_watermark
            ):
                latest_watermark = received_at

            raw = self._extract_raw(row["full_payload"])
            if raw is None:
                continue

            domain = raw.get("domain")
            if domain != "binary_sensor":
                # bead-1 scope: person handled by HomeAssistantHistoryAdapter;
                # every other domain deliberately excluded from v1 (design §3.1).
                continue

            device_class = raw.get("device_class")
            entity_id = raw.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id:
                continue

            if device_class == _MOTION_DEVICE_CLASS:
                if self._state_value(raw.get("new_state")) == "on" and received_at is not None:
                    motion_pings.setdefault(entity_id, []).append(received_at)
                    result.rows_projected += 1
            elif device_class in _ENTRY_DEVICE_CLASSES and received_at is not None:
                entry_rows.append(
                    {
                        "row_id": row["id"],
                        "received_at": received_at,
                        "entity_id": entity_id,
                        "device_class": device_class,
                        "raw": raw,
                    }
                )
                result.rows_projected += 1
            # else: unclassified device_class on an allow-listed domain — not
            # projected, exactly today's status quo (design §3.1 scenario).

        owner_id = await resolve_owner_entity_id(pool)

        for erow in entry_rows:
            await self._project_entry_event(chronicler_pool, erow, owner_id=owner_id)
            result.point_events += 1

        if motion_pings:
            prior_carryover = await get_carryover(chronicler_pool, self.source_name)
            episodes_upserted, new_carryover, cluster_warnings = await self._project_room_activity(
                chronicler_pool, motion_pings, prior_carryover, owner_id=owner_id
            )
            result.episodes_closed += episodes_upserted
            result.warnings.extend(cluster_warnings)
            await save_carryover(chronicler_pool, self.source_name, new_carryover)

        result.episodes_promoted += await self._recheck_evidence_promotions(chronicler_pool)

        lag_warning = await self._check_retention_lag(pool, latest_watermark)
        if lag_warning is not None:
            result.warnings.append(lag_warning)
            logger.warning("%s", lag_warning)

        result.watermark = latest_watermark
        return result

    # ------------------------------------------------------------------
    # Fetch
    # ------------------------------------------------------------------

    async def _fetch_rows(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
    ) -> list[asyncpg.Record] | None:
        """Fetch ``connectors.filtered_events`` rows for the HA connector.

        Domain filtering (``binary_sensor`` vs. everything else) cannot be
        pushed into SQL — ``domain`` lives inside ``full_payload`` JSONB, not
        a real column — so every home_assistant-connector row since the
        watermark is fetched and classified in Python, matching
        ``HomeAssistantHistoryAdapter``'s own "fetch all rows, filter
        in-Python" shape for its sibling table.

        Returns ``None`` if the evidence table is missing — degrade
        gracefully per RFC 0014 optional-schema guard.
        """
        try:
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'connectors'
                          AND table_name = 'filtered_events'
                    )
                    """
                )
                if not exists:
                    return None

                if since is None:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, received_at, full_payload
                        FROM {_EVIDENCE_TABLE}
                        WHERE connector_type = $1
                        ORDER BY received_at ASC, id ASC
                        LIMIT $2
                        """,
                        _CONNECTOR_TYPE,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, received_at, full_payload
                        FROM {_EVIDENCE_TABLE}
                        WHERE connector_type = $1 AND received_at > $2
                        ORDER BY received_at ASC, id ASC
                        LIMIT $3
                        """,
                        _CONNECTOR_TYPE,
                        since,
                        self.batch_limit,
                    )
        except asyncpg.PostgresError:
            logger.exception("Failed reading %s", _EVIDENCE_TABLE)
            return None

        return list(rows)

    # ------------------------------------------------------------------
    # Payload decode
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_raw(full_payload: Any) -> dict[str, Any] | None:
        """Decode ``full_payload`` and return its ``payload.raw`` dict, or ``None``.

        Handles both the correct (dict) shape and the legacy double-JSON-
        encoded (str) shape — see module docstring.
        """
        payload: Any = full_payload
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except json.JSONDecodeError:
                return None
        if not isinstance(payload, dict):
            return None

        inner = payload.get("payload")
        if not isinstance(inner, dict):
            return None
        raw = inner.get("raw")
        return raw if isinstance(raw, dict) else None

    @staticmethod
    def _state_value(state_obj: Any) -> str | None:
        """Extract the ``state`` string from an HA old_state/new_state dict."""
        if isinstance(state_obj, dict):
            value = state_obj.get("state")
            return str(value) if value is not None else None
        if isinstance(state_obj, str):
            return state_obj
        return None

    # ------------------------------------------------------------------
    # entry_event (door / garage_door / opening) — point events
    # ------------------------------------------------------------------

    async def _project_entry_event(
        self,
        chronicler_pool: asyncpg.Pool,
        erow: dict[str, Any],
        *,
        owner_id: UUID | None,
    ) -> PointEvent:
        entity_id: str = erow["entity_id"]
        row_id = erow["row_id"]
        raw: dict[str, Any] = erow["raw"]
        device_class: str = erow["device_class"]

        new_state = self._state_value(raw.get("new_state"))
        old_state = self._state_value(raw.get("old_state"))
        friendly_name = raw.get("friendly_name")
        label = friendly_name if isinstance(friendly_name, str) and friendly_name else entity_id
        title = f"{label}: {new_state}" if new_state else label

        source_ref = f"{_EVIDENCE_TABLE}:sensor_activity:entry:{row_id}"
        payload: dict[str, Any] = {
            "entity_id": entity_id,
            "device_class": device_class,
            "old_state": old_state,
            "new_state": new_state,
        }

        async with chronicler_pool.acquire() as conn:
            event = await upsert_point_event(
                conn,
                PointEvent(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    event_type=EVENT_TYPE_ENTRY,
                    occurred_at=erow["received_at"],
                    precision=Precision.EXACT,
                    title=title,
                    payload=payload,
                    privacy=Privacy.NORMAL,
                    entity_id=owner_id,
                    layer=Layer.EVIDENCE,
                ),
            )
        return event

    # ------------------------------------------------------------------
    # room_activity_episode (motion) — gap-tolerant clustering
    # ------------------------------------------------------------------

    async def _project_room_activity(
        self,
        chronicler_pool: asyncpg.Pool,
        motion_pings: dict[str, list[datetime]],
        prior_carryover: dict,
        *,
        owner_id: UUID | None,
    ) -> tuple[int, dict, list[str]]:
        """Cluster motion "on" pings per entity into room_activity_episode spans."""
        gap = timedelta(minutes=self.room_activity_gap_minutes)
        episodes_upserted = 0
        new_carryover: dict[str, Any] = {}
        warnings: list[str] = []

        for entity_id, pings in motion_pings.items():
            pings_sorted = sorted(pings)

            carry = prior_carryover.get(entity_id)
            existing_source_ref: str | None = None
            prior_start_at: datetime | None = None
            if carry:
                resolved = self._resolve_carryover(carry, pings_sorted[0], gap)
                if resolved is not None:
                    existing_source_ref, prior_start_at = resolved
                else:
                    warnings.append(
                        f"Discarding stale/malformed room-activity carryover for {entity_id}"
                    )

            segments: list[list[datetime]] = []
            seg_source_refs: list[str | None] = []
            seg_prior_starts: list[datetime | None] = []

            current = [pings_sorted[0]]
            current_source_ref = existing_source_ref
            current_prior_start = prior_start_at

            for ts in pings_sorted[1:]:
                if ts - current[-1] <= gap:
                    current.append(ts)
                else:
                    segments.append(current)
                    seg_source_refs.append(current_source_ref)
                    seg_prior_starts.append(current_prior_start)
                    current = [ts]
                    current_source_ref = None
                    current_prior_start = None
            segments.append(current)
            seg_source_refs.append(current_source_ref)
            seg_prior_starts.append(current_prior_start)

            last_episode: Episode | None = None
            for seg, seg_ref, seg_prior_start in zip(
                segments, seg_source_refs, seg_prior_starts, strict=True
            ):
                start_at = seg_prior_start if seg_prior_start is not None else seg[0]
                end_at = seg[-1]
                last_episode = await self._upsert_room_activity_episode(
                    chronicler_pool,
                    entity_id,
                    start_at,
                    end_at,
                    seg_ref,
                    owner_id=owner_id,
                )
                episodes_upserted += 1

            # The last segment may still be open (motion could continue past
            # this batch's window) — always carry it over, same posture as
            # OwnTracks' movement-episode clustering.
            if last_episode is not None:
                new_carryover[entity_id] = {
                    "source_ref": last_episode.source_ref,
                    "start_at": last_episode.start_at.isoformat(),
                    "end_at": last_episode.end_at.isoformat() if last_episode.end_at else None,
                }

        return episodes_upserted, new_carryover, warnings

    @staticmethod
    def _resolve_carryover(
        carry: Any,
        first_ping: datetime,
        gap: timedelta,
    ) -> tuple[str, datetime] | None:
        """Validate carryover and decide whether it extends into this batch.

        Returns ``(source_ref, prior_start_at)`` when well-formed and within
        ``gap`` of ``first_ping``; ``None`` when the carryover should be
        discarded (malformed, or the gap since the prior batch's last ping
        is too large — a fresh episode starts instead).
        """
        if not isinstance(carry, dict):
            return None
        try:
            source_ref = carry["source_ref"]
            start_at = datetime.fromisoformat(carry["start_at"])
            end_at = datetime.fromisoformat(carry["end_at"])
        except (KeyError, ValueError, TypeError):
            return None
        if not isinstance(source_ref, str) or not source_ref.strip():
            return None
        if start_at.tzinfo is None or end_at.tzinfo is None:
            return None
        if first_ping - end_at > gap:
            return None
        return source_ref.strip(), start_at

    async def _upsert_room_activity_episode(
        self,
        chronicler_pool: asyncpg.Pool,
        entity_id: str,
        start_at: datetime,
        end_at: datetime,
        existing_source_ref: str | None,
        *,
        owner_id: UUID | None,
    ) -> Episode:
        if existing_source_ref is not None:
            source_ref = existing_source_ref
        else:
            start_tst = int(start_at.timestamp())
            source_ref = f"{_EVIDENCE_TABLE}:sensor_activity:room:{entity_id}:{start_tst}"

        short_name = entity_id.split(".", 1)[-1].replace("_", " ").title()
        title = f"Motion: {short_name}"
        payload: dict[str, Any] = {"entity_id": entity_id, "device_class": _MOTION_DEVICE_CLASS}

        async with chronicler_pool.acquire() as conn:
            corroborator_ids = await self._corroborator_episode_ids(conn, start_at, end_at)

            if corroborator_ids:
                layer = Layer.ACTIVITY
                confidence = Confidence.LOW
                evidence_refs = evidence_refs_from_event_ids(corroborator_ids)
            else:
                # Uncorroborated ambient motion — evidence only, never counted
                # in any lane aggregate (spec: "SHALL NOT be counted").
                layer = Layer.EVIDENCE
                confidence = Confidence.LOW
                evidence_refs = []

            episode = await upsert_episode(
                conn,
                Episode(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    episode_type=EPISODE_TYPE_ROOM_ACTIVITY,
                    start_at=start_at,
                    end_at=end_at,
                    precision=Precision.EXACT,
                    title=title,
                    payload=payload,
                    privacy=Privacy.NORMAL,
                    layer=layer,
                    confidence=confidence,
                    evidence_refs=evidence_refs,
                ),
            )
            await upsert_owner_episode_entity(conn, episode.id, owner_id=owner_id)
        return episode

    @classmethod
    async def _corroborator_episode_ids(
        cls,
        conn: asyncpg.Connection,
        start_at: datetime,
        end_at: datetime,
    ) -> list[UUID]:
        """Return corroborating episode ids overlapping ``[start_at, end_at)``.

        THE corroboration predicate for ``room_activity_episode`` — the single
        definition shared by the clustering path
        (:meth:`_upsert_room_activity_episode`) and the retroactive re-check
        pass (:meth:`_recheck_evidence_promotions`). Keep it that way: two
        copies of this rule would drift, and a span would then be promoted on
        one path but not the other.
        """
        ids: list[UUID] = []
        for source in _CORROBORATOR_EPISODE_SOURCES:
            ids.extend(await cls._fetch_overlapping_episode_ids(conn, source, start_at, end_at))
        return ids

    @staticmethod
    async def _fetch_overlapping_episode_ids(
        conn: asyncpg.Connection,
        source: tuple[str, str],
        start_at: datetime,
        end_at: datetime,
    ) -> list[UUID]:
        """Return ids of ``(source_name, episode_type)`` episodes overlapping the span.

        Same shape as ``OccupationInferredAdapter._fetch_episode_ids`` — the
        established corroborator-lookup pattern for this "evidence needs a
        corroborator before it counts" posture.
        """
        source_name, episode_type = source
        rows = await conn.fetch(
            """
            SELECT id FROM episodes
            WHERE tombstone_at IS NULL
              AND source_name = $1
              AND episode_type = $2
              AND start_at < $4
              AND (end_at IS NULL OR end_at > $3)
            ORDER BY start_at ASC, id ASC
            """,
            source_name,
            episode_type,
            start_at,
            end_at,
        )
        return [r["id"] for r in rows]

    # ------------------------------------------------------------------
    # Retroactive evidence -> activity promotion re-check (bu-mul8i)
    # ------------------------------------------------------------------

    async def _recheck_evidence_promotions(self, chronicler_pool: asyncpg.Pool) -> int:
        """Re-evaluate recent evidence-layer spans and promote the ones that qualify.

        Bounded to spans starting within the trailing
        ``promotion_lookback_hours`` window (plus a
        ``PROMOTION_RECHECK_LIMIT`` safety cap) and idempotent: the UPDATE is
        guarded on ``layer = 'evidence'``, and a promoted span no longer
        matches the candidate SELECT, so a second pass touches nothing.

        Cost is two indexed corroborator lookups per candidate span — a
        handful of narrow queries per tick at realistic sensor volumes, which
        is why the pass is affordable inline on every run rather than needing
        a schedule of its own.

        Returns the number of spans promoted.
        """
        cutoff = _now() - timedelta(hours=self.promotion_lookback_hours)
        promoted = 0

        async with chronicler_pool.acquire() as conn:
            candidates = await conn.fetch(
                """
                SELECT id, start_at, end_at FROM episodes
                WHERE tombstone_at IS NULL
                  AND source_name = $1
                  AND episode_type = $2
                  AND layer = $3
                  AND start_at >= $4
                ORDER BY start_at ASC, id ASC
                LIMIT $5
                """,
                self.source_name,
                EPISODE_TYPE_ROOM_ACTIVITY,
                Layer.EVIDENCE.value,
                cutoff,
                PROMOTION_RECHECK_LIMIT,
            )

            for row in candidates:
                start_at: datetime = row["start_at"]
                # Defensive: an open-ended span has no end_at yet; treat the
                # instant it started as its window, matching how the clustering
                # path evaluates a single-ping (start == end) span.
                end_at: datetime = row["end_at"] or start_at

                corroborator_ids = await self._corroborator_episode_ids(conn, start_at, end_at)
                if not corroborator_ids:
                    continue

                updated = await conn.execute(
                    """
                    UPDATE episodes
                    SET layer = $2,
                        confidence = $3,
                        evidence_refs = $4,
                        updated_at = now()
                    WHERE id = $1
                      AND layer = $5
                      AND tombstone_at IS NULL
                    """,
                    row["id"],
                    Layer.ACTIVITY.value,
                    Confidence.LOW.value,
                    evidence_refs_from_event_ids(corroborator_ids),
                    Layer.EVIDENCE.value,
                )
                if updated == "UPDATE 0":
                    # Another run promoted it between our SELECT and UPDATE.
                    continue

                promoted += 1
                logger.info(
                    "%s: retroactively promoted %s %s (%s) to layer=activity on %d corroborator(s)",
                    SOURCE_NAME,
                    EPISODE_TYPE_ROOM_ACTIVITY,
                    row["id"],
                    start_at.isoformat(),
                    len(corroborator_ids),
                )

        return promoted

    # ------------------------------------------------------------------
    # Retention-lag monitoring (design §5 / §2.6)
    # ------------------------------------------------------------------

    @staticmethod
    async def _check_retention_lag(
        pool: asyncpg.Pool,
        watermark: datetime | None,
    ) -> str | None:
        """Warn when ``watermark`` is close to the oldest retained partition.

        ``connectors.filtered_events`` partitions are named
        ``filtered_events_YYYYMM`` and pruned by
        ``jobs/retention.py::prune_filtered_events_partitions`` (default
        keep_months=12). If this adapter's watermark is only barely ahead of
        the oldest *still-retained* partition, the adapter is running so far
        behind real time that source days it hasn't read yet risk being
        dropped by the retention sweep before they are ever projected — a
        genuinely new adapter-risk class (design §2.6) since every prior
        Chronicler adapter reads a TTL-free surface.
        """
        if watermark is None:
            return None
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch(
                    """
                    SELECT table_name FROM information_schema.tables
                    WHERE table_schema = 'connectors'
                      AND table_name LIKE 'filtered\\_events\\_%' ESCAPE '\\'
                      AND table_type = 'BASE TABLE'
                    """
                )
        except asyncpg.PostgresError:
            logger.debug("retention-lag check: failed to list filtered_events partitions")
            return None

        suffixes: list[str] = []
        for row in rows:
            name: str = row["table_name"]
            suffix = name.removeprefix("filtered_events_")
            if len(suffix) == 6 and suffix.isdigit():
                suffixes.append(suffix)

        if not suffixes:
            return None

        oldest = min(suffixes)
        oldest_year, oldest_month = int(oldest[:4]), int(oldest[4:6])
        oldest_start = datetime(oldest_year, oldest_month, 1, tzinfo=UTC)

        margin_days = (watermark - oldest_start).days
        if margin_days < RETENTION_LAG_WARNING_DAYS:
            return (
                f"{SOURCE_NAME}: checkpoint watermark ({watermark.date().isoformat()}) is only "
                f"{margin_days}d ahead of the oldest retained connectors.filtered_events "
                f"partition ({oldest}); within the {RETENTION_LAG_WARNING_DAYS}d safety margin — "
                "unprojected source days risk aging out under the retention sweep "
                "(jobs/retention.py::prune_filtered_events_partitions)."
            )
        return None


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_ROOM_ACTIVITY",
    "EVENT_TYPE_ENTRY",
    "PROMOTION_LOOKBACK_HOURS",
    "PROMOTION_RECHECK_LIMIT",
    "RETENTION_LAG_WARNING_DAYS",
    "ROOM_ACTIVITY_GAP_MINUTES",
    "HomeAssistantSensorActivityAdapter",
    "SOURCE_NAME",
]
