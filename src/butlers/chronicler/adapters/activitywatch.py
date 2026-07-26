"""ActivityWatch desktop-activity projection adapter.

Projects window-focus events from ``connectors.activitywatch_events`` into
two Chronicler output layers (bu-whhll.6, epic bu-whhll Tier 1):

1. **``app_focus`` point events** — one per non-AFK evidence row. Each point
   event records a single window-focus interval's app-class and duration.
   ``source_ref`` = ``connectors.activitywatch_events:{idempotency_key}``
   so replays are idempotent.

2. **``screen_episode`` rollups** — contiguous sequences of non-AFK rows
   within ``SCREEN_GAP_MINUTES`` of each other are collapsed into a single
   episode whose (start_at, end_at) span the sequence, with a per-app-class
   duration breakdown (``ide_seconds`` / ``terminal_seconds`` /
   ``browser_seconds`` / ``other_seconds``) and a ``dominant_app_class``.

   The Chronicler ``Episode`` model itself has no category column — categories
   are assigned out-of-band by ``aggregations.category_for()`` keyed on
   ``(source_name, episode_type)``. ``screen_episode`` is mapped there to the
   ``"tasks"`` category (Work lane) since no dedicated ``"occupation"``
   category exists yet (that lands with Tier 2 of epic bu-whhll, routine
   inference). ``dominant_app_class`` is carried in the episode payload
   precisely so a future occupation-classifier can refine work-vs-not-work
   without re-reading the raw evidence table.

Privacy (window titles default privacy=sensitive; normal views receive only
app class, duration, and a validated browser hostname when available):
- Raw window titles and raw ``app`` process names are NEVER read from the
  evidence table into a projected point event or episode payload. Only the
  connector-computed ``app_class`` bucket (``ide`` / ``terminal`` /
  ``browser`` / ``other``), duration, and a validated browser hostname are
  projected. Raw browser URLs and web-watcher tab titles remain in the
  evidence table's sensitive ``raw_payload`` and are never selected here.
  Both point events and episodes are stamped ``Privacy.NORMAL`` — a future
  title-surfacing view (if ever built) MUST stamp ``Privacy.SENSITIVE``
  instead; this adapter never builds one.

AFK handling:
- Rows with ``is_afk = true`` are excluded from both point events and
  episode duration (idle time is not screen activity).
- Rows with ``is_afk = NULL`` (no AFK bucket on that machine) are treated as
  active — the AFK watcher is optional infrastructure; its absence should
  not silently drop all activity from a machine that never installed it.

Semantics:
- Boundary precision is ``exact`` — ActivityWatch window-watcher timestamps
  carry sub-second resolution.
- No LLM call per event — Tier-0 projection only (RFC 0014 §D5).
- Missing evidence table degrades gracefully (connector not deployed /
  migration not run on this deployment).
- Watermark on ``ts`` only. ``connectors.activitywatch_events.id`` is a
  UUID, not an integer serial, so this adapter must not use the integer
  ``watermark_id`` tuple-watermark path (mirrors the OwnTracks adapter).
"""

from __future__ import annotations

import logging
import math
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
    get_carryover,
    link_event_to_episode,
    save_carryover,
    upsert_episode,
    upsert_point_event,
)

logger = logging.getLogger(__name__)

SOURCE_NAME = "activitywatch.window"
EVENT_TYPE_APP_FOCUS = "app_focus"
EPISODE_TYPE_SCREEN = "screen_episode"
_EVIDENCE_TABLE = "connectors.activitywatch_events"
DEFAULT_BATCH_LIMIT = 1000

# Consecutive active (non-AFK) rows separated by more than this threshold
# start a new screen episode.
SCREEN_GAP_MINUTES = 10

_APP_CLASSES = ("ide", "terminal", "browser", "other")


def _safe_browser_domain(raw: object) -> str | None:
    """Validate an evidence hostname before exposing it in normal projections.

    The connector writes a hostname derived from an HTTP(S) URL. This second
    guard deliberately accepts only a plain ASCII hostname, so an accidental
    raw URL/path/query/title in evidence cannot cross into Chronicler output.
    """

    if not isinstance(raw, str):
        return None
    hostname = raw.strip().lower().rstrip(".")
    if not hostname or len(hostname) > 253:
        return None

    labels = hostname.split(".")
    if any(
        not label
        or len(label) > 63
        or not label[0].isalnum()
        or not label[-1].isalnum()
        or any(not (char.isascii() and (char.isalnum() or char == "-")) for char in label)
        for label in labels
    ):
        return None
    return hostname


class ActivityWatchWindowAdapter(ProjectionAdapter):
    """Project ``connectors.activitywatch_events`` rows into Chronicler.

    Each active (non-AFK) row -> one ``app_focus`` point event.
    Contiguous active rows within ``SCREEN_GAP_MINUTES`` are collapsed into
    ``screen_episode`` spans with a per-app-class duration breakdown.
    """

    def __init__(
        self,
        *,
        batch_limit: int = DEFAULT_BATCH_LIMIT,
        screen_gap_minutes: int = SCREEN_GAP_MINUTES,
    ) -> None:
        super().__init__(SOURCE_NAME)
        self.batch_limit = batch_limit
        if screen_gap_minutes <= 0:
            raise ValueError(f"screen_gap_minutes must be positive, got {screen_gap_minutes}")
        self.screen_gap = timedelta(minutes=screen_gap_minutes)

    async def project(
        self,
        pool: asyncpg.Pool,
        *,
        chronicler_pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> AdapterResult:
        result = AdapterResult(source_name=self.source_name)

        rows = await self._fetch_events(pool, since)
        if rows is None:
            result.skipped = True
            result.skipped_reason = (
                f"{_EVIDENCE_TABLE} not found; ActivityWatch evidence surface unavailable"
            )
            return result

        if not rows:
            result.watermark = since
            return result

        latest_watermark = since
        active_rows: list[dict[str, Any]] = []
        event_id_by_key: dict[str, UUID] = {}

        for row in rows:
            ts = row["ts"]
            if isinstance(ts, datetime) and ts.tzinfo is not None:
                if latest_watermark is None or ts > latest_watermark:
                    latest_watermark = ts

            normalized_row, warnings = self._normalize_row(row)
            for warning in warnings:
                logger.warning("%s", warning)
                result.warnings.append(warning)
            if normalized_row is None:
                continue

            # AFK rows never become point events or count toward screen time,
            # but they still advance the watermark (handled above).
            if normalized_row["is_afk"] is True:
                continue

            active_rows.append(normalized_row)
            event = await self._project_point_event(chronicler_pool, normalized_row)
            if event is not None and event.id is not None:
                event_id_by_key[normalized_row["idempotency_key"]] = event.id
            result.rows_projected += 1
            result.point_events += 1

        if active_rows:
            entity_id = await resolve_owner_entity_id(pool)
            prior_carryover = await get_carryover(chronicler_pool, self.source_name)
            episodes_closed, new_carryover = await self._project_screen_episodes(
                chronicler_pool,
                active_rows,
                prior_carryover,
                event_id_by_key=event_id_by_key,
                entity_id=entity_id,
            )
            result.episodes_closed += episodes_closed
            await save_carryover(chronicler_pool, self.source_name, new_carryover)

        result.watermark = latest_watermark
        return result

    def _normalize_row(
        self,
        row: asyncpg.Record,
    ) -> tuple[dict[str, Any] | None, list[str]]:
        """Return a sanitized row dict (or None) and a list of warnings."""
        row_ref = self._row_reference(row)
        warnings: list[str] = []

        ts = row["ts"]
        if not isinstance(ts, datetime) or ts.tzinfo is None:
            return None, [
                f"Skipping malformed ActivityWatch row {row_ref}: ts must be timezone-aware"
            ]

        idempotency_key = row["idempotency_key"]
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            return None, [
                f"Skipping malformed ActivityWatch row {row_ref}: idempotency_key missing"
            ]

        endpoint_identity = row["endpoint_identity"]
        if not isinstance(endpoint_identity, str) or not endpoint_identity.strip():
            return None, [
                f"Skipping malformed ActivityWatch row {row_ref}: endpoint_identity missing"
            ]

        app_class = row["app_class"]
        if app_class not in _APP_CLASSES:
            warnings.append(
                f"ActivityWatch row {row_ref} has unrecognized app_class={app_class!r}; "
                "treating as 'other'"
            )
            app_class = "other"

        duration_seconds = row["duration_seconds"]
        try:
            duration_seconds = max(0.0, float(duration_seconds))
        except (TypeError, ValueError):
            return None, [
                f"Skipping malformed ActivityWatch row {row_ref}: duration_seconds invalid"
            ]

        try:
            raw_browser_domain = row["browser_domain"]
        except (IndexError, KeyError):
            raw_browser_domain = None
        browser_domain = (
            _safe_browser_domain(raw_browser_domain) if app_class == "browser" else None
        )

        normalized = {
            "id": row["id"],
            "idempotency_key": idempotency_key.strip(),
            "ts": ts,
            "duration_seconds": duration_seconds,
            "app_class": app_class,
            "is_afk": row["is_afk"],
            "endpoint_identity": endpoint_identity.strip(),
            "browser_domain": browser_domain,
        }
        return normalized, warnings

    @staticmethod
    def _row_reference(row: asyncpg.Record) -> str:
        idempotency_key = row["idempotency_key"]
        if isinstance(idempotency_key, str) and idempotency_key.strip():
            return idempotency_key.strip()
        row_id = row["id"]
        return str(row_id) if row_id is not None else "<unknown>"

    async def _fetch_events(
        self,
        pool: asyncpg.Pool,
        since: datetime | None,
        since_id: int | None = None,
    ) -> list[asyncpg.Record] | None:
        """Fetch evidence rows since the watermark.

        ``since_id`` is intentionally ignored (see module docstring — the
        evidence table primary key is UUID). Returns ``None`` if the
        evidence table is missing — degrade gracefully.
        """
        del since_id
        try:
            async with pool.acquire() as conn:
                exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1 FROM information_schema.tables
                        WHERE table_schema = 'connectors'
                          AND table_name = 'activitywatch_events'
                    )
                    """
                )
                if not exists:
                    return None

                if since is None:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, idempotency_key, ts, duration_seconds,
                               app_class, browser_domain, is_afk, endpoint_identity
                        FROM {_EVIDENCE_TABLE}
                        ORDER BY ts ASC, id ASC
                        LIMIT $1
                        """,
                        self.batch_limit,
                    )
                else:
                    rows = await conn.fetch(
                        f"""
                        SELECT id, idempotency_key, ts, duration_seconds,
                               app_class, browser_domain, is_afk, endpoint_identity
                        FROM {_EVIDENCE_TABLE}
                        WHERE ts > $1
                        ORDER BY ts ASC, id ASC
                        LIMIT $2
                        """,
                        since,
                        self.batch_limit,
                    )
        except asyncpg.PostgresError:
            logger.exception("Failed reading %s", _EVIDENCE_TABLE)
            return None

        return list(rows)

    async def _project_point_event(
        self,
        chronicler_pool: asyncpg.Pool,
        row: dict[str, Any],
    ) -> PointEvent:
        idempotency_key = row["idempotency_key"]
        source_ref = f"{_EVIDENCE_TABLE}:{idempotency_key}"
        app_class = row["app_class"]
        duration_seconds = row["duration_seconds"]

        browser_domain = (
            _safe_browser_domain(row.get("browser_domain")) if app_class == "browser" else None
        )
        if browser_domain is not None:
            title = f"Browser activity ({browser_domain}, {round(duration_seconds)}s)"
        else:
            title = f"{app_class.capitalize()} activity ({round(duration_seconds)}s)"

        # Privacy: safe app class, duration, and optional validated hostname
        # only — never a raw app name, URL, or title (see module docstring).
        payload: dict = {
            "app_class": app_class,
            "duration_seconds": duration_seconds,
        }
        if browser_domain is not None:
            payload["browser_domain"] = browser_domain

        async with chronicler_pool.acquire() as conn:
            event = await upsert_point_event(
                conn,
                PointEvent(
                    source_name=self.source_name,
                    source_ref=source_ref,
                    event_type=EVENT_TYPE_APP_FOCUS,
                    occurred_at=row["ts"],
                    precision=Precision.EXACT,
                    title=title,
                    payload=payload,
                    privacy=Privacy.NORMAL,
                    layer=Layer.EVIDENCE,
                ),
            )
        return event

    async def _project_screen_episodes(
        self,
        chronicler_pool: asyncpg.Pool,
        rows: list[dict[str, Any]],
        prior_carryover: dict,
        *,
        event_id_by_key: dict[str, UUID] | None = None,
        entity_id: UUID | None = None,
    ) -> tuple[int, dict]:
        """Collapse active-row sequences into screen episodes.

        ``prior_carryover`` is keyed by ``endpoint_identity`` (typically a
        single key — one ActivityWatch connector instance per machine) and
        stores the open episode's ``start_at`` plus its running per-app-class
        second totals, so an episode spanning multiple adapter runs is
        extended rather than fragmented.

        Returns ``(episodes_upserted, new_carryover)``.
        """
        if not rows:
            return 0, {}

        event_id_by_key = event_id_by_key or {}
        rows = sorted(rows, key=lambda r: r["ts"])
        gap = self.screen_gap
        episodes_upserted = 0

        segments: list[dict] = []
        first_row = rows[0]
        endpoint = first_row["endpoint_identity"]

        carry = prior_carryover.get(endpoint)
        seg_start_at, seg_class_seconds = self._resolve_carryover(
            carry=carry, row_ts=first_row["ts"], gap=gap
        )
        seg_browser_domain_seconds = (
            self._resolve_browser_domain_carryover(carry) if seg_start_at is not None else {}
        )

        current: list[dict[str, Any]] = [first_row]
        current_start_at = seg_start_at
        current_class_seconds = seg_class_seconds
        current_browser_domain_seconds = seg_browser_domain_seconds

        for row in rows[1:]:
            prev = current[-1]
            same_identity = row["endpoint_identity"] == prev["endpoint_identity"]
            # Gap is measured from where the *previous* row's activity ends
            # (ts + duration_seconds) to where the next row starts — not
            # start-to-start. Using start-to-start overestimates the true
            # gap for any row with non-trivial duration (e.g. a sustained
            # multi-hour IDE session), incorrectly fragmenting one
            # continuous session into multiple episodes. This mirrors
            # `_resolve_carryover`'s cross-batch boundary, which already
            # gates on `end_at` rather than `start_at`.
            prev_end_at = prev["ts"] + timedelta(seconds=prev["duration_seconds"])
            time_gap = row["ts"] - prev_end_at
            if same_identity and time_gap <= gap:
                current.append(row)
            else:
                segments.append(
                    {
                        "rows": current,
                        "start_at": current_start_at,
                        "class_seconds": current_class_seconds,
                        "browser_domain_seconds": current_browser_domain_seconds,
                    }
                )
                current = [row]
                new_endpoint = row["endpoint_identity"]
                new_carry = prior_carryover.get(new_endpoint)
                current_start_at, current_class_seconds = self._resolve_carryover(
                    carry=new_carry, row_ts=row["ts"], gap=gap
                )
                current_browser_domain_seconds = (
                    self._resolve_browser_domain_carryover(new_carry)
                    if current_start_at is not None
                    else {}
                )
        segments.append(
            {
                "rows": current,
                "start_at": current_start_at,
                "class_seconds": current_class_seconds,
                "browser_domain_seconds": current_browser_domain_seconds,
            }
        )

        new_carryover: dict = {}

        for seg in segments:
            seg_rows: list[dict[str, Any]] = seg["rows"]
            first = seg_rows[0]
            last = seg_rows[-1]

            effective_start_at: datetime = seg["start_at"] if seg["start_at"] else first["ts"]
            end_at: datetime = last["ts"] + timedelta(seconds=last["duration_seconds"])
            endpoint_identity: str = first["endpoint_identity"]

            class_seconds: dict[str, float] = dict(seg["class_seconds"] or {})
            browser_domain_seconds: dict[str, float] = dict(seg["browser_domain_seconds"] or {})
            for row in seg_rows:
                class_seconds[row["app_class"]] = (
                    class_seconds.get(row["app_class"], 0.0) + row["duration_seconds"]
                )
                browser_domain = (
                    _safe_browser_domain(row.get("browser_domain"))
                    if row["app_class"] == "browser"
                    else None
                )
                if browser_domain is not None:
                    browser_domain_seconds[browser_domain] = (
                        browser_domain_seconds.get(browser_domain, 0.0) + row["duration_seconds"]
                    )

            if end_at < effective_start_at:
                logger.warning(
                    "Inverted screen episode for %s (start_at=%s > end_at=%s); swapping bounds.",
                    endpoint_identity,
                    effective_start_at.isoformat(),
                    end_at.isoformat(),
                )
                effective_start_at, end_at = end_at, effective_start_at

            dominant_app_class = max(_APP_CLASSES, key=lambda cls: class_seconds.get(cls, 0.0))
            point_count = len(seg_rows)
            title = f"Screen activity ({point_count} events, dominant: {dominant_app_class})"

            source_ref = (
                f"{_EVIDENCE_TABLE}:screen:{endpoint_identity}:"
                f"{int(effective_start_at.timestamp())}"
            )

            payload: dict = {
                "endpoint_identity": endpoint_identity,
                "point_count": point_count,
                "dominant_app_class": dominant_app_class,
                **{f"{cls}_seconds": class_seconds.get(cls, 0.0) for cls in _APP_CLASSES},
            }
            if browser_domain_seconds:
                payload["browser_domain_seconds"] = {
                    domain: browser_domain_seconds[domain]
                    for domain in sorted(browser_domain_seconds)
                }

            seg_event_ids = [
                event_id_by_key[r["idempotency_key"]]
                for r in seg_rows
                if r["idempotency_key"] in event_id_by_key
            ]
            evidence_refs = evidence_refs_from_event_ids(seg_event_ids)

            # Single weak evidence kind (desktop window-focus signal, no
            # independent corroboration) -> low confidence, still counted.
            confidence = derive_confidence([EvidenceKind(name="desktop_activity")])

            async with chronicler_pool.acquire() as conn:
                episode = await upsert_episode(
                    conn,
                    Episode(
                        source_name=self.source_name,
                        source_ref=source_ref,
                        episode_type=EPISODE_TYPE_SCREEN,
                        start_at=effective_start_at,
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
                ep_id = episode.id if episode is not None else None
                await upsert_owner_episode_entity(conn, ep_id, owner_id=entity_id)
                if ep_id is not None:
                    for event_id in seg_event_ids:
                        await link_event_to_episode(
                            conn,
                            episode_id=ep_id,
                            event_id=event_id,
                            relation=LinkRelation.EVIDENCE,
                        )
            episodes_upserted += 1

            new_carryover[endpoint_identity] = {
                "start_at": effective_start_at.isoformat(),
                "end_at": end_at.isoformat(),
                "class_seconds": class_seconds,
                "browser_domain_seconds": browser_domain_seconds,
            }

        return episodes_upserted, new_carryover

    @staticmethod
    def _resolve_carryover(
        *,
        carry: Any,
        row_ts: datetime,
        gap: timedelta,
    ) -> tuple[datetime | None, dict[str, float]]:
        """Validate ``carry`` and return ``(start_at, class_seconds)`` to extend, or (None, {})."""
        if not isinstance(carry, dict):
            return None, {}

        try:
            raw_start_at = carry["start_at"]
            raw_end_at = carry["end_at"]
        except KeyError:
            return None, {}

        try:
            prior_start_at = datetime.fromisoformat(raw_start_at)
            prior_end_at = datetime.fromisoformat(raw_end_at)
        except (TypeError, ValueError):
            logger.warning("Discarding screen-episode carryover with invalid timestamps: %r", carry)
            return None, {}

        if prior_start_at.tzinfo is None or prior_end_at.tzinfo is None:
            logger.warning("Discarding naive (tz-less) screen-episode carryover: %r", carry)
            return None, {}

        if prior_start_at > prior_end_at or prior_end_at > row_ts or (row_ts - prior_end_at) > gap:
            return None, {}

        class_seconds = carry.get("class_seconds")
        if not isinstance(class_seconds, dict):
            class_seconds = {}
        else:
            class_seconds = {
                cls: float(secs)
                for cls, secs in class_seconds.items()
                if cls in _APP_CLASSES and isinstance(secs, int | float)
            }

        return prior_start_at, class_seconds

    @staticmethod
    def _resolve_browser_domain_carryover(carry: Any) -> dict[str, float]:
        """Return only valid, finite hostname totals from an open episode."""
        if not isinstance(carry, dict):
            return {}
        raw_domain_seconds = carry.get("browser_domain_seconds")
        if not isinstance(raw_domain_seconds, dict):
            return {}

        domain_seconds: dict[str, float] = {}
        for raw_domain, raw_seconds in raw_domain_seconds.items():
            domain = _safe_browser_domain(raw_domain)
            if domain is None or not isinstance(raw_seconds, int | float):
                continue
            seconds = float(raw_seconds)
            if not math.isfinite(seconds) or seconds < 0:
                continue
            domain_seconds[domain] = domain_seconds.get(domain, 0.0) + seconds
        return domain_seconds


__all__ = [
    "DEFAULT_BATCH_LIMIT",
    "EPISODE_TYPE_SCREEN",
    "EVENT_TYPE_APP_FOCUS",
    "SCREEN_GAP_MINUTES",
    "ActivityWatchWindowAdapter",
    "SOURCE_NAME",
]
