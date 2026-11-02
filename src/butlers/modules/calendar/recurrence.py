"""Calendar recurrence implementation."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from butlers.core.tool_call_capture import get_current_runtime_session_id as _get_session_id

from .constants import (
    DEFAULT_SCHEDULED_TASK_DURATION_MINUTES,
    MUTATION_HIGH_IMPACT_ACTIONS,
    MUTATION_STATUS_APPLIED,
    MUTATION_STATUS_NOOP,
    MUTATION_STATUS_PENDING,
    RECURRENCE_HIGH_IMPACT_THRESHOLD,
    RECURRENCE_PROJECTION_WINDOW_DAYS,
    SOURCE_KIND_PROVIDER,
    logger,
)
from .models import CalendarEvent, CalendarEventCreate
from .time_helpers import (
    _recurrence_lines_append_exdate,
    _recurrence_lines_bound_until,
    _rrule_occurrences_in_window,
)


class RecurrenceMixin:
    """Existing Calendar recurrence behavior on the owning module instance."""

    def _count_scope_occurrences(
        self,
        *,
        event: CalendarEvent,
        recurrence_scope: str,
        occurrence_start: datetime,
    ) -> int:
        """Number of occurrences a scope-aware mutation will touch (impact preview).

        ``this`` always touches exactly one occurrence.  ``following`` counts the
        boundary occurrence and every later one inside the rolling projection
        window; ``series`` counts the whole expanded series in that window.
        """
        if recurrence_scope == "this":
            return 1
        recurrence_rule = event.recurrence_rule
        if not recurrence_rule or event.start_at is None:
            return 1
        window_start = event.start_at
        window_end = window_start + timedelta(days=RECURRENCE_PROJECTION_WINDOW_DAYS)
        if event.end_at is not None:
            duration_minutes = max(int((event.end_at - event.start_at).total_seconds() // 60), 1)
        else:
            duration_minutes = DEFAULT_SCHEDULED_TASK_DURATION_MINUTES
        occurrences = _rrule_occurrences_in_window(
            recurrence_rule,
            event.start_at,
            window_start,
            window_end,
            duration_minutes=duration_minutes,
        )
        if recurrence_scope == "following":
            return sum(1 for (start, _end) in occurrences if start >= occurrence_start)
        return len(occurrences)

    async def _mark_instances_exception(
        self,
        *,
        source_id: uuid.UUID | None,
        origin_ref: str,
        occurrence_start: datetime | None = None,
        from_boundary: datetime | None = None,
        cancel: bool = False,
    ) -> None:
        """Mark projected ``calendar_event_instances`` rows as exceptions (fail-open).

        For ``this`` pass ``occurrence_start`` to stamp the single matching row;
        for ``following`` pass ``from_boundary`` to stamp the boundary row and all
        later ones.  ``cancel`` additionally flips status to ``cancelled`` (used by
        the delete path).
        """
        if source_id is None:
            return
        try:
            if not await self._projection_tables_available():
                return
            pool = getattr(self._db, "pool", None) if self._db is not None else None
            if pool is None:
                return
            event_row = await pool.fetchrow(
                "SELECT id FROM calendar_events WHERE source_id = $1 AND origin_ref = $2",
                source_id,
                origin_ref,
            )
            if event_row is None:
                return
            event_db_id = event_row["id"]
            status_clause = ", status = 'cancelled'" if cancel else ""
            if occurrence_start is not None:
                await pool.execute(
                    f"UPDATE calendar_event_instances "
                    f"SET is_exception = true{status_clause}, updated_at = now() "
                    f"WHERE event_id = $1 AND starts_at = $2",
                    event_db_id,
                    occurrence_start,
                )
            elif from_boundary is not None:
                await pool.execute(
                    f"UPDATE calendar_event_instances "
                    f"SET is_exception = true{status_clause}, updated_at = now() "
                    f"WHERE event_id = $1 AND starts_at >= $2",
                    event_db_id,
                    from_boundary,
                )
        except Exception as exc:  # noqa: BLE001 — fail-open projection hygiene
            logger.warning(
                "Marking instance exceptions failed (origin_ref=%s): %s", origin_ref, exc
            )

    async def _maybe_gate_recurrence_impact(
        self,
        *,
        tool_name: str,
        action_type: str,
        tool_args: dict[str, Any],
        request_id: str | None,
        idempotency_key: str,
        action_payload: dict[str, Any],
        recurrence_scope: str,
        occurrences_touched: int,
        approval_bypass: bool,
    ) -> dict[str, Any] | None:
        """Route large ``series`` / ``following`` mutations through the approval gate.

        Returns an ``approval_required`` response when gated, otherwise ``None``.
        A single-occurrence ``this`` mutation is never gated on impact.
        """
        if approval_bypass or self._approval_enqueuer is None:
            return None
        if recurrence_scope == "this":
            return None
        if occurrences_touched < RECURRENCE_HIGH_IMPACT_THRESHOLD:
            return None
        gated_tool_args = dict(tool_args)
        gated_tool_args["_approval_bypass"] = True
        agent_summary = (
            f"Calendar recurrence high-impact action: {tool_name} touches "
            f"{occurrences_touched} occurrences (scope={recurrence_scope}, "
            f"butler={self._butler_name})"
        )
        action_id = await self._approval_enqueuer(tool_name, gated_tool_args, agent_summary)
        response = {
            "status": "approval_required",
            "action_id": action_id,
            "message": "This recurrence-scoped mutation has been queued for approval.",
            "impact": {"occurrences_touched": occurrences_touched, "scope": recurrence_scope},
        }
        source_id = await self._resolve_action_source_id(
            source_kind=SOURCE_KIND_PROVIDER, lane="user"
        )
        await self._finalize_workspace_mutation(
            idempotency_key=idempotency_key,
            action_type=action_type,
            request_id=request_id,
            action_status=MUTATION_STATUS_PENDING,
            action_payload=action_payload,
            action_result=response,
            source_id=source_id,
            origin_ref=None,
            error=None,
        )
        return response

    async def _apply_occurrence_delete(
        self,
        *,
        event_id: str,
        instance_start_at: datetime | None,
        recurrence_scope: str,
        calendar_id: str | None,
        send_updates: str | None,
        request_id: str | None,
        tool_name: str,
        approval_bypass: bool = False,
    ) -> dict[str, Any]:
        """Delete a single occurrence (``this``) or this-and-following of a series."""
        await self._require_calendar_write_permission()
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")
        if instance_start_at is None:
            raise ValueError(
                "instance_start_at is required for recurrence_scope 'this'/'following'"
            )
        if instance_start_at.tzinfo is None:
            raise ValueError("instance_start_at must be timezone-aware")

        provider = self._require_provider()
        resolved_calendar_id = await self._resolve_home_calendar_id(
            event_id=normalized_event_id, override_calendar_id=calendar_id
        )
        normalized_request_id = self._normalize_request_id(request_id)
        occurrence_start = instance_start_at.astimezone(UTC)
        action_payload = {
            "event_id": normalized_event_id,
            "calendar_id": resolved_calendar_id,
            "recurrence_scope": recurrence_scope,
            "instance_start_at": occurrence_start.isoformat(),
            "send_updates": send_updates,
        }
        idempotency_key, replay = await self._prepare_workspace_mutation(
            action_type="workspace_user_delete",
            request_id=normalized_request_id,
            action_payload=action_payload,
        )
        if replay is not None:
            return replay
        source_id = await self._resolve_action_source_id(
            source_kind=SOURCE_KIND_PROVIDER,
            lane="user",
            calendar_id=resolved_calendar_id,
        )

        existing_event = await provider.get_event(
            calendar_id=resolved_calendar_id, event_id=normalized_event_id
        )
        if existing_event is None:
            result = {
                "status": "not_found",
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "event_id": normalized_event_id,
            }
            await self._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_delete",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_NOOP,
                action_payload=action_payload,
                action_result=result,
                source_id=source_id,
                origin_ref=normalized_event_id,
            )
            return result

        # A non-recurring event has no occurrences to exclude — fall back to a
        # whole-event delete so the scope argument degrades gracefully.
        if not existing_event.recurrence_rule:
            await provider.delete_event(
                calendar_id=resolved_calendar_id,
                event_id=normalized_event_id,
                send_updates=send_updates,
            )
            await self._project_provider_mutation(
                source_id=source_id,
                calendar_id=resolved_calendar_id,
                cancelled_ids=[normalized_event_id],
            )
            result = {
                "status": "deleted",
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "event_id": normalized_event_id,
                "recurrence_scope": recurrence_scope,
                "impact": {"occurrences_touched": 1, "scope": recurrence_scope},
            }
            result["projection_freshness"] = await self._refresh_user_projection(
                resolved_calendar_id
            )
            await self._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_delete",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_APPLIED,
                action_payload=action_payload,
                action_result=result,
                source_id=source_id,
                origin_ref=normalized_event_id,
            )
            return result

        occurrences_touched = self._count_scope_occurrences(
            event=existing_event,
            recurrence_scope=recurrence_scope,
            occurrence_start=occurrence_start,
        )
        gate = await self._maybe_gate_recurrence_impact(
            tool_name=tool_name,
            action_type="workspace_user_delete",
            tool_args={
                "event_id": normalized_event_id,
                "calendar_id": resolved_calendar_id,
                "recurrence_scope": recurrence_scope,
                "instance_start_at": occurrence_start.isoformat(),
                "send_updates": send_updates,
            },
            request_id=normalized_request_id,
            idempotency_key=idempotency_key,
            action_payload=action_payload,
            recurrence_scope=recurrence_scope,
            occurrences_touched=occurrences_touched,
            approval_bypass=approval_bypass,
        )
        if gate is not None:
            return gate

        recurrence_lines = await provider.get_recurrence(
            calendar_id=resolved_calendar_id, event_id=normalized_event_id
        )
        if not recurrence_lines:
            recurrence_lines = [f"RRULE:{existing_event.recurrence_rule}"]
        if recurrence_scope == "this":
            new_recurrence = _recurrence_lines_append_exdate(recurrence_lines, occurrence_start)
        else:  # following
            new_recurrence = _recurrence_lines_bound_until(
                recurrence_lines, occurrence_start - timedelta(seconds=1)
            )

        updated_master = await provider.set_recurrence(
            calendar_id=resolved_calendar_id,
            event_id=normalized_event_id,
            recurrence=new_recurrence,
            send_updates=send_updates,
        )
        await self._emit_calendar_audit("delete", existing_event.title, resolved_calendar_id)
        await self._project_provider_mutation(
            source_id=source_id,
            calendar_id=resolved_calendar_id,
            updated_events=[updated_master],
        )
        await self._mark_instances_exception(
            source_id=source_id,
            origin_ref=normalized_event_id,
            occurrence_start=occurrence_start if recurrence_scope == "this" else None,
            from_boundary=occurrence_start if recurrence_scope == "following" else None,
            cancel=True,
        )
        result = {
            "status": "deleted",
            "provider": provider.name,
            "calendar_id": resolved_calendar_id,
            "event_id": normalized_event_id,
            "recurrence_scope": recurrence_scope,
            "recurrence": new_recurrence,
            "impact": {"occurrences_touched": occurrences_touched, "scope": recurrence_scope},
        }
        result["projection_freshness"] = await self._refresh_user_projection(resolved_calendar_id)
        await self._finalize_workspace_mutation(
            idempotency_key=idempotency_key,
            action_type="workspace_user_delete",
            request_id=normalized_request_id,
            action_status=MUTATION_STATUS_APPLIED,
            action_payload=action_payload,
            action_result=result,
            source_id=source_id,
            origin_ref=normalized_event_id,
        )
        return result

    async def _apply_occurrence_update(
        self,
        *,
        event_id: str,
        instance_start_at: datetime | None,
        recurrence_scope: str,
        title: str | None,
        start_at: datetime | None,
        end_at: datetime | None,
        timezone: str | None,
        description: str | None,
        body: str | None,
        location: str | None,
        recurrence_rule: str | None,
        color_id: str | None,
        calendar_id: str | None,
        entity_ids: list[uuid.UUID] | None,
        request_id: str | None,
        tool_name: str,
        clear_entity_ids: bool = False,
        approval_bypass: bool = False,
    ) -> dict[str, Any]:
        """Edit a single occurrence (``this``) or this-and-following of a series.

        ``this`` detaches the named occurrence: the original slot is EXDATE-d from
        the series and the edited fields are carried onto a standalone detached
        event.  ``following`` bounds the original series with ``UNTIL`` and spins a
        new recurring event (carrying the edits) from the boundary onward.
        """
        await self._require_calendar_write_permission()
        if clear_entity_ids and entity_ids != []:
            raise ValueError("clear_entity_ids requires entity_ids=[]")
        normalized_event_id = event_id.strip()
        if not normalized_event_id:
            raise ValueError("event_id must be a non-empty string")
        if instance_start_at is None:
            raise ValueError(
                "instance_start_at is required for recurrence_scope 'this'/'following'"
            )
        if instance_start_at.tzinfo is None:
            raise ValueError("instance_start_at must be timezone-aware")

        provider = self._require_provider()
        resolved_calendar_id = await self._resolve_home_calendar_id(
            event_id=normalized_event_id, override_calendar_id=calendar_id
        )
        normalized_request_id = self._normalize_request_id(request_id)
        occurrence_start = instance_start_at.astimezone(UTC)
        action_payload = {
            "event_id": normalized_event_id,
            "calendar_id": resolved_calendar_id,
            "recurrence_scope": recurrence_scope,
            "instance_start_at": occurrence_start.isoformat(),
            "title": title,
            "start_at": start_at,
            "end_at": end_at,
            "timezone": timezone,
            "location": location,
            "entity_ids": [str(e) for e in entity_ids] if entity_ids is not None else None,
            "clear_entity_ids": clear_entity_ids,
        }
        idempotency_key, replay = await self._prepare_workspace_mutation(
            action_type="workspace_user_update",
            request_id=normalized_request_id,
            action_payload=action_payload,
        )
        if replay is not None:
            return replay
        source_id = await self._resolve_action_source_id(
            source_kind=SOURCE_KIND_PROVIDER,
            lane="user",
            calendar_id=resolved_calendar_id,
        )

        existing_event = await provider.get_event(
            calendar_id=resolved_calendar_id, event_id=normalized_event_id
        )
        if existing_event is None or not existing_event.recurrence_rule:
            result = {
                "status": "not_found",
                "provider": provider.name,
                "calendar_id": resolved_calendar_id,
                "event_id": normalized_event_id,
                "reason": (
                    "event not found"
                    if existing_event is None
                    else "event is not recurring; use calendar_update_event (series scope)"
                ),
            }
            await self._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_user_update",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_NOOP,
                action_payload=action_payload,
                action_result=result,
                source_id=source_id,
                origin_ref=normalized_event_id,
            )
            return result

        occurrences_touched = self._count_scope_occurrences(
            event=existing_event,
            recurrence_scope=recurrence_scope,
            occurrence_start=occurrence_start,
        )
        gate = await self._maybe_gate_recurrence_impact(
            tool_name=tool_name,
            action_type="workspace_user_update",
            tool_args={
                "event_id": normalized_event_id,
                "instance_start_at": occurrence_start.isoformat(),
                "recurrence_scope": recurrence_scope,
                "title": title,
                "calendar_id": resolved_calendar_id,
                "entity_ids": [str(e) for e in entity_ids] if entity_ids is not None else None,
                "clear_entity_ids": clear_entity_ids,
            },
            request_id=normalized_request_id,
            idempotency_key=idempotency_key,
            action_payload=action_payload,
            recurrence_scope=recurrence_scope,
            occurrences_touched=occurrences_touched,
            approval_bypass=approval_bypass,
        )
        if gate is not None:
            return gate

        recurrence_lines = await provider.get_recurrence(
            calendar_id=resolved_calendar_id, event_id=normalized_event_id
        )
        if not recurrence_lines:
            recurrence_lines = [f"RRULE:{existing_event.recurrence_rule}"]

        duration = (
            existing_event.end_at - existing_event.start_at
            if existing_event.end_at and existing_event.start_at
            else timedelta(minutes=DEFAULT_SCHEDULED_TASK_DURATION_MINUTES)
        )
        new_start = start_at if start_at is not None else occurrence_start
        new_end = end_at if end_at is not None else new_start + duration
        detached_recurrence: str | None = None
        if recurrence_scope != "this":
            carried_rule = recurrence_rule or existing_event.recurrence_rule
            # CalendarEventCreate re-validates the rule and requires the RRULE:
            # prefix; the stored form on a CalendarEvent is prefix-stripped.
            if carried_rule:
                detached_recurrence = (
                    carried_rule
                    if carried_rule.upper().startswith("RRULE:")
                    else f"RRULE:{carried_rule}"
                )
        detached_payload = CalendarEventCreate(
            title=title if title is not None else existing_event.title,
            start_at=new_start,
            end_at=new_end,
            timezone=timezone or existing_event.timezone,
            description=description if description is not None else existing_event.description,
            body=body if body is not None else existing_event.body,
            location=location if location is not None else existing_event.location,
            attendees=[attendee.email for attendee in existing_event.attendees],
            recurrence_rule=detached_recurrence,
            color_id=color_id if color_id is not None else existing_event.color_id,
        )

        if recurrence_scope == "this":
            new_recurrence = _recurrence_lines_append_exdate(recurrence_lines, occurrence_start)
        else:  # following
            new_recurrence = _recurrence_lines_bound_until(
                recurrence_lines, occurrence_start - timedelta(seconds=1)
            )
        updated_master = await provider.set_recurrence(
            calendar_id=resolved_calendar_id,
            event_id=normalized_event_id,
            recurrence=new_recurrence,
        )
        detached_event = await provider.create_event(
            calendar_id=resolved_calendar_id, payload=detached_payload
        )
        session_id_str = _get_session_id()
        detached_event = detached_event.model_copy(
            update={
                "source_butler": self._butler_name,
                "source_session_id": session_id_str,
                "entity_ids": entity_ids if entity_ids is not None else detached_event.entity_ids,
            }
        )
        await self._emit_calendar_audit(
            "update", detached_event.title or existing_event.title, resolved_calendar_id
        )
        # The recurrence master keeps its existing links for untouched occurrences.
        # The detached/remainder event alone owns the explicit replacement signal.
        await self._project_provider_mutation(
            source_id=source_id,
            calendar_id=resolved_calendar_id,
            updated_events=[updated_master],
        )
        await self._project_provider_mutation(
            source_id=source_id,
            calendar_id=resolved_calendar_id,
            updated_events=[detached_event],
            clear_entity_ids=clear_entity_ids,
        )
        await self._mark_instances_exception(
            source_id=source_id,
            origin_ref=normalized_event_id,
            occurrence_start=occurrence_start if recurrence_scope == "this" else None,
            from_boundary=occurrence_start if recurrence_scope == "following" else None,
            cancel=True,
        )
        result = {
            "status": "updated",
            "provider": provider.name,
            "calendar_id": resolved_calendar_id,
            "event_id": normalized_event_id,
            "recurrence_scope": recurrence_scope,
            "recurrence": new_recurrence,
            "event": self._event_to_payload(detached_event),
            "impact": {"occurrences_touched": occurrences_touched, "scope": recurrence_scope},
        }
        result["projection_freshness"] = await self._refresh_user_projection(resolved_calendar_id)
        await self._finalize_workspace_mutation(
            idempotency_key=idempotency_key,
            action_type="workspace_user_update",
            request_id=normalized_request_id,
            action_status=MUTATION_STATUS_APPLIED,
            action_payload=action_payload,
            action_result=result,
            source_id=source_id,
            origin_ref=normalized_event_id,
        )
        return result

    async def _gate_high_impact_mutation(
        self,
        *,
        action_type: str,
        tool_name: str,
        tool_args: dict[str, Any],
        request_id: str | None,
        idempotency_key: str,
        action_payload: dict[str, Any],
        source_kind: str,
    ) -> dict[str, Any] | None:
        if action_type not in MUTATION_HIGH_IMPACT_ACTIONS:
            return None
        if self._approval_enqueuer is None:
            return None

        gated_tool_args = dict(tool_args)
        gated_tool_args["_approval_bypass"] = True
        agent_summary = (
            f"Calendar workspace high-impact action requested: {tool_name} "
            f"(butler={self._butler_name})"
        )
        action_id = await self._approval_enqueuer(tool_name, gated_tool_args, agent_summary)
        response = {
            "status": "approval_required",
            "action_id": action_id,
            "message": "This workspace mutation has been queued for approval.",
        }
        source_id = await self._resolve_action_source_id(source_kind=source_kind, lane="butler")
        await self._finalize_workspace_mutation(
            idempotency_key=idempotency_key,
            action_type=action_type,
            request_id=request_id,
            action_status=MUTATION_STATUS_PENDING,
            action_payload=action_payload,
            action_result=response,
            source_id=source_id,
            origin_ref=None,
            error=None,
        )
        return response

    @staticmethod
    def _rrule_components(recurrence_rule: str) -> dict[str, str]:
        normalized = recurrence_rule.removeprefix("RRULE:")
        values: dict[str, str] = {}
        for part in normalized.split(";"):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            values[key.strip().upper()] = value.strip()
        return values

    @staticmethod
    def _rrule_until(recurrence_rule: str) -> datetime | None:
        from . import CalendarModule

        components = CalendarModule._rrule_components(recurrence_rule)
        until_raw = components.get("UNTIL")
        if not until_raw:
            return None
        if until_raw.endswith("Z"):
            until_raw = f"{until_raw[:-1]}+00:00"
        for pattern in ("%Y%m%dT%H%M%S%z", "%Y%m%d"):
            try:
                parsed = datetime.strptime(until_raw, pattern)
            except ValueError:
                continue
            if pattern == "%Y%m%d":
                return datetime(parsed.year, parsed.month, parsed.day, tzinfo=UTC)
            return parsed.astimezone(UTC)
        return None

    @staticmethod
    def _rrule_to_cron(start_at: datetime, recurrence_rule: str) -> str:
        from . import CalendarModule

        components = CalendarModule._rrule_components(recurrence_rule)
        freq = components.get("FREQ")
        if freq is None:
            raise ValueError("recurrence_rule must include FREQ")

        minute = start_at.minute
        hour = start_at.hour
        if freq == "DAILY":
            return f"{minute} {hour} * * *"
        if freq == "WEEKLY":
            byday = components.get("BYDAY")
            day_lookup = {
                "SU": "0",
                "MO": "1",
                "TU": "2",
                "WE": "3",
                "TH": "4",
                "FR": "5",
                "SA": "6",
            }
            if byday:
                parts = [day_lookup[item] for item in byday.split(",") if item in day_lookup]
                if not parts:
                    raise ValueError("recurrence_rule BYDAY must contain at least one valid day")
                day_of_week = ",".join(parts)
            else:
                day_of_week = str((start_at.weekday() + 1) % 7)
            return f"{minute} {hour} * * {day_of_week}"
        if freq == "MONTHLY":
            day_of_month = components.get("BYMONTHDAY") or str(start_at.day)
            return f"{minute} {hour} {day_of_month} * *"
        if freq == "YEARLY":
            return f"{minute} {hour} {start_at.day} {start_at.month} *"
        raise ValueError("Unsupported recurrence_rule frequency for scheduler projection")
