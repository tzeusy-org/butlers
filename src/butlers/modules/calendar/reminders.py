"""Calendar reminders implementation."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Coroutine
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal

from .constants import (
    NATIVE_REMINDER_PROVIDER_EVENT_ID_KEY,
    RECURRENCE_PROJECTION_WINDOW_DAYS,
    SOURCE_KIND_INTERNAL_REMINDERS,
    logger,
)
from .errors import CalendarRequestError
from .google_mapping import _normalize_optional_text
from .models import EventStatus, _ensure_valid_timezone, _normalize_recurrence_rule
from .time_helpers import (
    _format_ical_utc,
    _recurrence_lines_bound_until,
    _rrule_occurrences_in_window,
)

if TYPE_CHECKING:
    from . import CalendarModule


class RemindersMixin:
    """Existing Calendar reminders behavior on the owning module instance."""

    async def tick(
        self,
        source_butler: str,
        notify_fn: Callable[[dict[str, Any]], Coroutine[Any, Any, None]] | None = None,
    ) -> int:
        """Evaluate due reminder events and dispatch notifications.

        For **recurring** reminders (``recurrence_rule IS NOT NULL``), each
        occurrence in ``calendar_event_instances`` is checked independently:
        an instance fires when its ``starts_at <= now`` and its own
        ``metadata->>'notified_at'`` is absent.  After a successful dispatch
        the instance-level metadata is updated to prevent duplicate delivery of
        the same occurrence.  A cancelled or dismissed instance (``status =
        'cancelled'``) is always skipped.

        For **one-time** reminders (``recurrence_rule IS NULL``), the series
        event row is checked directly — the same ``last_notified_at``
        comparison used in the original PR #1079 design.

        Parameters
        ----------
        source_butler:
            Butler name used to scope reminder evaluation.  Only events owned
            by this butler (via ``calendar_sources.butler_name``) are
            considered.
        notify_fn:
            Optional async callable ``async def notify_fn(envelope: dict) -> None``.
            Receives a canonical ``notify.v1`` envelope for each due reminder.
            When ``None``, due reminders are logged but not dispatched.

        Returns
        -------
        int
            Number of reminders that were both dispatched via *notify_fn* and
            had their dedup metadata persisted successfully.  A reminder whose
            notification was delivered but whose metadata write subsequently
            failed is **not** counted, and it will re-fire on the next tick.
        """
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return 0
        if not await self._projection_tables_available():
            return 0

        now = datetime.now(UTC)
        dispatched = 0

        # ------------------------------------------------------------------
        # Pass 1: recurring reminders — per-instance dedup via instances table
        # ------------------------------------------------------------------
        try:
            recurring_rows = await pool.fetch(
                """
                SELECT
                    ce.id           AS event_id,
                    ce.title        AS title,
                    i.id            AS instance_id,
                    i.starts_at     AS instance_starts_at
                FROM calendar_events ce
                JOIN calendar_sources cs ON cs.id = ce.source_id
                JOIN calendar_event_instances i ON i.event_id = ce.id
                WHERE cs.source_kind = $1
                  AND cs.butler_name = $2
                  AND ce.recurrence_rule IS NOT NULL
                  AND ce.status = $4
                  AND i.status = $4
                  AND i.starts_at <= $3
                  AND (i.metadata->>'notified_at') IS NULL
                ORDER BY i.starts_at
                """,
                SOURCE_KIND_INTERNAL_REMINDERS,
                source_butler,
                now,
                EventStatus.confirmed.value,
            )
        except Exception as exc:
            logger.error(
                "CalendarModule.tick: failed to query due recurring reminders for %s: %s",
                source_butler,
                exc,
                exc_info=True,
            )
            recurring_rows = []

        for row in recurring_rows:
            event_id = row["event_id"]
            instance_id = row["instance_id"]
            title = row["title"] or "Reminder"
            instance_starts_at = row["instance_starts_at"]

            envelope = {
                "schema_version": "notify.v1",
                "origin_butler": source_butler,
                "delivery": {
                    "intent": "send",
                    "message": f"Reminder: {title}",
                },
                "reminder_event_id": str(event_id),
                "reminder_instance_id": str(instance_id),
                "due_at": (
                    instance_starts_at.isoformat() if instance_starts_at is not None else None
                ),
            }

            notified = False
            if notify_fn is not None:
                try:
                    await notify_fn(envelope)
                    notified = True
                except Exception as exc:
                    logger.error(
                        "CalendarModule.tick: notify_fn failed for recurring reminder "
                        "instance %s (event %s): %s",
                        instance_id,
                        event_id,
                        exc,
                        exc_info=True,
                    )
            else:
                logger.info(
                    "CalendarModule.tick: due recurring reminder instance %s "
                    "('%s') for %s — no notify_fn configured",
                    instance_id,
                    title,
                    source_butler,
                )

            if notified:
                try:
                    await pool.execute(
                        "UPDATE calendar_event_instances "
                        "SET metadata = COALESCE(metadata, '{}'::jsonb) || $1 "
                        "WHERE id = $2",
                        self._encode_jsonb({"notified_at": now.isoformat()}),
                        instance_id,
                    )
                    dispatched += 1
                except Exception as exc:
                    logger.error(
                        "CalendarModule.tick: failed to record notified_at "
                        "for recurring reminder instance %s: %s",
                        instance_id,
                        exc,
                        exc_info=True,
                    )

        # ------------------------------------------------------------------
        # Pass 2: one-time reminders — series-level dedup via event metadata
        # ------------------------------------------------------------------
        try:
            onetime_rows = await pool.fetch(
                """
                SELECT ce.id AS event_id, ce.title AS title, ce.starts_at AS starts_at
                FROM calendar_events ce
                JOIN calendar_sources cs ON cs.id = ce.source_id
                WHERE cs.source_kind = $1
                  AND cs.butler_name = $2
                  AND ce.recurrence_rule IS NULL
                  AND ce.status = $4
                  AND ce.starts_at <= $3
                  AND (
                      ce.metadata->>'last_notified_at' IS NULL
                      OR (ce.metadata->>'last_notified_at')::timestamptz < ce.starts_at
                  )
                ORDER BY ce.starts_at
                """,
                SOURCE_KIND_INTERNAL_REMINDERS,
                source_butler,
                now,
                EventStatus.confirmed.value,
            )
        except Exception as exc:
            logger.error(
                "CalendarModule.tick: failed to query due one-time reminders for %s: %s",
                source_butler,
                exc,
                exc_info=True,
            )
            onetime_rows = []

        for row in onetime_rows:
            event_id = row["event_id"]
            title = row["title"] or "Reminder"
            starts_at = row["starts_at"]

            envelope = {
                "schema_version": "notify.v1",
                "origin_butler": source_butler,
                "delivery": {
                    "intent": "send",
                    "message": f"Reminder: {title}",
                },
                "reminder_event_id": str(event_id),
                "due_at": starts_at.isoformat() if starts_at is not None else None,
            }

            notified = False
            if notify_fn is not None:
                try:
                    await notify_fn(envelope)
                    notified = True
                except Exception as exc:
                    logger.error(
                        "CalendarModule.tick: notify_fn failed for one-time reminder %s: %s",
                        event_id,
                        exc,
                        exc_info=True,
                    )
            else:
                logger.info(
                    "CalendarModule.tick: due one-time reminder %s ('%s') for %s "
                    "— no notify_fn configured",
                    event_id,
                    title,
                    source_butler,
                )

            if notified:
                try:
                    await pool.execute(
                        "UPDATE calendar_events "
                        "SET metadata = COALESCE(metadata, '{}'::jsonb) || $1 "
                        "WHERE id = $2",
                        self._encode_jsonb({"last_notified_at": now.isoformat()}),
                        event_id,
                    )
                    dispatched += 1
                except Exception as exc:
                    logger.error(
                        "CalendarModule.tick: failed to record last_notified_at "
                        "for one-time reminder %s: %s",
                        event_id,
                        exc,
                        exc_info=True,
                    )

        return dispatched

    async def _insert_reminder_to_calendar_events(
        self,
        *,
        title: str,
        body: str | None,
        description: str | None = None,
        location: str | None = None,
        starts_at: datetime,
        ends_at: datetime,
        timezone: str,
        recurrence_rule: str | None,
        entity_ids: list[uuid.UUID],
    ) -> tuple[uuid.UUID, dict[str, Any]]:
        """Insert a new reminder row into calendar_events and link entity_ids.

        Returns (event_id, row_dict).
        """
        from butlers.core.tool_call_capture import get_current_runtime_session_id

        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Database pool is not available")

        source_id = await self._resolve_action_source_id(
            source_kind=SOURCE_KIND_INTERNAL_REMINDERS,
            lane="butler",
        )
        if source_id is None:
            raise RuntimeError("Could not resolve internal_reminders calendar source")

        session_id_str = get_current_runtime_session_id()
        origin_ref = str(uuid.uuid4())
        resolved_source_butler = self._resolve_effective_butler_name()

        row = await pool.fetchrow(
            """
            INSERT INTO calendar_events (
                source_id, origin_ref, title, description, body, location, timezone,
                starts_at, ends_at, all_day, status, visibility,
                recurrence_rule, source_butler, source_session_id, metadata
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, FALSE, 'confirmed', 'default',
                $10, $11, $12, '{"native": true}'::jsonb
            )
            RETURNING id, title, description, body, location, starts_at, ends_at, status,
                      recurrence_rule, source_butler, source_session_id
            """,
            source_id,
            origin_ref,
            title,
            description,
            body,
            location,
            timezone,
            starts_at,
            ends_at,
            recurrence_rule,
            resolved_source_butler,
            session_id_str,
        )
        if row is None:
            raise RuntimeError("Failed to insert reminder into calendar_events")

        event_id: uuid.UUID = row["id"]

        if recurrence_rule:
            await self._materialize_native_reminder_instances(
                event_id,
                source_id,
                recurrence_rule=recurrence_rule,
                starts_at=starts_at,
                ends_at=ends_at,
                timezone=timezone,
                window_start=starts_at,
            )

        # Insert entity associations if provided.
        if entity_ids:
            await pool.executemany(
                """
                INSERT INTO calendar_event_entities (event_id, entity_id)
                VALUES ($1, $2)
                ON CONFLICT DO NOTHING
                """,
                [(event_id, eid) for eid in entity_ids],
            )

        return event_id, dict(row)

    async def _materialize_native_reminder_instances(
        self,
        event_id: uuid.UUID,
        source_id: uuid.UUID,
        *,
        recurrence_rule: str,
        starts_at: datetime,
        ends_at: datetime,
        timezone: str,
        window_start: datetime,
        executor: Any | None = None,
    ) -> None:
        """Materialize a rolling RRULE window without overwriting dismissed instances."""
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Database pool is not available")
        db = executor or pool

        duration_minutes = max(int((ends_at - starts_at).total_seconds() // 60), 1)
        window_end = window_start + timedelta(days=RECURRENCE_PROJECTION_WINDOW_DAYS)
        occurrences = _rrule_occurrences_in_window(
            recurrence_rule,
            starts_at,
            window_start,
            window_end,
            duration_minutes=duration_minutes,
        )
        if not occurrences:
            return

        rows = [
            (
                event_id,
                source_id,
                f"native:{_format_ical_utc(occurrence_start)}",
                timezone,
                occurrence_start,
                occurrence_end,
            )
            for occurrence_start, occurrence_end in occurrences
        ]
        await db.executemany(
            """
            INSERT INTO calendar_event_instances (
                event_id, source_id, origin_instance_ref, timezone,
                starts_at, ends_at, status, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, 'confirmed', '{"native": true}'::jsonb)
            ON CONFLICT (event_id, origin_instance_ref) DO NOTHING
            """,
            rows,
        )

    async def _update_native_reminder_event(
        self,
        *,
        reminder_id: uuid.UUID,
        title: str | None,
        body: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
        timezone: str | None,
        until_at: datetime | None,
        recurrence_rule: str | None,
        enabled: bool | None,
    ) -> dict[str, Any]:
        """Update a calendar-native reminder owned by this butler."""
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Database pool is not available")

        async with pool.acquire() as conn:
            async with conn.transaction():
                row = await conn.fetchrow(
                    """
                    SELECT e.*
                    FROM calendar_events e
                    JOIN calendar_sources s ON s.id = e.source_id
                    WHERE e.id = $1
                      AND s.source_kind = $2
                      AND e.source_butler = $3
                    FOR UPDATE OF e
                    """,
                    reminder_id,
                    SOURCE_KIND_INTERNAL_REMINDERS,
                    self._resolve_effective_butler_name(),
                )
                if row is None:
                    raise ValueError(f"Native reminder {reminder_id} not found")
                existing = dict(row)

                effective_start = start_at or existing["starts_at"]
                effective_end = end_at or existing["ends_at"]
                if effective_start.tzinfo is None:
                    raise ValueError("start_at must be timezone-aware")
                if effective_end.tzinfo is None:
                    raise ValueError("end_at must be timezone-aware")
                if effective_end <= effective_start:
                    raise ValueError("end_at must be after start_at")

                updates: list[str] = []
                params: list[Any] = [reminder_id]
                index = 2

                def add(column: str, value: Any) -> None:
                    nonlocal index
                    updates.append(f"{column} = ${index}")
                    params.append(value)
                    index += 1

                if title is not None:
                    normalized_title = title.strip()
                    if not normalized_title:
                        raise ValueError("title must be a non-empty string")
                    add("title", normalized_title)
                if body is not None:
                    add("body", _normalize_optional_text(body))
                if start_at is not None:
                    add("starts_at", start_at)
                if end_at is not None:
                    add("ends_at", end_at)
                if timezone is not None:
                    normalized_timezone = timezone.strip()
                    _ensure_valid_timezone(normalized_timezone)
                    add("timezone", normalized_timezone)

                recurrence_changed = recurrence_rule is not None or until_at is not None
                effective_rule = (
                    _normalize_recurrence_rule(recurrence_rule)
                    if recurrence_rule is not None
                    else existing.get("recurrence_rule")
                )
                if until_at is not None:
                    if effective_rule is None:
                        raise ValueError(
                            "until_at requires recurrence_rule for butler_reminder events"
                        )
                    effective_rule = _recurrence_lines_bound_until([effective_rule], until_at)[0]
                if recurrence_changed:
                    add("recurrence_rule", effective_rule)
                if enabled is not None:
                    add("status", "confirmed" if enabled else "cancelled")

                if not updates:
                    return existing

                updated = await conn.fetchrow(
                    f"""
                    UPDATE calendar_events
                    SET {", ".join(updates)}, updated_at = now()
                    WHERE id = $1
                    RETURNING *
                    """,
                    *params,
                )
                if updated is None:
                    raise RuntimeError(f"Failed to update native reminder {reminder_id}")
                result = dict(updated)

                if start_at is not None or end_at is not None or recurrence_changed:
                    await conn.execute(
                        """
                        DELETE FROM calendar_event_instances
                        WHERE event_id = $1
                          AND starts_at > now()
                          AND status = 'confirmed'
                          AND metadata->>'notified_at' IS NULL
                        """,
                        reminder_id,
                    )
                    if result.get("recurrence_rule") and result.get("status") != "cancelled":
                        await self._materialize_native_reminder_instances(
                            reminder_id,
                            result["source_id"],
                            recurrence_rule=result["recurrence_rule"],
                            starts_at=result["starts_at"],
                            ends_at=result["ends_at"],
                            timezone=result["timezone"],
                            window_start=result["starts_at"],
                            executor=conn,
                        )

                return result

    async def _delete_native_reminder_event(
        self,
        reminder_id: uuid.UUID,
        *,
        scope: Literal["this", "following", "series"] = "series",
        instance_start_at: datetime | None = None,
    ) -> bool:
        """Delete a calendar-native reminder series or occurrence range."""
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Database pool is not available")
        if scope not in {"this", "following", "series"}:
            raise ValueError("scope must be one of: this | following | series")
        if scope != "series":
            if instance_start_at is None:
                raise ValueError("instance_start_at is required for scope 'this'/'following'")
            if instance_start_at.tzinfo is None:
                raise ValueError("instance_start_at must be timezone-aware")

        if scope == "series":
            await self._delete_native_reminder_provider_copy(reminder_id)

        async with pool.acquire() as conn:
            async with conn.transaction():
                if scope == "series":
                    row = await conn.fetchrow(
                        """
                        DELETE FROM calendar_events e
                        USING calendar_sources s
                        WHERE e.id = $1
                          AND e.source_id = s.id
                          AND s.source_kind = $2
                          AND e.source_butler = $3
                        RETURNING e.id
                        """,
                        reminder_id,
                        SOURCE_KIND_INTERNAL_REMINDERS,
                        self._resolve_effective_butler_name(),
                    )
                    return row is not None

                event = await conn.fetchrow(
                    """
                    SELECT e.recurrence_rule
                    FROM calendar_events e
                    JOIN calendar_sources s ON s.id = e.source_id
                    WHERE e.id = $1
                      AND s.source_kind = $2
                      AND e.source_butler = $3
                    FOR UPDATE OF e
                    """,
                    reminder_id,
                    SOURCE_KIND_INTERNAL_REMINDERS,
                    self._resolve_effective_butler_name(),
                )
                if event is None:
                    return False
                recurrence_rule = event["recurrence_rule"]
                if not recurrence_rule:
                    raise ValueError(
                        "Occurrence-scoped deletion requires a recurring native reminder"
                    )
                occurrence_start = instance_start_at.astimezone(UTC)
                occurrence_exists = await conn.fetchval(
                    """
                    SELECT EXISTS (
                        SELECT 1
                        FROM calendar_event_instances
                        WHERE event_id = $1
                          AND starts_at = $2
                    )
                    """,
                    reminder_id,
                    occurrence_start,
                )
                if not occurrence_exists:
                    return False

                if scope == "this":
                    row = await conn.fetchrow(
                        """
                        UPDATE calendar_event_instances
                        SET status = 'cancelled',
                            is_exception = true,
                            updated_at = now()
                        WHERE event_id = $1
                          AND starts_at = $2
                        RETURNING id
                        """,
                        reminder_id,
                        occurrence_start,
                    )
                    return row is not None

                bounded_rule = _recurrence_lines_bound_until(
                    [recurrence_rule],
                    occurrence_start - timedelta(seconds=1),
                )[0]
                await conn.execute(
                    """
                    UPDATE calendar_events
                    SET recurrence_rule = $2, updated_at = now()
                    WHERE id = $1
                    """,
                    reminder_id,
                    bounded_rule,
                )
                result = await conn.execute(
                    """
                    UPDATE calendar_event_instances
                    SET status = 'cancelled',
                        is_exception = true,
                        updated_at = now()
                    WHERE event_id = $1
                      AND starts_at >= $2
                    """,
                    reminder_id,
                    occurrence_start,
                )
                return result != "UPDATE 0"

    async def _delete_native_reminder_provider_copy(self, reminder_id: uuid.UUID) -> None:
        """Delete a native reminder's durable provider mirror before local series deletion."""
        provider = self._provider
        calendar_id = self._resolved_calendar_id
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if provider is None or calendar_id is None or pool is None:
            return

        row = await pool.fetchrow(
            """
            SELECT e.metadata
            FROM calendar_events e
            JOIN calendar_sources s ON s.id = e.source_id
            WHERE e.id = $1
              AND s.source_kind = $2
              AND e.source_butler = $3
            """,
            reminder_id,
            SOURCE_KIND_INTERNAL_REMINDERS,
            self._resolve_effective_butler_name(),
        )
        if row is None:
            return
        metadata = self._normalize_json_object(row["metadata"])
        provider_event_id = metadata.get(NATIVE_REMINDER_PROVIDER_EVENT_ID_KEY)
        if provider_event_id is None:
            return
        try:
            await provider.delete_event(
                calendar_id=calendar_id,
                event_id=str(provider_event_id),
            )
        except CalendarRequestError as exc:
            if exc.status_code != 404:
                raise

    async def _toggle_native_reminder_event(
        self,
        reminder_id: uuid.UUID,
        enabled: bool,
    ) -> dict[str, Any]:
        """Pause or resume a calendar-native reminder owned by this butler."""
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Database pool is not available")
        row = await pool.fetchrow(
            """
            UPDATE calendar_events e
            SET status = $2, updated_at = now()
            FROM calendar_sources s
            WHERE e.id = $1
              AND e.source_id = s.id
              AND s.source_kind = $3
              AND e.source_butler = $4
            RETURNING e.*
            """,
            reminder_id,
            "confirmed" if enabled else "cancelled",
            SOURCE_KIND_INTERNAL_REMINDERS,
            self._resolve_effective_butler_name(),
        )
        if row is None:
            raise ValueError(f"Native reminder {reminder_id} not found")
        return dict(row)

    async def _query_reminders(
        self,
        *,
        entity_id: uuid.UUID | None,
        due_before: datetime | None,
        include_dismissed: bool,
    ) -> list[dict[str, Any]]:
        """Query calendar_events for reminders owned by the calling butler."""
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return []

        if not await self._projection_tables_available():
            return []

        conditions: list[str] = [
            "e.source_butler = $1",
            "s.source_kind = $2",
        ]
        params: list[Any] = [self._butler_name, SOURCE_KIND_INTERNAL_REMINDERS]
        idx = 3

        if not include_dismissed:
            conditions.append("e.status != 'cancelled'")

        if due_before is not None:
            conditions.append(f"e.starts_at <= ${idx}")
            params.append(due_before)
            idx += 1

        join_clause = ""
        if entity_id is not None:
            join_clause = "JOIN calendar_event_entities ee ON ee.event_id = e.id"
            conditions.append(f"ee.entity_id = ${idx}")
            params.append(entity_id)
            idx += 1

        where_clause = " AND ".join(conditions)
        query = f"""
            SELECT
                e.id, e.title, e.body, e.starts_at, e.ends_at, e.timezone,
                e.status, e.recurrence_rule, e.source_butler, e.source_session_id,
                e.created_at, e.updated_at
            FROM calendar_events e
            JOIN calendar_sources s ON s.id = e.source_id
            {join_clause}
            WHERE {where_clause}
            ORDER BY e.starts_at ASC
        """
        rows = await pool.fetch(query, *params)

        # Collect entity associations for each event in one query.
        event_ids = [row["id"] for row in rows]
        entity_map: dict[uuid.UUID, list[str]] = {}
        if event_ids:
            entity_rows = await pool.fetch(
                """
                SELECT event_id, entity_id
                FROM calendar_event_entities
                WHERE event_id = ANY($1::uuid[])
                """,
                event_ids,
            )
            for er in entity_rows:
                eid_key = er["event_id"]
                entity_map.setdefault(eid_key, []).append(str(er["entity_id"]))

        result: list[dict[str, Any]] = []
        for row in rows:
            row_id: uuid.UUID = row["id"]
            result.append(
                {
                    "event_id": str(row_id),
                    "title": row["title"],
                    "body": row["body"],
                    "starts_at": row["starts_at"].isoformat() if row["starts_at"] else None,
                    "ends_at": row["ends_at"].isoformat() if row["ends_at"] else None,
                    "timezone": row["timezone"],
                    "status": row["status"],
                    "recurrence_rule": row["recurrence_rule"],
                    "source_butler": row["source_butler"],
                    "source_session_id": row["source_session_id"],
                    "entity_ids": entity_map.get(row_id, []),
                    "created_at": (row["created_at"].isoformat() if row["created_at"] else None),
                    "updated_at": (row["updated_at"].isoformat() if row["updated_at"] else None),
                }
            )
        return result

    async def _dismiss_reminder(self, event_id: uuid.UUID) -> dict[str, Any]:
        """Dismiss a reminder event.

        One-time reminders: set status='cancelled' on calendar_events.
        Recurring reminders: cancel the earliest confirmed instance in
        calendar_event_instances; the series row itself stays 'confirmed'.
        """
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Database pool is not available")

        # Fetch the event, verifying it belongs to this butler and is a reminder.
        row = await pool.fetchrow(
            """
            SELECT e.id, e.title, e.status, e.recurrence_rule,
                   e.source_butler, s.source_kind
            FROM calendar_events e
            JOIN calendar_sources s ON s.id = e.source_id
            WHERE e.id = $1
            """,
            event_id,
        )
        if row is None:
            raise ValueError(f"Reminder event {event_id} not found")
        if row["source_kind"] != SOURCE_KIND_INTERNAL_REMINDERS:
            raise ValueError(
                f"Event {event_id} is not a reminder (source_kind={row['source_kind']!r})"
            )
        if row["source_butler"] != self._butler_name:
            raise ValueError(
                f"Event {event_id} belongs to butler {row['source_butler']!r}, "
                f"not {self._butler_name!r}"
            )

        recurrence_rule = row["recurrence_rule"]
        current_status = row["status"]

        if not recurrence_rule:
            # One-time reminder: cancel the event directly.
            if current_status == "cancelled":
                return {
                    "status": "already_dismissed",
                    "event_id": str(event_id),
                    "recurrence": "one_time",
                }
            await pool.execute(
                """
                UPDATE calendar_events
                SET status = 'cancelled', updated_at = now()
                WHERE id = $1
                """,
                event_id,
            )
            return {
                "status": "dismissed",
                "event_id": str(event_id),
                "recurrence": "one_time",
            }

        # Recurring reminder: cancel the earliest confirmed instance.
        instance_row = await pool.fetchrow(
            """
            SELECT id, starts_at
            FROM calendar_event_instances
            WHERE event_id = $1
              AND status = 'confirmed'
            ORDER BY starts_at ASC
            LIMIT 1
            """,
            event_id,
        )
        if instance_row is None:
            return {
                "status": "no_active_instance",
                "event_id": str(event_id),
                "recurrence": "recurring",
                "message": (
                    "No upcoming instances found; series remains active for "
                    "future projection cycles."
                ),
            }
        await pool.execute(
            """
            UPDATE calendar_event_instances
            SET status = 'cancelled', updated_at = now()
            WHERE id = $1
            """,
            instance_row["id"],
        )
        return {
            "status": "instance_dismissed",
            "event_id": str(event_id),
            "recurrence": "recurring",
            "dismissed_instance_starts_at": instance_row["starts_at"].isoformat(),
        }


def register_reminder_tools(
    module: CalendarModule, mcp: Any, _tool: Callable[..., Any], butler_name: str
) -> None:

    # ------------------------------------------------------------------
    # Reminder tools (source_kind = "internal_reminders")
    # ------------------------------------------------------------------

    @_tool("core")
    async def reminder_create(
        title: str,
        due_at: datetime,
        body: str | None = None,
        ends_at: datetime | None = None,
        recurrence: str | None = None,
        entity_ids: list[str] | None = None,
        timezone: str | None = None,
    ) -> dict[str, Any]:
        """Create a reminder as a native calendar event.

        The reminder is stored in ``calendar_events`` with
        ``source_kind = 'internal_reminders'`` and attributed to the
        calling butler.  ``ends_at`` defaults to ``due_at + 15 minutes``
        when not provided.

        ``recurrence`` accepts ``"daily"``, ``"weekly"``, ``"monthly"``,
        or ``"yearly"``; omit for one-time reminders.

        ``entity_ids`` links the reminder to zero or more entities via
        the ``calendar_event_entities`` junction table.
        """
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("title must be a non-empty string")
        if due_at.tzinfo is None:
            raise ValueError("due_at must be timezone-aware")
        effective_ends_at = ends_at or (due_at + timedelta(minutes=15))
        if effective_ends_at.tzinfo is None:
            raise ValueError("ends_at must be timezone-aware when provided")
        if effective_ends_at <= due_at:
            raise ValueError("ends_at must be after due_at")
        effective_timezone = (timezone or module._require_config().timezone).strip()
        _ensure_valid_timezone(effective_timezone)

        recurrence_rule: str | None = None
        if recurrence is not None:
            normalized_rec = recurrence.strip().lower()
            if normalized_rec == "yearly":
                recurrence_rule = "RRULE:FREQ=YEARLY"
            elif normalized_rec == "monthly":
                recurrence_rule = "RRULE:FREQ=MONTHLY"
            elif normalized_rec == "weekly":
                recurrence_rule = "RRULE:FREQ=WEEKLY"
            elif normalized_rec == "daily":
                recurrence_rule = "RRULE:FREQ=DAILY"
            else:
                raise ValueError(
                    f"Unsupported recurrence value: {recurrence!r}. "
                    "Use 'daily', 'weekly', 'monthly', or 'yearly'."
                )

        normalized_entity_ids: list[uuid.UUID] = []
        for raw_eid in entity_ids or []:
            try:
                normalized_entity_ids.append(uuid.UUID(str(raw_eid)))
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"Invalid entity_id: {raw_eid!r}") from exc

        event_id, event_data = await module._insert_reminder_to_calendar_events(
            title=normalized_title,
            body=body,
            starts_at=due_at,
            ends_at=effective_ends_at,
            timezone=effective_timezone,
            recurrence_rule=recurrence_rule,
            entity_ids=normalized_entity_ids,
        )
        return {
            "status": "created",
            "event_id": str(event_id),
            "title": normalized_title,
            "starts_at": due_at.isoformat(),
            "ends_at": effective_ends_at.isoformat(),
            "recurrence_rule": recurrence_rule,
            "entity_ids": [str(eid) for eid in normalized_entity_ids],
            "source_butler": module._butler_name,
        }

    @_tool("core")
    async def reminder_list(
        entity_id: str | None = None,
        due_before: datetime | None = None,
        include_dismissed: bool = False,
    ) -> dict[str, Any]:
        """List reminders for the calling butler.

        Filters:
        - ``entity_id``: restrict to reminders linked to this entity.
        - ``due_before``: restrict to reminders with ``starts_at <= due_before``.
        - ``include_dismissed``: when True, include cancelled reminders
          (default False).

        Returns reminders from ``calendar_events`` where
        ``source_kind = 'internal_reminders'`` and
        ``source_butler = <calling butler>``.
        """
        resolved_entity_id: uuid.UUID | None = None
        if entity_id is not None:
            try:
                resolved_entity_id = uuid.UUID(entity_id.strip())
            except (ValueError, AttributeError) as exc:
                raise ValueError(f"Invalid entity_id: {entity_id!r}") from exc

        reminders = await module._query_reminders(
            entity_id=resolved_entity_id,
            due_before=due_before,
            include_dismissed=include_dismissed,
        )
        return {"reminders": reminders, "count": len(reminders)}

    @_tool("core")
    async def reminder_dismiss(
        event_id: str,
    ) -> dict[str, Any]:
        """Dismiss a reminder.

        For one-time reminders (no recurrence_rule): sets
        ``status = 'cancelled'`` on the ``calendar_events`` row.

        For recurring reminders: cancels the current (earliest
        non-cancelled) instance in ``calendar_event_instances``.  The
        series itself remains active so future instances are still
        projected.

        Returns the updated event state.
        """
        normalized_id = event_id.strip()
        if not normalized_id:
            raise ValueError("event_id must be a non-empty string")
        try:
            parsed_event_id = uuid.UUID(normalized_id)
        except ValueError as exc:
            raise ValueError(f"event_id must be a valid UUID: {normalized_id!r}") from exc

        result = await module._dismiss_reminder(parsed_event_id)
        return result
