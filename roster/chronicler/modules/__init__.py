"""Chronicler butler MCP module.

Registers the Tier-1 read tools (``chronicler_list_events``,
``chronicler_list_episodes``, ``chronicler_get_episode``,
``chronicler_submit_correction``, ``chronicler_list_corrections``) and the
Tier-2 bundle assembler tool (``chronicler_day_close_bundle``).

The bundle assembler tool is the entry-point for scheduled Tier-2 paths
(day-close, drilldown, etc.).  It applies sensitive masking, field stripping,
per-source roll-up, and hard cardinality caps before returning structured JSON
— guaranteeing the agent never receives an unbounded context payload.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from butlers.modules.base import Module

logger = logging.getLogger(__name__)


class ChroniclerModuleConfig(BaseModel):
    """Configuration for the Chronicler read/bundle tools module."""


class ChroniclerModule(Module):
    """Chronicler MCP module.

    Provides read tools and the day-close bundle assembler.
    """

    def __init__(self) -> None:
        self._db: Any = None

    @property
    def name(self) -> str:
        return "chronicler"

    @property
    def config_schema(self) -> type[BaseModel]:
        return ChroniclerModuleConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return None

    async def on_startup(
        self,
        config: Any,
        db: Any,
        credential_store: Any = None,
        blob_store: Any = None,
    ) -> None:
        self._db = db

    async def on_shutdown(self) -> None:
        self._db = None

    def _get_pool(self) -> Any:
        if self._db is None:
            raise RuntimeError("ChroniclerModule not initialised — no DB available")
        return self._db.pool

    async def register_tools(self, mcp: Any, config: Any, db: Any, butler_name: str) -> None:
        """Register all Chronicler MCP tools."""
        self._db = db
        _register_tools(mcp, self)


def _register_tools(mcp: Any, module: ChroniclerModule) -> None:
    """Register Chronicler read and bundle tools on *mcp*."""

    # ------------------------------------------------------------------
    # chronicler_list_events
    # ------------------------------------------------------------------

    @mcp.tool()
    async def chronicler_list_events(
        occurred_from: str | None = None,
        occurred_to: str | None = None,
        source_name: str | None = None,
        event_type: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List corrected point events within an optional time window.

        Args:
            occurred_from: ISO-8601 datetime lower bound (inclusive).
            occurred_to: ISO-8601 datetime upper bound (exclusive).
            source_name: Filter to a specific source adapter.
            event_type: Filter to a specific event type.
            limit: Maximum rows to return (max 500).
            offset: Row offset for pagination.

        Returns:
            ``{"data": [...], "count": int}`` — corrected point events.
            Sensitive events (``canonical_privacy='sensitive'``) are
            included in this read tool; masking is applied only by the
            bundle assembler path (``chronicler_day_close_bundle``).
        """
        from datetime import datetime

        from butlers.chronicler.storage import list_point_events

        limit = min(max(1, limit), 500)

        def _parse_dt(s: str | None) -> datetime | None:
            if s is None:
                return None
            return datetime.fromisoformat(s)

        pool = module._get_pool()
        rows = await list_point_events(
            pool,
            occurred_from=_parse_dt(occurred_from),
            occurred_to=_parse_dt(occurred_to),
            source_name=source_name,
            event_type=event_type,
            limit=limit,
            offset=offset,
        )
        from dataclasses import asdict

        return {"data": [asdict(r) for r in rows], "count": len(rows)}

    # ------------------------------------------------------------------
    # chronicler_list_episodes
    # ------------------------------------------------------------------

    @mcp.tool()
    async def chronicler_list_episodes(
        start_from: str | None = None,
        start_to: str | None = None,
        source_name: str | None = None,
        episode_type: str | None = None,
        participant_entity_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> dict[str, Any]:
        """List corrected episodes within an optional time window.

        Args:
            start_from: ISO-8601 datetime lower bound on episode start.
            start_to: ISO-8601 datetime upper bound on episode start (exclusive).
            source_name: Filter to a specific source adapter.
            episode_type: Filter to a specific episode type.
            participant_entity_id: Filter to episodes where this entity UUID
                appears in any role (owner, organizer, or participant) via the
                ``episode_entities`` join table.  Use this for entity activity
                feeds that should surface meetings where the entity attended
                but did not own the calendar.
            limit: Maximum rows to return (max 500).
            offset: Row offset for pagination.

        Returns:
            ``{"data": [...], "count": int}`` — corrected episodes.
        """
        from datetime import datetime
        from uuid import UUID

        from butlers.chronicler.storage import list_episodes

        limit = min(max(1, limit), 500)

        def _parse_dt(s: str | None) -> datetime | None:
            if s is None:
                return None
            return datetime.fromisoformat(s)

        pool = module._get_pool()
        rows = await list_episodes(
            pool,
            start_from=_parse_dt(start_from),
            start_to=_parse_dt(start_to),
            source_name=source_name,
            episode_type=episode_type,
            participant_entity_id=(
                UUID(participant_entity_id) if participant_entity_id not in (None, "") else None
            ),
            limit=limit,
            offset=offset,
        )
        from dataclasses import asdict

        return {"data": [asdict(r) for r in rows], "count": len(rows)}

    # ------------------------------------------------------------------
    # chronicler_get_episode
    # ------------------------------------------------------------------

    @mcp.tool()
    async def chronicler_get_episode(
        episode_id: str,
    ) -> dict[str, Any]:
        """Fetch a single corrected episode (with override overlay).

        Args:
            episode_id: UUID of the episode to fetch.

        Returns:
            The corrected episode row, or ``{"error": "not_found"}`` when
            the episode does not exist or is tombstoned.
        """
        from uuid import UUID

        from butlers.chronicler.storage import get_episode

        pool = module._get_pool()
        ep = await get_episode(pool, UUID(episode_id))
        if ep is None:
            return {"error": "not_found"}
        from dataclasses import asdict

        return asdict(ep)

    # ------------------------------------------------------------------
    # chronicler_submit_correction
    # ------------------------------------------------------------------

    @mcp.tool()
    async def chronicler_submit_correction(
        episode_id: str,
        corrected_title: str | None = None,
        corrected_start_at: str | None = None,
        corrected_end_at: str | None = None,
        corrected_privacy: str | None = None,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Submit a correction override for an episode.

        Corrections are additive: the canonical row is never mutated.
        The latest correction wins when building the corrected view.

        Args:
            episode_id: UUID of the target episode.
            corrected_title: New title for the episode.
            corrected_start_at: New start time (ISO-8601).
            corrected_end_at: New end time (ISO-8601).
            corrected_privacy: New privacy class (``normal`` / ``sensitive``).
            note: Free-form correction note (human-readable context).

        Returns:
            ``{"status": "ok", "override_id": "<uuid>"}`` on success.
        """
        from datetime import datetime
        from uuid import UUID

        from butlers.chronicler.models import Override, OverrideTarget, Privacy
        from butlers.chronicler.storage import insert_override

        def _parse_dt(s: str | None) -> datetime | None:
            if s is None:
                return None
            return datetime.fromisoformat(s)

        override = Override(
            target_kind=OverrideTarget.EPISODE,
            target_id=UUID(episode_id),
            corrected_title=corrected_title,
            corrected_start_at=_parse_dt(corrected_start_at),
            corrected_end_at=_parse_dt(corrected_end_at),
            corrected_privacy=Privacy(corrected_privacy) if corrected_privacy else None,
            note=note,
        )
        pool = module._get_pool()
        saved = await insert_override(pool, override)
        return {"status": "ok", "override_id": str(saved.id)}

    # ------------------------------------------------------------------
    # chronicler_list_corrections
    # ------------------------------------------------------------------

    @mcp.tool()
    async def chronicler_list_corrections(
        episode_id: str,
    ) -> dict[str, Any]:
        """List all correction overrides for an episode.

        Args:
            episode_id: UUID of the episode.

        Returns:
            ``{"data": [...], "count": int}`` — correction history,
            newest first.
        """
        from uuid import UUID

        from butlers.chronicler.models import OverrideTarget
        from butlers.chronicler.storage import list_overrides_for

        pool = module._get_pool()
        overrides = await list_overrides_for(
            pool,
            target_kind=OverrideTarget.EPISODE,
            target_id=UUID(episode_id),
        )
        from dataclasses import asdict

        return {"data": [asdict(o) for o in overrides], "count": len(overrides)}

    # ------------------------------------------------------------------
    # chronicler_day_close_bundle  — Tier-2 bounded assembler
    # ------------------------------------------------------------------

    @mcp.tool()
    async def chronicler_day_close_bundle(
        date_label: str,
        timezone: str = "UTC",
        max_episodes: int = 50,
        max_events: int = 100,
        rollup_threshold: int = 10,
        max_total_chars: int = 15_000,
    ) -> dict[str, Any]:
        """Return a token-bounded day-close bundle for the given date.

        Fetches all non-tombstoned episodes and events for *date_label*
        (``YYYY-MM-DD`` in *timezone*), then applies:

        1. **Sensitive masking** — ``canonical_privacy='sensitive'`` rows
           are excluded from the bundle unconditionally.
        2. **Field stripping** — low-signal payload keys are removed.
        3. **Per-source roll-up** — sources emitting > *rollup_threshold*
           items are collapsed to a count/time-range/subjects summary.
        4. **Hard cap** — episode and event counts are capped at
           *max_episodes* / *max_events*; the total bundle characters are
           capped at *max_total_chars*.

        The result is structured JSON suitable as-is for the
        ``chronicler_day_close`` interpretation prompt.

        Args:
            date_label: Date to close in ``YYYY-MM-DD`` format.
            timezone: IANA timezone used for date boundaries and display
                timestamps (default ``UTC``).
            max_episodes: Episode cap before serialization (default 50).
            max_events: Event cap before serialization (default 100).
            rollup_threshold: Per-source item count that triggers roll-up
                (default 10).
            max_total_chars: Hard limit on bundle JSON characters (default
                15 000).  Set to 0 to disable character cap.

        Returns:
            Pre-truncated bundle dict with keys ``date``, ``episodes``,
            ``events``, ``episodes_truncated``, ``events_truncated``,
            and ``citations``.
        """
        from datetime import UTC, datetime
        from zoneinfo import ZoneInfo

        from butlers.chronicler.bundle_assembler import BundleConfig, assemble_day_close_bundle
        from butlers.chronicler.storage import list_episodes, list_point_events

        # Parse date_label to a local calendar-day window, then query UTC instants.
        day = datetime.fromisoformat(date_label).date()
        tzinfo = ZoneInfo(timezone)
        start_at = datetime(day.year, day.month, day.day, tzinfo=tzinfo).astimezone(UTC)
        from datetime import timedelta

        end_at = datetime(day.year, day.month, day.day, tzinfo=tzinfo) + timedelta(days=1)
        end_at = end_at.astimezone(UTC)

        pool = module._get_pool()

        # Fetch with a generous DB-level cap to avoid unbounded scans.
        db_limit = max(500, max_episodes * 5, max_events * 5)

        episodes = await list_episodes(
            pool,
            start_from=start_at,
            start_to=end_at,
            limit=db_limit,
        )
        events = await list_point_events(
            pool,
            occurred_from=start_at,
            occurred_to=end_at,
            limit=db_limit,
        )

        from dataclasses import asdict

        episode_dicts = [asdict(ep) for ep in episodes]
        event_dicts = [asdict(ev) for ev in events]

        cfg = BundleConfig(
            max_episodes=max_episodes,
            max_events=max_events,
            rollup_threshold=rollup_threshold,
            max_total_chars=max_total_chars,
        )
        bundle_input = assemble_day_close_bundle(
            date_label=date_label,
            episodes=episode_dicts,
            events=event_dicts,
            timezone=timezone,
            config=cfg,
        )

        return {
            **bundle_input.bundle,
            "citations": bundle_input.citations,
        }
