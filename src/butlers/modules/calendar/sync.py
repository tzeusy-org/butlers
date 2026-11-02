"""Calendar sync implementation."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from typing import Any

from butlers.core.state import state_get as _state_get
from butlers.core.state import state_set as _state_set

from .constants import (
    DEFAULT_INTERNAL_PROJECTION_INTERVAL_MINUTES,
    DEFAULT_SYNC_INTERVAL_MINUTES,
    PROJECTION_STALENESS_MULTIPLIER,
    PROJECTION_STATUS_FAILED,
    PROJECTION_STATUS_FRESH,
    PROJECTION_STATUS_STALE,
    SOURCE_KIND_PROVIDER,
    SYNC_CURSOR_PROVIDER,
    SYNC_ERROR_KIND_TOKEN_EXPIRED,
    SYNC_STATE_KEY_PREFIX,
    logger,
)
from .errors import (
    CalendarAuthError,
    CalendarRequestError,
    CalendarSyncTokenExpiredError,
    _is_transient_connectivity_error,
    classify_sync_error_kind,
)
from .models import CalendarSyncState


class SyncMixin:
    """Existing Calendar sync behavior on the owning module instance."""

    def _sync_state_key(self, calendar_id: str) -> str:
        """Return the KV state store key for the given calendar's sync state."""
        return f"{SYNC_STATE_KEY_PREFIX}{calendar_id}"

    async def _load_sync_state(self, calendar_id: str) -> CalendarSyncState:
        """Load sync state from the KV store, returning a default if not found."""
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return CalendarSyncState()
        key = self._sync_state_key(calendar_id)
        raw = await _state_get(pool, key)
        if not isinstance(raw, dict):
            return CalendarSyncState()
        return CalendarSyncState(**raw)

    async def _save_sync_state(self, calendar_id: str, state: CalendarSyncState) -> None:
        """Persist sync state to the KV store."""
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            logger.warning("Cannot persist sync state: database pool not available")
            return
        key = self._sync_state_key(calendar_id)
        await _state_set(pool, key, state.model_dump())

    async def _projection_freshness_metadata(self) -> dict[str, Any]:
        if not await self._projection_tables_available():
            return {
                "available": False,
                "last_refreshed_at": None,
                "staleness_ms": None,
                "sources": [],
            }
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return {
                "available": False,
                "last_refreshed_at": None,
                "staleness_ms": None,
                "sources": [],
            }

        rows = await pool.fetch(
            """
            SELECT
                s.id,
                s.source_key,
                s.source_kind,
                s.lane,
                s.provider,
                s.calendar_id,
                s.butler_name,
                c.cursor_name,
                c.last_synced_at,
                c.last_success_at,
                c.last_error_at,
                c.last_error,
                c.full_sync_required
            FROM calendar_sources AS s
            LEFT JOIN LATERAL (
                SELECT cursor_name, last_synced_at, last_success_at, last_error_at, last_error,
                       full_sync_required, updated_at
                FROM calendar_sync_cursors
                WHERE source_id = s.id
                ORDER BY updated_at DESC
                LIMIT 1
            ) AS c ON TRUE
            ORDER BY s.lane, s.source_kind, s.source_key
            """
        )

        now = datetime.now(UTC)
        cfg = self._config
        interval_minutes = (
            cfg.sync.interval_minutes if cfg is not None else DEFAULT_SYNC_INTERVAL_MINUTES
        )
        stale_threshold_ms = max(
            interval_minutes * 60 * 1000 * PROJECTION_STALENESS_MULTIPLIER,
            300_000,
        )

        source_payloads: list[dict[str, Any]] = []
        freshest_at: datetime | None = None
        for row in rows:
            last_synced_at = self._coerce_datetime(row["last_synced_at"])
            last_success_at = self._coerce_datetime(row["last_success_at"])
            last_error_at = self._coerce_datetime(row["last_error_at"])
            last_error = row["last_error"]
            full_sync_required = (
                bool(row["full_sync_required"]) if row["full_sync_required"] is not None else False
            )

            staleness_ms = None
            if last_synced_at is not None:
                staleness_ms = max(int((now - last_synced_at).total_seconds() * 1000), 0)
                if freshest_at is None or last_synced_at > freshest_at:
                    freshest_at = last_synced_at

            if last_error and (
                last_success_at is None or (last_error_at and last_error_at >= last_success_at)
            ):
                sync_state = PROJECTION_STATUS_FAILED
            elif last_synced_at is None:
                sync_state = PROJECTION_STATUS_STALE
            elif full_sync_required:
                sync_state = PROJECTION_STATUS_STALE
            elif staleness_ms is not None and staleness_ms > stale_threshold_ms:
                sync_state = PROJECTION_STATUS_STALE
            else:
                sync_state = PROJECTION_STATUS_FRESH

            source_payloads.append(
                {
                    "source_key": row["source_key"],
                    "source_kind": row["source_kind"],
                    "lane": row["lane"],
                    "provider": row["provider"],
                    "calendar_id": row["calendar_id"],
                    "butler_name": row["butler_name"],
                    "cursor_name": row["cursor_name"],
                    "last_synced_at": last_synced_at.isoformat() if last_synced_at else None,
                    "last_success_at": last_success_at.isoformat() if last_success_at else None,
                    "last_error_at": last_error_at.isoformat() if last_error_at else None,
                    "last_error": last_error,
                    "full_sync_required": full_sync_required,
                    "sync_state": sync_state,
                    "staleness_ms": staleness_ms,
                    "error_kind": classify_sync_error_kind(last_error),
                }
            )

        overall_staleness_ms = None
        if freshest_at is not None:
            overall_staleness_ms = max(int((now - freshest_at).total_seconds() * 1000), 0)
        return {
            "available": True,
            "last_refreshed_at": freshest_at.isoformat() if freshest_at else None,
            "staleness_ms": overall_staleness_ms,
            "sources": source_payloads,
        }

    async def _sync_calendar(self, calendar_id: str, *, full: bool = False) -> bool:
        """Run one sync cycle for ``calendar_id``.

        Loads the saved sync token, calls the provider's sync endpoint, handles
        token expiration (falls back to full sync), and persists the new token.
        Errors are logged and swallowed so the poller stays alive.

        When ``full=True`` this is operator-driven **cursor recovery**: the
        stored incremental token is ignored and a full re-sync runs against
        ``sync_token=None`` over ``config.sync.full_sync_window_days``. A forced
        full re-sync is logged.

        Returns ``True`` when a full re-sync ran (either because ``full=True`` or
        because an expired token forced a 410 recovery), so callers can report
        whether a recovery was performed.
        """
        provider = self._provider
        if provider is None:
            return False

        config = self._require_config()
        sync_state = self._sync_states.get(calendar_id) or await self._load_sync_state(calendar_id)
        now = datetime.now(UTC)

        source_id: uuid.UUID | None = None
        source_key = f"provider:{provider.name}:{calendar_id}"

        # Honor the per-calendar source toggle (POST /api/calendar/sources): a
        # source disabled by the operator is skipped by the background sync loop
        # on subsequent syncs. An explicit operator-driven full re-sync
        # (``full=True``, the Recover action) still runs so a disabled source
        # can be recovered without first re-enabling it.
        if not full and not await self._source_sync_enabled(source_key):
            logger.info("Skipping disabled calendar source '%s'", source_key)
            return False

        # Resolve a human-readable display name (e.g. "Work" instead of the raw
        # Google Calendar ID like "ae06dba...@group.calendar.google.com").
        display_name = await provider.get_calendar_summary(calendar_id) or calendar_id
        # Butler-specific calendars (from credential store) are owned by this
        # butler; the shared project calendar (auto-discovered) is not.
        is_butler_cal = self._calendar_is_butler_specific
        try:
            source_id = await self._ensure_calendar_source(
                source_key=source_key,
                source_kind=SOURCE_KIND_PROVIDER,
                lane="user",
                provider=provider.name,
                calendar_id=calendar_id,
                butler_name=self._butler_name,
                display_name=display_name,
                writable=True,
                metadata={
                    "projection": "provider_sync",
                    "butler_specific": is_butler_cal,
                },
            )
        except Exception as exc:
            logger.debug("Failed to ensure provider calendar source '%s': %s", source_key, exc)

        # Forced full re-sync (cursor recovery): ignore the stored incremental
        # token so the provider returns a full window instead of a delta.
        if full:
            logger.info("Forced full re-sync (cursor recovery) for calendar '%s'", calendar_id)
        effective_sync_token = None if full else sync_state.sync_token
        cursor_checkpoint: dict[str, Any] = {}
        provider_projection_applied = False
        if source_id is not None:
            try:
                cursor_row = await self._load_projection_cursor(
                    source_id=source_id,
                    cursor_name=SYNC_CURSOR_PROVIDER,
                )
            except Exception as exc:
                logger.debug("Failed loading projection cursor for '%s': %s", source_key, exc)
                cursor_row = None
            if cursor_row is not None:
                # A forced full recovery deliberately discards the stored token.
                if not full:
                    cursor_token = cursor_row.get("sync_token")
                    if isinstance(cursor_token, str):
                        effective_sync_token = cursor_token
                cursor_checkpoint = self._normalize_json_object(cursor_row.get("checkpoint"))

        performed_full_resync = full
        error_message: str | None = None

        try:
            updated_events, cancelled_ids, next_token = await provider.sync_incremental(
                calendar_id=calendar_id,
                sync_token=effective_sync_token,
                full_sync_window_days=config.sync.full_sync_window_days,
            )
        except CalendarSyncTokenExpiredError:
            logger.warning(
                "Sync token expired (410 Gone) for calendar '%s'; recovering via full re-sync",
                calendar_id,
            )
            performed_full_resync = True
            # Record the token-expiry up-front so the source's ``error_kind`` is
            # observable as ``token_expired`` even if the recovery below also
            # fails. The success path overwrites this with a healthy cursor once
            # the full re-sync lands.
            token_expired_error = "sync token expired (410 Gone); recovering via full re-sync"
            await self._upsert_projection_cursor(
                source_id=source_id,
                cursor_name=SYNC_CURSOR_PROVIDER,
                sync_token=None,
                checkpoint={
                    **cursor_checkpoint,
                    "provider": provider.name,
                    "calendar_id": calendar_id,
                    "error_kind": SYNC_ERROR_KIND_TOKEN_EXPIRED,
                    "recovery": True,
                },
                full_sync_required=True,
                last_synced_at=now,
                last_success_at=self._coerce_datetime(cursor_checkpoint.get("last_success_at")),
                last_error_at=now,
                last_error=token_expired_error,
            )
            try:
                updated_events, cancelled_ids, next_token = await provider.sync_incremental(
                    calendar_id=calendar_id,
                    sync_token=None,
                    full_sync_window_days=config.sync.full_sync_window_days,
                )
            except CalendarRequestError as exc:
                if exc.status_code == 404:
                    logger.warning(
                        "Calendar '%s' not found (404); deregistering from sync", calendar_id
                    )
                    self._all_provider_calendar_ids = [
                        cid for cid in self._all_provider_calendar_ids if cid != calendar_id
                    ]
                    return
                raise
            except CalendarAuthError as exc:
                logger.error(
                    "Full re-sync failed for calendar '%s': %s",
                    calendar_id,
                    exc,
                    exc_info=True,
                )
                error_message = str(exc)[:200]
                sync_state.last_sync_error = error_message
                self._sync_states[calendar_id] = sync_state
                await self._save_sync_state(calendar_id, sync_state)
                await self._upsert_projection_cursor(
                    source_id=source_id,
                    cursor_name=SYNC_CURSOR_PROVIDER,
                    sync_token=effective_sync_token,
                    checkpoint={
                        **cursor_checkpoint,
                        "provider": provider.name,
                        "calendar_id": calendar_id,
                        "error": error_message,
                    },
                    full_sync_required=True,
                    last_synced_at=now,
                    last_success_at=self._coerce_datetime(cursor_checkpoint.get("last_success_at")),
                    last_error_at=now,
                    last_error=error_message,
                )
                await self._record_projection_action(
                    idempotency_key=f"calendar-sync:{source_key}:error:{int(now.timestamp())}",
                    action_type="projection_sync_provider",
                    action_status="failed",
                    source_id=source_id,
                    origin_ref=None,
                    action_payload={
                        "calendar_id": calendar_id,
                        "provider": provider.name,
                        "sync_token": effective_sync_token,
                    },
                    action_result={"status": "failed"},
                    error=error_message,
                )
                await self._project_internal_sources()
                return performed_full_resync
        except CalendarRequestError as exc:
            if exc.status_code == 404:
                logger.warning(
                    "Calendar '%s' not found (404); deregistering from sync", calendar_id
                )
                self._all_provider_calendar_ids = [
                    cid for cid in self._all_provider_calendar_ids if cid != calendar_id
                ]
                return performed_full_resync
            raise
        except CalendarAuthError as exc:
            logger.error(
                "Incremental sync failed for calendar '%s': %s",
                calendar_id,
                exc,
                exc_info=True,
            )
            error_message = str(exc)[:200]
            sync_state.last_sync_error = error_message
            self._sync_states[calendar_id] = sync_state
            await self._save_sync_state(calendar_id, sync_state)
            await self._upsert_projection_cursor(
                source_id=source_id,
                cursor_name=SYNC_CURSOR_PROVIDER,
                sync_token=effective_sync_token,
                checkpoint={
                    **cursor_checkpoint,
                    "provider": provider.name,
                    "calendar_id": calendar_id,
                    "error": error_message,
                },
                full_sync_required=False,
                last_synced_at=now,
                last_success_at=self._coerce_datetime(cursor_checkpoint.get("last_success_at")),
                last_error_at=now,
                last_error=error_message,
            )
            await self._record_projection_action(
                idempotency_key=f"calendar-sync:{source_key}:error:{int(now.timestamp())}",
                action_type="projection_sync_provider",
                action_status="failed",
                source_id=source_id,
                origin_ref=None,
                action_payload={
                    "calendar_id": calendar_id,
                    "provider": provider.name,
                    "sync_token": effective_sync_token,
                },
                action_result={"status": "failed"},
                error=error_message,
            )
            await self._project_internal_sources()
            return performed_full_resync

        if source_id is not None:
            try:
                await self._project_provider_changes(
                    source_id=source_id,
                    provider_name=provider.name,
                    calendar_id=calendar_id,
                    updated_events=updated_events,
                    cancelled_ids=cancelled_ids,
                )
            except Exception as exc:
                logger.error(
                    "Provider projection write failed for calendar '%s': %s",
                    calendar_id,
                    exc,
                    exc_info=True,
                )
                error_message = str(exc)[:200]
                await self._upsert_projection_cursor(
                    source_id=source_id,
                    cursor_name=SYNC_CURSOR_PROVIDER,
                    sync_token=effective_sync_token,
                    checkpoint={
                        "provider": provider.name,
                        "calendar_id": calendar_id,
                        "updated_events": len(updated_events),
                        "cancelled_events": len(cancelled_ids),
                    },
                    full_sync_required=False,
                    last_synced_at=now,
                    last_success_at=None,
                    last_error_at=now,
                    last_error=error_message,
                )
                await self._record_projection_action(
                    idempotency_key=f"calendar-sync:{source_key}:projection-error:{int(now.timestamp())}",
                    action_type="projection_sync_provider",
                    action_status="failed",
                    source_id=source_id,
                    origin_ref=None,
                    action_payload={
                        "calendar_id": calendar_id,
                        "provider": provider.name,
                        "updated_events": len(updated_events),
                        "cancelled_events": len(cancelled_ids),
                    },
                    action_result={"status": "failed"},
                    error=error_message,
                )
            else:
                provider_projection_applied = True
                checkpoint = {
                    "provider": provider.name,
                    "calendar_id": calendar_id,
                    "updated_events": len(updated_events),
                    "cancelled_events": len(cancelled_ids),
                    "full_resync": performed_full_resync,
                }
                await self._upsert_projection_cursor(
                    source_id=source_id,
                    cursor_name=SYNC_CURSOR_PROVIDER,
                    sync_token=next_token,
                    checkpoint=checkpoint,
                    full_sync_required=False,
                    last_synced_at=now,
                    last_success_at=now,
                    last_error_at=None,
                    last_error=None,
                )
                await self._record_projection_action(
                    idempotency_key=f"calendar-sync:{source_key}:{next_token}",
                    action_type="projection_sync_provider",
                    action_status="applied",
                    source_id=source_id,
                    origin_ref=None,
                    action_payload={
                        "calendar_id": calendar_id,
                        "provider": provider.name,
                        "sync_token_before": effective_sync_token,
                    },
                    action_result={
                        "next_sync_token": next_token,
                        "updated_events": len(updated_events),
                        "cancelled_events": len(cancelled_ids),
                        "full_resync": performed_full_resync,
                    },
                )

        pending_count = len(updated_events) + len(cancelled_ids)
        now_iso = now.isoformat()
        new_state = CalendarSyncState(
            sync_token=next_token,
            last_sync_at=now_iso,
            last_sync_error=error_message,
            last_batch_change_count=pending_count,
        )
        self._sync_states[calendar_id] = new_state
        await self._save_sync_state(calendar_id, new_state)
        internal_projection_material = await self._project_internal_sources(emit_event=False)
        if provider_projection_applied and pending_count:
            await self._publish_calendar_fleet_event(
                kind="provider_projection",
                data={
                    "updated_events": len(updated_events),
                    "cancelled_events": len(cancelled_ids),
                },
            )
        elif internal_projection_material:
            await self._publish_calendar_fleet_event(kind="internal_projection")

        logger.info(
            "Calendar sync completed (calendar_id=%s, updated=%d, cancelled=%d)",
            calendar_id,
            len(updated_events),
            len(cancelled_ids),
        )
        return performed_full_resync

    async def _run_force_sync(
        self,
        *,
        calendar_id: str | None,
        full: bool,
    ) -> dict[str, Any]:
        """Perform the historical inline ``calendar_force_sync`` work.

        This stays separate from command acknowledgement so direct MCP callers
        retain their synchronous contract while dashboard-triggered commands can
        be drained by the durable module queue.
        """
        provider = self._require_provider()

        if calendar_id is not None:
            resolved = self._resolve_calendar_id(calendar_id)
            recovery = await self._sync_calendar(resolved, full=full)
            try:
                await self._push_internal_events_to_provider()
            except Exception as exc:
                logger.error("Push to provider failed: %s", exc, exc_info=True)
            sync_state = self._sync_states.get(resolved, CalendarSyncState())
            return {
                "status": "sync_completed",
                "provider": provider.name,
                "calendar_id": resolved,
                "full": full,
                "recovery": recovery,
                "last_sync_at": sync_state.last_sync_at,
                "last_batch_change_count": sync_state.last_batch_change_count,
                "last_sync_error": sync_state.last_sync_error,
                "error_kind": classify_sync_error_kind(sync_state.last_sync_error),
                "projection_freshness": await self._projection_freshness_metadata(),
            }

        cal_ids = self._all_provider_calendar_ids
        if not cal_ids:
            resolved = self._resolve_calendar_id(None)
            cal_ids = [resolved]

        total_updated = 0
        errors: list[str] = []
        recovered_calendars: list[str] = []
        for cid in cal_ids:
            try:
                if await self._sync_calendar(cid, full=full):
                    recovered_calendars.append(cid)
            except Exception as exc:
                errors.append(f"{cid}: {exc}")
            state = self._sync_states.get(cid, CalendarSyncState())
            total_updated += state.last_batch_change_count or 0
            if state.last_sync_error:
                errors.append(f"{cid}: {state.last_sync_error}")

        try:
            await self._push_internal_events_to_provider()
        except Exception as exc:
            logger.error("Push to provider failed: %s", exc, exc_info=True)
            errors.append(f"push: {exc}")

        return {
            "status": "sync_completed",
            "provider": provider.name,
            "calendars_synced": len(cal_ids),
            "total_changes": total_updated,
            "full": full,
            "recovery": bool(recovered_calendars),
            "recovered_calendars": recovered_calendars,
            "errors": errors or None,
            "projection_freshness": await self._projection_freshness_metadata(),
        }

    async def _run_sync_poller(self) -> None:
        """Background task: poll for calendar changes at the configured interval.

        The poller also listens for a ``_force_sync_event`` to trigger
        immediate syncs (used by the ``calendar_force_sync`` MCP tool).
        """
        config = self._require_config()
        interval_seconds = config.sync.interval_minutes * 60

        logger.debug("Calendar sync poller loop started (interval=%ds)", interval_seconds)
        while True:
            try:
                # Sync all registered provider calendars (pull-all).
                cal_ids = self._all_provider_calendar_ids
                if not cal_ids:
                    if self._resolved_calendar_id is None:
                        raise RuntimeError("Calendar ID not resolved; call on_startup first")
                    cal_ids = [self._resolved_calendar_id]
                for cal_id in cal_ids:
                    try:
                        await self._sync_calendar(cal_id)
                    except Exception as inner_exc:
                        logger.error(
                            "Calendar sync failed for '%s': %s", cal_id, inner_exc, exc_info=True
                        )
            except Exception as exc:
                logger.error("Calendar sync poller error: %s", exc, exc_info=True)

            # Wait for the configured interval OR for an immediate-sync request.
            try:
                await asyncio.wait_for(
                    self._force_sync_event.wait(),
                    timeout=interval_seconds,
                )
                # Force sync was requested; reset the event and loop immediately.
                self._force_sync_event.clear()
                logger.debug("Calendar sync poller: immediate sync triggered via force_sync")
            except TimeoutError:
                # Normal timer expiry; loop and sync again.
                pass

    async def _run_internal_projection_poller(self) -> None:
        """Background task: project internal sources immediately on startup, then periodically.

        This ensures calendar_event_instances is populated for scheduler tasks
        and reminders even when no external provider sync has ever completed.
        The interval is controlled by DEFAULT_INTERNAL_PROJECTION_INTERVAL_MINUTES.
        """
        interval_seconds = DEFAULT_INTERNAL_PROJECTION_INTERVAL_MINUTES * 60
        logger.debug("Calendar internal projection poller started (interval=%ds)", interval_seconds)
        while True:
            try:
                await self._project_internal_sources()
            except Exception as exc:
                logger.error("Calendar internal projection poller error: %s", exc, exc_info=True)
            # Push butler events to the Butlers Google Calendar after projection.
            try:
                await self._push_internal_events_to_provider()
            except Exception as exc:
                if _is_transient_connectivity_error(exc):
                    logger.warning(
                        "Push to provider after projection deferred by transient connectivity: %s",
                        exc,
                    )
                else:
                    logger.error("Push to provider after projection failed: %s", exc, exc_info=True)
            # Sleep for the configured interval before the next run.
            try:
                await asyncio.sleep(interval_seconds)
            except asyncio.CancelledError:
                break
