"""Calendar internal projection implementation."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.fleet_events import publish_fleet_event

from .constants import (
    BUTLER_GENERATED_PRIVATE_KEY,
    BUTLER_NAME_PRIVATE_KEY,
    DEFAULT_SCHEDULED_TASK_DURATION_MINUTES,
    NATIVE_REMINDER_PROVIDER_EVENT_ID_KEY,
    RECURRENCE_PROJECTION_WINDOW_DAYS,
    SOURCE_KIND_INTERNAL_REMINDERS,
    SOURCE_KIND_INTERNAL_SCHEDULER,
    SYNC_CURSOR_PROJECTION,
    logger,
)
from .errors import CalendarRequestError
from .google_mapping import _normalize_optional_text
from .models import (
    CalendarEventCreate,
    CalendarEventUpdate,
    EventStatus,
    EventVisibility,
    _normalize_recurrence_rule,
)
from .time_helpers import _cron_next_occurrence, _cron_occurrences_in_window


class InternalProjectionMixin:
    """Existing Calendar internal projection behavior on the owning module instance."""

    async def _push_internal_events_to_provider(self) -> None:
        """Push butler-generated events (scheduled tasks, reminders) to the Butlers Google Calendar.

        Creates new Google Calendar events for internal source items that don't
        yet have a ``calendar_event_id``, and updates existing ones if the title
        or time has changed. Cancelled/disabled items are deleted from Google.
        """
        provider = self._provider
        if provider is None or self._resolved_calendar_id is None:
            return
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return

        cal_id = self._resolved_calendar_id
        pushed = 0
        updated = 0
        deleted = 0

        # --- Push scheduled_tasks ---
        # Only push user-facing (prompt) tasks. Job tasks are internal
        # automation (e.g. eligibility_sweep) and shouldn't appear on the
        # user's Google Calendar.
        if await self._table_exists("scheduled_tasks"):
            # Clean up any job tasks that were previously pushed to Google
            stale_job_rows = await pool.fetch(
                """
                SELECT id, calendar_event_id
                FROM scheduled_tasks
                WHERE dispatch_mode = 'job' AND calendar_event_id IS NOT NULL
                """
            )
            for stale in stale_job_rows:
                try:
                    await provider.delete_event(
                        calendar_id=cal_id, event_id=str(stale["calendar_event_id"])
                    )
                except Exception as exc:
                    logger.warning(
                        "Failed to delete stale Google event for job task %s: %s "
                        "(will retry next sync)",
                        stale["id"],
                        exc,
                    )
                    continue
                await pool.execute(
                    "UPDATE scheduled_tasks SET calendar_event_id = NULL WHERE id = $1",
                    stale["id"],
                )
                deleted += 1

            rows = await pool.fetch(
                """
                SELECT id, name, cron, timezone, start_at, end_at, until_at,
                       display_title, description, location,
                       calendar_event_id, enabled, updated_at
                FROM scheduled_tasks
                WHERE dispatch_mode != 'job'
                """
            )
            for row in rows:
                record = dict(row)
                task_id = record["id"]
                enabled = record.get("enabled", True)
                google_event_id = record.get("calendar_event_id")
                title = str(record.get("display_title") or record.get("name") or "Scheduled task")
                timezone = str(record.get("timezone") or "UTC")
                cron_expr = record.get("cron")
                task_description = _normalize_optional_text(record.get("description"))
                task_location = _normalize_optional_text(record.get("location"))
                # Prefer the author-supplied description; fall back to a cron note
                # so recurring tasks still show their schedule on Google.
                event_description = task_description or (
                    f"Cron: {cron_expr}" if cron_expr else None
                )
                start_at = self._coerce_datetime(record.get("start_at"))
                end_at = self._coerce_datetime(record.get("end_at"))

                # Determine event times
                if cron_expr and start_at is None:
                    occ_start = _cron_next_occurrence(str(cron_expr), now=datetime.now(UTC))
                    occ_end = occ_start + timedelta(minutes=DEFAULT_SCHEDULED_TASK_DURATION_MINUTES)
                elif start_at is not None:
                    occ_start = start_at
                    occ_end = end_at or (
                        start_at + timedelta(minutes=DEFAULT_SCHEDULED_TASK_DURATION_MINUTES)
                    )
                else:
                    continue

                if not enabled and google_event_id:
                    # Delete from Google
                    try:
                        await provider.delete_event(
                            calendar_id=cal_id, event_id=str(google_event_id)
                        )
                    except Exception as exc:
                        logger.debug("Failed to delete Google event for task %s: %s", task_id, exc)
                    await pool.execute(
                        "UPDATE scheduled_tasks SET calendar_event_id = NULL WHERE id = $1",
                        task_id,
                    )
                    deleted += 1
                elif not enabled:
                    continue
                elif google_event_id is None:
                    # Create in Google
                    try:
                        event = await provider.create_event(
                            calendar_id=cal_id,
                            payload=CalendarEventCreate(
                                title=title,
                                start_at=occ_start,
                                end_at=occ_end,
                                timezone=timezone,
                                description=event_description,
                                location=task_location,
                                private_metadata={
                                    "butler_generated": "true",
                                    "butler_name": self._butler_name,
                                    "task_id": str(task_id),
                                },
                            ),
                        )
                        await pool.execute(
                            "UPDATE scheduled_tasks SET calendar_event_id = $1 WHERE id = $2",
                            event.event_id,
                            task_id,
                        )
                        pushed += 1
                    except Exception as exc:
                        logger.warning(
                            "Failed to push task %s to Google Calendar: %s", task_id, exc
                        )
                else:
                    # Update existing Google event
                    try:
                        await provider.update_event(
                            calendar_id=cal_id,
                            event_id=str(google_event_id),
                            patch=CalendarEventUpdate(
                                title=title,
                                start_at=occ_start,
                                end_at=occ_end,
                                timezone=timezone,
                                description=event_description,
                                location=task_location,
                            ),
                        )
                        updated += 1
                    except Exception as exc:
                        logger.debug("Failed to update Google event for task %s: %s", task_id, exc)

        # --- Push calendar-native reminders ---
        # The local calendar_events row is authoritative.  Its provider event id
        # lives in metadata so repeated refreshes update one durable mirror.
        if await self._table_exists("calendar_events"):
            rows = await pool.fetch(
                """
                SELECT e.id, e.title, e.description, e.body, e.location,
                       e.timezone, e.starts_at, e.ends_at, e.recurrence_rule,
                       e.status, e.metadata
                FROM calendar_events e
                JOIN calendar_sources s ON s.id = e.source_id
                WHERE s.source_kind = $1
                  AND e.source_butler = $2
                """,
                SOURCE_KIND_INTERNAL_REMINDERS,
                self._resolve_effective_butler_name(),
            )
            for row in rows:
                record = dict(row)
                reminder_id = record["id"]
                metadata = self._normalize_json_object(record.get("metadata"))
                provider_event_id = metadata.get(NATIVE_REMINDER_PROVIDER_EVENT_ID_KEY)
                title = str(record.get("title") or "Reminder")
                timezone = str(record.get("timezone") or "UTC")
                starts_at = self._coerce_datetime(record.get("starts_at"))
                ends_at = self._coerce_datetime(record.get("ends_at"))
                description = _normalize_optional_text(record.get("description"))
                body = _normalize_optional_text(record.get("body"))
                location = _normalize_optional_text(record.get("location"))
                recurrence_rule = _normalize_recurrence_rule(record.get("recurrence_rule"))
                is_active = record.get("status") != "cancelled"

                if starts_at is None or ends_at is None:
                    continue

                if not is_active and provider_event_id:
                    try:
                        await provider.delete_event(
                            calendar_id=cal_id,
                            event_id=str(provider_event_id),
                        )
                    except CalendarRequestError as exc:
                        if exc.status_code != 404:
                            logger.warning(
                                "Failed to delete provider event for native reminder %s: %s "
                                "(will retry next sync)",
                                reminder_id,
                                exc,
                            )
                            continue
                    except Exception as exc:
                        logger.warning(
                            "Failed to delete provider event for native reminder %s: %s "
                            "(will retry next sync)",
                            reminder_id,
                            exc,
                        )
                        continue
                    await pool.execute(
                        """
                        UPDATE calendar_events
                        SET metadata = metadata - $1, updated_at = now()
                        WHERE id = $2
                        """,
                        NATIVE_REMINDER_PROVIDER_EVENT_ID_KEY,
                        reminder_id,
                    )
                    deleted += 1
                    continue
                if not is_active:
                    continue

                create_payload = CalendarEventCreate(
                    title=title,
                    start_at=starts_at,
                    end_at=ends_at,
                    timezone=timezone,
                    description=description,
                    body=body,
                    location=location,
                    recurrence_rule=recurrence_rule,
                    private_metadata={
                        BUTLER_GENERATED_PRIVATE_KEY: "true",
                        BUTLER_NAME_PRIVATE_KEY: self._resolve_effective_butler_name(),
                        "reminder_id": str(reminder_id),
                    },
                )
                if provider_event_id is None:
                    try:
                        event = await provider.create_event(
                            calendar_id=cal_id,
                            payload=create_payload,
                        )
                        await pool.execute(
                            """
                            UPDATE calendar_events
                            SET metadata = jsonb_set(
                                    metadata,
                                    ARRAY[$1]::text[],
                                    to_jsonb($2::text),
                                    true
                                ),
                                updated_at = now()
                            WHERE id = $3
                            """,
                            NATIVE_REMINDER_PROVIDER_EVENT_ID_KEY,
                            event.event_id,
                            reminder_id,
                        )
                        pushed += 1
                    except Exception as exc:
                        logger.warning(
                            "Failed to push native reminder %s to provider calendar: %s",
                            reminder_id,
                            exc,
                        )
                    continue

                try:
                    await provider.update_event(
                        calendar_id=cal_id,
                        event_id=str(provider_event_id),
                        patch=CalendarEventUpdate(
                            title=title,
                            start_at=starts_at,
                            end_at=ends_at,
                            timezone=timezone,
                            description=description,
                            body=body,
                            location=location,
                            recurrence_rule=recurrence_rule,
                            private_metadata=create_payload.private_metadata,
                        ),
                    )
                    updated += 1
                except CalendarRequestError as exc:
                    if exc.status_code != 404:
                        logger.warning(
                            "Failed to update provider event for native reminder %s: %s",
                            reminder_id,
                            exc,
                        )
                        continue
                    try:
                        event = await provider.create_event(
                            calendar_id=cal_id,
                            payload=create_payload,
                        )
                        await pool.execute(
                            """
                            UPDATE calendar_events
                            SET metadata = jsonb_set(
                                    metadata,
                                    ARRAY[$1]::text[],
                                    to_jsonb($2::text),
                                    true
                                ),
                                updated_at = now()
                            WHERE id = $3
                            """,
                            NATIVE_REMINDER_PROVIDER_EVENT_ID_KEY,
                            event.event_id,
                            reminder_id,
                        )
                        pushed += 1
                    except Exception as create_exc:
                        logger.warning(
                            "Failed to recreate missing provider event for native reminder %s: %s",
                            reminder_id,
                            create_exc,
                        )
                except Exception as exc:
                    logger.warning(
                        "Failed to update provider event for native reminder %s: %s",
                        reminder_id,
                        exc,
                    )

        if pushed or updated or deleted:
            logger.info(
                "Push to Google Calendar complete (butler=%s, created=%d, updated=%d, deleted=%d)",
                self._butler_name,
                pushed,
                updated,
                deleted,
            )

        # --- Orphan sweep ---
        # Clean up butler-generated events on Google that no longer have a
        # matching local record (e.g. job-task events whose calendar_event_id
        # was cleared before the Google delete succeeded).
        try:
            known_event_ids: set[str] = set()
            if await self._table_exists("scheduled_tasks"):
                task_rows = await pool.fetch(
                    "SELECT calendar_event_id FROM scheduled_tasks "
                    "WHERE calendar_event_id IS NOT NULL"
                )
                known_event_ids.update(str(r["calendar_event_id"]) for r in task_rows)
            if await self._table_exists("calendar_events"):
                reminder_rows = await pool.fetch(
                    """
                    SELECT e.metadata->>$1 AS provider_event_id
                    FROM calendar_events e
                    JOIN calendar_sources s ON s.id = e.source_id
                    WHERE s.source_kind = $2
                      AND e.source_butler = $3
                      AND e.metadata ? $1
                    """,
                    NATIVE_REMINDER_PROVIDER_EVENT_ID_KEY,
                    SOURCE_KIND_INTERNAL_REMINDERS,
                    self._resolve_effective_butler_name(),
                )
                known_event_ids.update(
                    str(row["provider_event_id"])
                    for row in reminder_rows
                    if row["provider_event_id"] is not None
                )
            # Include events created via calendar_create_event (workspace
            # mutations).  These are tracked in the action log, not in
            # scheduled_tasks/reminders.
            if await self._table_exists("calendar_action_log"):
                action_rows = await pool.fetch(
                    "SELECT origin_ref FROM calendar_action_log "
                    "WHERE action_type IN ('workspace_user_create', 'workspace_user_update') "
                    "  AND action_status = 'applied' "
                    "  AND origin_ref IS NOT NULL"
                )
                known_event_ids.update(str(r["origin_ref"]) for r in action_rows)

            google_events = await provider.list_events(
                calendar_id=cal_id,
                start_at=datetime.now(UTC) - timedelta(days=90),
                end_at=datetime.now(UTC) + timedelta(days=365),
                limit=250,
            )
            orphan_count = 0
            for ev in google_events:
                if not ev.butler_generated:
                    continue
                if ev.event_id in known_event_ids:
                    continue
                # Butler-generated event with no local record — orphan
                try:
                    await provider.delete_event(calendar_id=cal_id, event_id=ev.event_id)
                    orphan_count += 1
                except Exception as exc:
                    logger.warning(
                        "Failed to delete orphaned Google event %s: %s",
                        ev.event_id,
                        exc,
                    )
            if orphan_count:
                logger.info(
                    "Orphan sweep: deleted %d stale butler-generated events from Google",
                    orphan_count,
                )
        except Exception as exc:
            logger.debug("Orphan sweep skipped: %s", exc)

    async def _scheduler_projection_fingerprint(
        self,
        *,
        source_id: uuid.UUID,
    ) -> tuple[str, ...] | None:
        """Return a stable signature of the scheduler's user-visible projection.

        Scheduler sweeps intentionally update cursors and timestamps even when
        their visible event/instance rows are unchanged.  Those bookkeeping
        writes must not manufacture a calendar freshness event, so materiality
        compares only the fields the workspace can render.
        """
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None

        try:
            rows = await pool.fetch(
                """
                SELECT
                    e.origin_ref,
                    e.title,
                    e.description,
                    e.body,
                    e.location,
                    e.timezone AS event_timezone,
                    e.starts_at AS event_starts_at,
                    e.ends_at AS event_ends_at,
                    e.all_day,
                    e.status AS event_status,
                    e.visibility,
                    e.recurrence_rule,
                    e.metadata AS event_metadata,
                    e.source_butler,
                    e.source_session_id,
                    i.origin_instance_ref,
                    i.timezone AS instance_timezone,
                    i.starts_at AS instance_starts_at,
                    i.ends_at AS instance_ends_at,
                    i.status AS instance_status,
                    i.is_exception,
                    i.metadata AS instance_metadata
                FROM calendar_events AS e
                LEFT JOIN calendar_event_instances AS i ON i.event_id = e.id
                WHERE e.source_id = $1
                ORDER BY e.origin_ref, i.origin_instance_ref NULLS FIRST
                """,
                source_id,
            )
        except Exception as exc:
            # Publishing without a materiality check would turn a transient
            # read failure into a false freshness signal.  Keep the durable
            # projection sweep running, but conservatively suppress its event.
            logger.debug("Failed to fingerprint scheduler projection: %s", exc, exc_info=True)
            return None

        return tuple(
            json.dumps(
                self._jsonify_for_storage(dict(row)),
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            for row in rows
        )

    async def _project_scheduler_source(self) -> bool:
        """Project scheduled tasks and report whether the workspace changed."""
        if not await self._projection_tables_available():
            return False
        if not await self._table_exists("scheduled_tasks"):
            return False
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return False

        source_id = await self._ensure_calendar_source(
            source_key=f"internal_scheduler:{self._butler_name}",
            source_kind=SOURCE_KIND_INTERNAL_SCHEDULER,
            lane="butler",
            provider="internal",
            butler_name=self._butler_name,
            display_name=f"{self._butler_name} schedules",
            writable=True,
            metadata={"projection": "scheduler"},
        )
        if source_id is None:
            return False

        before_projection = await self._scheduler_projection_fingerprint(source_id=source_id)

        rows = await pool.fetch(
            """
            SELECT id, name, cron, dispatch_mode, prompt, job_name, job_args,
                   timezone, start_at, end_at, until_at, display_title,
                   description, location,
                   calendar_event_id, enabled, updated_at
            FROM scheduled_tasks
            WHERE dispatch_mode != 'job'
            """
        )

        seen_origin_refs: list[str] = []
        now = datetime.now(UTC)
        window_end = now + timedelta(days=RECURRENCE_PROJECTION_WINDOW_DAYS)
        for row in rows:
            record = dict(row)
            start_at = self._coerce_datetime(record.get("start_at"))
            end_at = self._coerce_datetime(record.get("end_at"))
            cron_expr = record.get("cron")

            # Rows without a cron expression and no explicit start_at cannot
            # produce occurrences — skip them.
            if start_at is None and not cron_expr:
                continue

            origin_ref = str(record["id"])
            seen_origin_refs.append(origin_ref)
            timezone = str(record.get("timezone") or "UTC")
            title = str(record.get("display_title") or record.get("name") or "Scheduled task")
            status = (
                EventStatus.confirmed.value
                if record.get("enabled", True)
                else EventStatus.cancelled.value
            )
            updated_at = self._coerce_datetime(record.get("updated_at"))
            metadata = {
                "source_type": SOURCE_KIND_INTERNAL_SCHEDULER,
                "name": record.get("name"),
                "cron": cron_expr,
                "dispatch_mode": record.get("dispatch_mode"),
                "prompt": record.get("prompt"),
                "job_name": record.get("job_name"),
                "job_args": self._normalize_json_object(record.get("job_args")),
                "until_at": record.get("until_at"),
                "calendar_event_id": record.get("calendar_event_id"),
            }

            if cron_expr and start_at is None:
                # Recurring cron-driven task: expand into all occurrences within
                # the rolling window.
                occurrences = _cron_occurrences_in_window(
                    str(cron_expr),
                    window_start=now,
                    window_end=window_end,
                )
                if not occurrences:
                    # Cron fires outside the window (e.g. very sparse schedule);
                    # fall back to next single occurrence to keep the event visible.
                    occ_start = _cron_next_occurrence(str(cron_expr), now=now)
                    occ_end = occ_start + timedelta(minutes=DEFAULT_SCHEDULED_TASK_DURATION_MINUTES)
                    occurrences = [(occ_start, occ_end)]
                # The canonical event represents the series; starts_at = first occurrence.
                event_start_at, event_end_at = occurrences[0]
            else:
                # Task with explicit start_at (one-time or fixed occurrence) or no cron.
                if start_at is None:
                    # Should not reach here due to earlier guard, but be defensive.
                    continue
                if end_at is None:
                    end_at = start_at + timedelta(minutes=DEFAULT_SCHEDULED_TASK_DURATION_MINUTES)
                event_start_at, event_end_at = start_at, end_at
                occurrences = [(start_at, end_at)]

            event_id = await self._upsert_projection_event(
                source_id=source_id,
                origin_ref=origin_ref,
                title=title,
                description=_normalize_optional_text(record.get("description")),
                location=_normalize_optional_text(record.get("location")),
                timezone=timezone,
                starts_at=event_start_at,
                ends_at=event_end_at,
                all_day=False,
                status=status,
                visibility=EventVisibility.default.value,
                recurrence_rule=str(cron_expr) if cron_expr else None,
                etag=None,
                origin_updated_at=updated_at,
                metadata=metadata,
                source_butler=self._butler_name,
            )

            # Prune stale instances outside the new window before re-projecting.
            await self._prune_recurring_instances_outside_window(
                event_id=event_id,
                window_start=now,
                window_end=window_end,
            )

            for occ_start, occ_end in occurrences:
                await self._upsert_projection_instance(
                    event_id=event_id,
                    source_id=source_id,
                    origin_instance_ref=f"{origin_ref}:{occ_start.isoformat()}",
                    timezone=timezone,
                    starts_at=occ_start,
                    ends_at=occ_end,
                    status=status,
                    is_exception=False,
                    origin_updated_at=updated_at,
                    metadata={"source_type": SOURCE_KIND_INTERNAL_SCHEDULER},
                )

        await self._mark_projection_source_stale_events_cancelled(
            source_id=source_id,
            seen_origin_refs=seen_origin_refs,
        )
        await self._upsert_projection_cursor(
            source_id=source_id,
            cursor_name=SYNC_CURSOR_PROJECTION,
            sync_token=None,
            checkpoint={
                "projected_rows": len(seen_origin_refs),
                "source": SOURCE_KIND_INTERNAL_SCHEDULER,
            },
            full_sync_required=False,
            last_synced_at=now,
            last_success_at=now,
            last_error_at=None,
            last_error=None,
        )
        after_projection = await self._scheduler_projection_fingerprint(source_id=source_id)
        return (
            before_projection is not None
            and after_projection is not None
            and before_projection != after_projection
        )

    async def _publish_calendar_fleet_event(
        self,
        *,
        kind: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Best-effort freshness signal after a durable calendar projection."""
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return
        await publish_fleet_event(pool, "calendar", {"kind": kind, **(data or {})})

    async def _project_native_reminder_instances(self) -> None:
        """Extend recurring native reminders through the rolling projection window."""
        if not await self._projection_tables_available():
            return
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return

        rows = await pool.fetch(
            """
            SELECT e.id, e.source_id, e.recurrence_rule, e.starts_at, e.ends_at, e.timezone
            FROM calendar_events e
            JOIN calendar_sources s ON s.id = e.source_id
            WHERE s.source_kind = $1
              AND e.source_butler = $2
              AND e.status = 'confirmed'
              AND e.recurrence_rule IS NOT NULL
            """,
            SOURCE_KIND_INTERNAL_REMINDERS,
            self._resolve_effective_butler_name(),
        )
        window_start = datetime.now(UTC)
        window_end = window_start + timedelta(days=RECURRENCE_PROJECTION_WINDOW_DAYS)
        for row in rows:
            await self._prune_recurring_instances_outside_window(
                event_id=row["id"],
                window_start=window_start,
                window_end=window_end,
                retain_overdue_unnotified=True,
            )
            await self._materialize_native_reminder_instances(
                row["id"],
                row["source_id"],
                recurrence_rule=row["recurrence_rule"],
                starts_at=row["starts_at"],
                ends_at=row["ends_at"],
                timezone=row["timezone"],
                window_start=window_start,
            )

    async def _project_internal_sources(self, *, emit_event: bool = True) -> bool:
        """Refresh scheduled-task projections and recurring native reminder instances."""
        try:
            material_projection = await self._project_scheduler_source()
            await self._project_native_reminder_instances()
        except Exception as exc:
            logger.error("Internal calendar projection refresh failed: %s", exc, exc_info=True)
            return False
        else:
            if material_projection and emit_event:
                await self._publish_calendar_fleet_event(kind="internal_projection")
            return bool(material_projection)
