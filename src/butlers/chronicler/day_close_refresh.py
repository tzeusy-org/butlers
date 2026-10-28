"""Chronicler-only infrastructure controls."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from butlers.chronicler.day_close_cache import (
    day_close_cache_key,
    resolve_day_close_timezone,
)
from butlers.chronicler.day_close_writer import (
    DAY_CLOSE_TASK_NAME,
    build_day_close_prompt_hooks,
    write_day_close_cache,
)
from butlers.core.model_routing import coerce_complexity_tier
from butlers.core.tool_call_capture import (
    MANUAL_DAY_CLOSE_TRIGGER_PREFIX,
)

logger = logging.getLogger(__name__)

_REFRESH_RATE_LIMIT = timedelta(hours=24)
_REFRESH_OPERATION_TIMEOUT_SECONDS = 100.0


def _error(
    code: str,
    message: str,
    *,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {"status": "error", "code": code, "message": message}
    if details:
        response["details"] = details
    return response


def _manual_run_at(target: date, timezone: ZoneInfo) -> datetime:
    """Anchor the scheduler-owned day window to exactly *target*."""
    return datetime.combine(
        target + timedelta(days=1),
        datetime.min.time().replace(hour=12),
        tzinfo=timezone,
    ).astimezone(UTC)


async def _authoritative_witness(pool: Any, target: date, timezone: str) -> Any:
    return await pool.fetchrow(
        """
        SELECT covered_at
        FROM covered_local_days
        WHERE local_date = $1
          AND timezone = $2
          AND origin <> 'legacy_unverified'
        """,
        target,
        timezone,
    )


async def _run_manual_refresh(
    ctx: Any,
    *,
    target: date,
    timezone_name: str,
    timezone_info: ZoneInfo,
) -> dict[str, Any]:
    pool = ctx.pool
    daemon = ctx.daemon
    cache_key = day_close_cache_key(target, timezone_name)
    now = datetime.now(UTC)
    existing_row = await pool.fetchrow(
        """
        SELECT cache_built_at
        FROM tier2_cache
        WHERE cache_key = $1
          AND superseded_at IS NULL
        """,
        cache_key,
    )
    witness_row = await _authoritative_witness(pool, target, timezone_name)

    saved = await pool.fetchval(
        "SELECT value FROM state WHERE key = $1", "day_close:success:" + cache_key
    )
    successful_at = (
        datetime.fromisoformat(saved["at"])
        if isinstance(saved, dict) and isinstance(saved.get("at"), str)
        else None
    )
    if witness_row is not None:
        successful_at = max(filter(None, (successful_at, witness_row["covered_at"])))
        if existing_row is not None:
            successful_at = max(successful_at, existing_row["cache_built_at"])
    if successful_at is not None and now - successful_at < _REFRESH_RATE_LIMIT:
        retry_after = max(1, int((_REFRESH_RATE_LIMIT - (now - successful_at)).total_seconds()))
        return _error(
            "day_close_rate_limited",
            f"A day-close refresh for {cache_key!r} was performed recently.",
            details={"retry_after_seconds": retry_after},
        )

    task_row = await pool.fetchrow(
        "SELECT prompt, complexity, max_token_budget FROM scheduled_tasks "
        "WHERE name = $1 AND enabled = true",
        DAY_CLOSE_TASK_NAME,
    )
    if task_row is None or not task_row["prompt"]:
        return _error(
            "task_not_found",
            f"Scheduled task {DAY_CLOSE_TASK_NAME!r} was not found or has no prompt.",
        )

    run_at = _manual_run_at(target, timezone_info)
    prompt_hook = build_day_close_prompt_hooks(timezone=timezone_name)[DAY_CLOSE_TASK_NAME]
    prepared_prompt = prompt_hook(
        task_name=DAY_CLOSE_TASK_NAME,
        prompt=task_row["prompt"],
        run_at=run_at,
        timezone=timezone_name,
    )
    prepared_prompt += (
        "\n\nThis is a manual historical regeneration. Return the narration for cache "
        "admission only. Do not call notify, remind, schedule_create, or schedule_update."
    )

    try:
        result = await daemon._dispatch_scheduled_task(
            trigger_source=f"{MANUAL_DAY_CLOSE_TRIGGER_PREFIX}{target.isoformat()}",
            prompt=prepared_prompt,
            complexity=coerce_complexity_tier(task_row.get("complexity"), strict=False),
            **(
                {"max_token_budget": task_row["max_token_budget"]}
                if task_row.get("max_token_budget") is not None
                else {}
            ),
        )
        write_outcome = await ctx.writer(
            pool,
            task_name=DAY_CLOSE_TASK_NAME,
            result=result,
            run_at=run_at,
            tz=timezone_name,
            target_date=target,
        )
    except Exception:
        logger.exception(
            "Manual Chronicler day-close refresh failed for date=%s timezone=%s",
            target,
            timezone_name,
        )
        return _error("dispatch_failed", "Day-close regeneration failed during execution.")

    if write_outcome is None:
        return _error(
            "cache_write_failed",
            "Day-close dispatch completed without an admissible cache outcome.",
        )

    new_row = (
        await pool.fetchrow(
            "SELECT cache_built_at FROM tier2_cache WHERE cache_key = $1 AND superseded_at IS NULL",
            cache_key,
        )
        if not write_outcome.quiet
        else None
    )
    if not write_outcome.quiet and new_row is None:
        return _error(
            "cache_write_failed",
            "Day-close regeneration completed without a cache row.",
        )

    # Historic authoritative rows are idempotent and need not advance their
    # timestamp. Only this canonical execution can report its own write success.
    if not write_outcome.coverage_recorded or (
        await _authoritative_witness(pool, target, timezone_name) is None
    ):
        return _error(
            "coverage_witness_write_failed",
            "Day-close regeneration completed without a durable coverage witness.",
        )

    await pool.execute(
        "INSERT INTO state(key,value) VALUES($1,$2::jsonb) "
        "ON CONFLICT(key) DO UPDATE SET value=EXCLUDED.value, "
        "version=state.version+1, updated_at=now()",
        "day_close:success:" + cache_key,
        {"at": datetime.now(UTC).isoformat()},
    )
    invalid_reason = write_outcome.invalid_reason
    if invalid_reason is not None:
        return {
            "status": "success",
            "cache_key": cache_key,
            "cache_built_at": new_row["cache_built_at"],
            "quiet": False,
            "invalid": True,
            "invalid_reason": invalid_reason,
        }

    if write_outcome.quiet:
        return {
            "status": "success",
            "cache_key": cache_key,
            "quiet": True,
        }

    return {
        "status": "success",
        "cache_key": cache_key,
        "cache_built_at": new_row["cache_built_at"],
        "quiet": False,
        "invalid": False,
        "invalid_reason": None,
    }


class DayCloseRefreshService:
    """One owning daemon's canonical refresh and exact tuple serialization.

    The task includes all reads, dispatch, writer transaction and pool release.
    A deadline never detaches its ownership or fabricates a successful result.
    """

    def __init__(self, pool: Any, daemon: Any):
        self.pool = pool
        self.daemon = daemon
        self.writer = write_day_close_cache
        self.locks: dict[tuple[date, str], asyncio.Lock] = {}
        self.pending: set[asyncio.Task] = set()
        self.closed = False
        self.releases: set[asyncio.Future] = set()

    async def refresh(
        self,
        target: date,
        timezone_name: str,
        *,
        bound_s: float = _REFRESH_OPERATION_TIMEOUT_SECONDS,
        writer: Any = None,
    ) -> dict[str, Any]:
        if self.closed or self.incomplete:
            return _error("refresh_incomplete", "Day-close owner is closing or settling.")
        name, zone = resolve_day_close_timezone(timezone_name)
        if target >= datetime.now(UTC).astimezone(zone).date():
            return _error("day_close_not_settled", "Day-close target is not settled.")

        async def run():
            async with self.locks.setdefault((target, name), asyncio.Lock()):
                # Per-operation context avoids a concurrent test seam changing
                # the writer of an already owned operation.
                from types import SimpleNamespace

                return await _run_manual_refresh(
                    SimpleNamespace(
                        pool=self.pool, daemon=self.daemon, writer=writer or self.writer
                    ),
                    target=target,
                    timezone_name=name,
                    timezone_info=zone,
                )

        return await self.run_owned(run(), bound_s=bound_s)

    def _observe_releases(self) -> None:
        # asyncpg shields holder.reset during release. A cancelled calling task
        # can finish before this physical lease is returned to its queue.
        import asyncpg

        if isinstance(self.pool, asyncpg.Pool):
            for holder in self.pool._holders:
                lease = holder._in_use
                if lease is not None and not lease.done():
                    self.releases.add(lease)
                    lease.add_done_callback(self.releases.discard)

    @property
    def incomplete(self) -> bool:
        return bool(self.releases or any(task.cancelling() for task in self.pending))

    @property
    def has_pending(self) -> bool:
        self._observe_releases()
        return bool(self.pending or self.releases)

    async def run_owned(self, operation: Any, *, bound_s: float) -> Any:
        if self.closed or self.incomplete:
            operation.close()
            return _error("refresh_incomplete", "Day-close owner is closing or settling.")
        task = asyncio.create_task(operation, name="chronicler-canonical-refresh")
        self.pending.add(task)
        task.add_done_callback(self._settled)
        try:
            done, _ = await asyncio.wait({task}, timeout=max(0, bound_s))
            if done:
                return task.result()
            self._cancel_once(task)
            return _error(
                "dispatch_timeout", "Day-close regeneration exceeded its execution deadline."
            )
        except asyncio.CancelledError:
            self._cancel_once(task)
            raise

    def _cancel_once(self, task: asyncio.Task) -> None:
        self._observe_releases()
        if not task.done() and not task.cancelling():
            task.cancel()

    def _settled(self, task: asyncio.Task) -> None:
        if task.cancelled() or task.cancelling():
            self._observe_releases()
        self.pending.discard(task)
        if not task.cancelled():
            task.exception()

    async def close(self, bound_s: float) -> bool:
        self.closed = True
        for task in tuple(self.pending):
            self._cancel_once(task)
        owned = set(self.pending) | set(self.releases)
        if owned:
            await asyncio.wait(owned, timeout=max(0, bound_s))
        return not self.has_pending

    async def settle(self) -> None:
        while self.has_pending:
            await asyncio.shield(
                asyncio.gather(*tuple(self.pending), *tuple(self.releases), return_exceptions=True)
            )


def held_refresh_service(daemon: Any, pool: Any) -> DayCloseRefreshService:
    """Internal composition only; never accepts an RPC-selected collaborator."""
    service = vars(daemon).get("_chronicler_refresh_service")
    if service is None:
        service = DayCloseRefreshService(pool, daemon)
        daemon._chronicler_refresh_service = service
    if service.pool is not pool:
        raise RuntimeError("Chronicler refresh pool identity changed")
    return service
