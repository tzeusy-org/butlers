"""Private scheduler continuation; state is bookkeeping, never coverage proof."""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from butlers.chronicler.bundle_assembler import assemble_day_close_bundle
from butlers.chronicler.day_close_cache import day_close_cache_key
from butlers.chronicler.day_close_refresh import DayCloseRefreshService
from butlers.chronicler.storage import list_episodes, list_point_events


class DayCloseContinuation:
    """One daemon's fixed post-tick caller, with durable revision discovery."""

    def __init__(self, service: DayCloseRefreshService, timezone: str):
        self.service = service
        self.pool = service.pool
        self.timezone = ZoneInfo(timezone).key
        self.day_close_ran = False
        self.committed = False
        self._tick_lock = asyncio.Lock()

    def begin_tick(self) -> None:
        self.day_close_ran = False
        self.committed = False

    def after_job(self, name: str, result: Any) -> None:
        # Called only after the actual fixed registry handler returned. No
        # model invocation occurs here, nor any authority supplied in job_args.
        if (
            isinstance(result, dict)
            and not result.get("error")
            and not result.get("skipped")
            and result.get("status") not in {"error", "failed", "skipped"}
        ):
            self.committed = True

    async def _state(self, key: str) -> dict:
        value = await self.pool.fetchval("SELECT value FROM state WHERE key=$1", key)
        return value if isinstance(value, dict) else {}

    async def _save(self, key: str, value: dict) -> None:
        await self.pool.execute(
            "INSERT INTO state(key,value) VALUES($1,$2::jsonb) "
            "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, "
            "version=state.version+1, updated_at=now()",
            key,
            value,
        )

    async def _snapshot(self, target: date) -> Any:
        zone = ZoneInfo(self.timezone)
        start = datetime.combine(target, datetime.min.time(), zone).astimezone(UTC)
        end = datetime.combine(target + timedelta(days=1), datetime.min.time(), zone).astimezone(
            UTC
        )
        episodes = await list_episodes(self.pool, start_from=start, start_to=end, limit=500)
        events = await list_point_events(self.pool, occurred_from=start, occurred_to=end, limit=500)
        # Use the accepted privacy projection, bounds and stable field stripping.
        # No raw material, private identity or computed_at is persisted.
        bundle = assemble_day_close_bundle(
            date_label=target.isoformat(),
            timezone=self.timezone,
            episodes=[asdict(row) for row in episodes],
            events=[asdict(row) for row in events],
        )
        return bundle

    async def revision(self, target: date) -> str:
        bundle = await self._snapshot(target)
        return hashlib.sha256(
            json.dumps(json.loads(bundle.encoded()), sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()

    async def _has_refresh_evidence(self, target: date) -> bool:
        bundle = (await self._snapshot(target)).bundle
        if any(bundle.get(key) for key in ("episodes", "events", "sensitive_envelopes")):
            return True
        # A zero rollup/checkpoint alone cannot license an automatic quiet day.
        # Previously covered material may legitimately have been removed.
        witness = await self.pool.fetchrow(
            "SELECT covered_at FROM covered_local_days WHERE local_date=$1 "
            "AND timezone=$2 AND origin <> 'legacy_unverified'",
            target,
            self.timezone,
        )
        return witness is not None

    async def _discover(self) -> list[date]:
        key = "day_close:cursor:" + self.timezone
        row = await self.pool.fetchrow("SELECT value FROM state WHERE key=$1", key)
        cursor = date.min
        if row is not None:
            state = row["value"]
            if not isinstance(state, dict) or not isinstance(state.get("date"), str):
                raise ValueError("Invalid day-close discovery cursor")
            try:
                # JSON retains the ISO string; asyncpg's date codec needs a
                # native date. A malformed checkpoint cannot advance work.
                cursor = date.fromisoformat(state["date"])
            except ValueError:
                raise ValueError("Invalid day-close discovery cursor") from None
        today = datetime.now(UTC).astimezone(ZoneInfo(self.timezone)).date()
        # Durable local tuples survive missed hooks, tombstones, window moves
        # and process restarts. There is no new age cutoff or completeness flag.
        rows = await self.pool.fetch(
            """
            WITH days AS (
              SELECT local_date FROM covered_local_days WHERE timezone=$1
              UNION SELECT local_date FROM daily_rollups WHERE timezone=$1
              UNION SELECT date_label::date FROM tier2_cache
                WHERE date_label ~ '^\\d{4}-\\d{2}-\\d{2}$'
                  AND cache_key LIKE '%:tz:' || $1
              UNION SELECT (canonical_start_at AT TIME ZONE $1)::date
                FROM v_episodes_corrected WHERE tombstone_at IS NULL
                  AND canonical_privacy IN ('normal','sensitive')
              UNION SELECT (occurred_at AT TIME ZONE $1)::date
                FROM v_point_events_corrected WHERE tombstone_at IS NULL
                  AND canonical_privacy IN ('normal','sensitive')
              UNION SELECT (value->>'date')::date FROM state
                WHERE key LIKE 'day_close:pending:%' AND value->>'timezone'=$1
            )
            SELECT local_date FROM days WHERE local_date < $2
            ORDER BY (local_date > $3::date) DESC, local_date LIMIT 32
            """,
            self.timezone,
            today,
            cursor,
        )
        return [row["local_date"] for row in rows]

    async def resume_once(self, *, bound_s: float = 100.0) -> dict:
        if self.day_close_ran or self.service.closed:
            return {"status": "skipped"}
        deadline = asyncio.get_running_loop().time() + bound_s

        async def resume():
            async with self._tick_lock:
                unproven = False
                for target in await self._discover():
                    await self._save(
                        "day_close:cursor:" + self.timezone, {"date": target.isoformat()}
                    )
                    key = "day_close:pending:" + day_close_cache_key(target, self.timezone)
                    revision = await self.revision(target)
                    state = await self._state(key)
                    if state.get("processed") == revision:
                        continue
                    # Checkpoint is not coverage, source completeness or a grant.
                    pending = {
                        "date": target.isoformat(),
                        "timezone": self.timezone,
                        "revision": revision,
                        "processed": state.get("processed"),
                    }
                    await self._save(key, pending)
                    if state.get("retry_at") and datetime.fromisoformat(
                        state["retry_at"]
                    ) > datetime.now(UTC):
                        continue
                    if not await self._has_refresh_evidence(target):
                        unproven = True
                        pending["outcome"] = "day_close_material_unproven"
                        await self._save(key, pending)
                        continue
                    result = await self.service.refresh(
                        target,
                        self.timezone,
                        bound_s=max(0, deadline - asyncio.get_running_loop().time()),
                    )
                    pending["outcome"] = result.get("code", result.get("status"))
                    if result.get("code") == "day_close_rate_limited":
                        pending["retry_at"] = (
                            datetime.now(UTC)
                            + timedelta(seconds=result["details"]["retry_after_seconds"])
                        ).isoformat()
                    elif result.get("status") == "success" and not result.get("invalid"):
                        if await self.revision(target) == revision:
                            pending["processed"] = revision
                    await self._save(key, pending)
                    return result
                if unproven:
                    return {"status": "error", "code": "day_close_material_unproven"}
                return {"status": "unchanged"}

        return await self.service.run_owned(resume(), bound_s=bound_s)
