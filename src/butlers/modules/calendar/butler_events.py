"""Calendar butler events implementation."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo

from butlers.core.scheduler import schedule_create as _schedule_create
from butlers.core.scheduler import schedule_delete as _schedule_delete
from butlers.core.scheduler import schedule_update as _schedule_update

from .constants import (
    _CALENDAR_EVENT_DEFAULT_ACTION,
    BUTLER_EVENT_SOURCE_REMINDER,
    BUTLER_EVENT_SOURCE_SCHEDULED,
    BUTLER_EVENT_TITLE_PREFIX,
    BUTLER_GENERATED_PRIVATE_KEY,
    BUTLER_NAME_PRIVATE_KEY,
    DEFAULT_BUTLER_NAME,
    MUTATION_STATUS_APPLIED,
    MUTATION_STATUS_FAILED,
    MUTATION_STATUS_NOOP,
    SOURCE_KIND_INTERNAL_REMINDERS,
    SOURCE_KIND_INTERNAL_SCHEDULER,
    ButlerEventSourceHint,
)
from .google_mapping import _normalize_optional_text
from .models import _ensure_valid_timezone, _normalize_recurrence_rule
from .time_helpers import _recurrence_lines_bound_until

if TYPE_CHECKING:
    from . import CalendarModule


class ButlerEventsMixin:
    """Existing Calendar butler events behavior on the owning module instance."""

    @staticmethod
    def _normalize_butler_event_source_hint(
        source_hint: str | None,
    ) -> ButlerEventSourceHint | None:
        if source_hint is None:
            return None
        if source_hint == BUTLER_EVENT_SOURCE_SCHEDULED:
            return BUTLER_EVENT_SOURCE_SCHEDULED
        if source_hint == BUTLER_EVENT_SOURCE_REMINDER:
            return BUTLER_EVENT_SOURCE_REMINDER
        raise ValueError("source_hint must be one of: scheduled_task | butler_reminder")

    async def _find_scheduled_task_target(self, event_id: str) -> uuid.UUID | None:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None or not await self._table_exists("scheduled_tasks"):
            return None

        columns = await self._table_columns("scheduled_tasks")
        # Try calendar_event_id (text) first — works for Google Calendar IDs
        if "calendar_event_id" in columns:
            row = await pool.fetchrow(
                "SELECT id FROM scheduled_tasks WHERE calendar_event_id = $1 LIMIT 1",
                event_id,
            )
            if row is not None:
                return row["id"]
        # Try as UUID primary key
        try:
            event_uuid = uuid.UUID(event_id)
        except ValueError:
            return None
        row = await pool.fetchrow(
            "SELECT id FROM scheduled_tasks WHERE id = $1 LIMIT 1",
            event_uuid,
        )
        if row is None:
            return None
        return row["id"]

    async def _find_native_reminder_target(self, event_id: str) -> uuid.UUID | None:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None or not await self._table_exists("calendar_events"):
            return None

        try:
            event_uuid = uuid.UUID(event_id)
        except ValueError:
            return None
        row = await pool.fetchrow(
            """
            SELECT e.id
            FROM calendar_events e
            JOIN calendar_sources s ON s.id = e.source_id
            WHERE e.id = $1
              AND s.source_kind = $2
              AND e.source_butler = $3
            LIMIT 1
            """,
            event_uuid,
            SOURCE_KIND_INTERNAL_REMINDERS,
            self._resolve_effective_butler_name(),
        )
        if row is None:
            return None
        return row["id"]

    async def _resolve_butler_event_target(
        self,
        *,
        event_id: str,
        source_hint: ButlerEventSourceHint | None,
    ) -> tuple[ButlerEventSourceHint, uuid.UUID]:
        normalized = event_id.strip()
        if not normalized:
            raise ValueError("event_id must be a non-empty string")

        if source_hint == BUTLER_EVENT_SOURCE_SCHEDULED:
            schedule_id = await self._find_scheduled_task_target(normalized)
            if schedule_id is None:
                raise ValueError(f"No scheduled task found for event_id '{event_id}'")
            return BUTLER_EVENT_SOURCE_SCHEDULED, schedule_id
        if source_hint == BUTLER_EVENT_SOURCE_REMINDER:
            reminder_id = await self._find_native_reminder_target(normalized)
            if reminder_id is None:
                raise ValueError(f"No reminder found for event_id '{event_id}'")
            return BUTLER_EVENT_SOURCE_REMINDER, reminder_id

        schedule_id = await self._find_scheduled_task_target(normalized)
        if schedule_id is not None:
            return BUTLER_EVENT_SOURCE_SCHEDULED, schedule_id
        reminder_id = await self._find_native_reminder_target(normalized)
        if reminder_id is not None:
            return BUTLER_EVENT_SOURCE_REMINDER, reminder_id
        raise ValueError(f"No butler event found for event_id '{event_id}'")

    @staticmethod
    def _ensure_butler_title(title: str) -> str:
        normalized = title.strip()
        if not normalized:
            raise ValueError("title must be a non-empty string")

        prefix_len = len(BUTLER_EVENT_TITLE_PREFIX)
        if normalized[:prefix_len].upper() == BUTLER_EVENT_TITLE_PREFIX:
            suffix = normalized[prefix_len:].lstrip()
        else:
            suffix = normalized
        return BUTLER_EVENT_TITLE_PREFIX if not suffix else f"{BUTLER_EVENT_TITLE_PREFIX} {suffix}"

    @staticmethod
    def _build_butler_private_metadata(*, butler_name: str = DEFAULT_BUTLER_NAME) -> dict[str, str]:
        normalized_name = butler_name.strip() or DEFAULT_BUTLER_NAME
        return {
            BUTLER_GENERATED_PRIVATE_KEY: "true",
            BUTLER_NAME_PRIVATE_KEY: normalized_name,
        }


def register_butler_event_tools(
    module: CalendarModule, mcp: Any, _tool: Callable[..., Any], butler_name: str
) -> None:

    @_tool("butler_events")
    async def calendar_create_butler_event(
        butler_name: str,
        title: str,
        start_at: datetime,
        end_at: datetime | None = None,
        timezone: str | None = None,
        description: str | None = None,
        location: str | None = None,
        recurrence_rule: str | None = None,
        cron: str | None = None,
        until_at: datetime | None = None,
        action: str = _CALENDAR_EVENT_DEFAULT_ACTION,
        action_args: dict[str, Any] | None = None,
        source_hint: ButlerEventSourceHint | None = None,
        request_id: str | None = None,
        _approval_bypass: bool = False,
    ) -> dict[str, Any]:
        """Create a butler-view workspace event as schedule or reminder.

        ``description`` and ``location`` are optional free-text fields carried
        onto the underlying scheduler/reminder row, surfaced on the workspace
        projection, and pushed to the Butlers Google subcalendar event. They
        let butler-authored events (e.g. accepted calendar proposals) preserve
        the proposal's description/location instead of dropping them.

        ``source_hint`` selects ``scheduled_task`` or ``butler_reminder``.
        When omitted, one-off events use reminders when available; recurring
        events use scheduled tasks.

        Calendar-native reminders support RRULE recurrence. Cron expressions,
        executable actions, and action arguments belong to scheduled-task events
        and are rejected when ``source_hint`` selects ``butler_reminder``.
        """
        normalized_butler = butler_name.strip()
        if not normalized_butler:
            raise ValueError("butler_name must be a non-empty string")
        if normalized_butler != module._butler_name:
            raise ValueError(
                f"butler_name '{normalized_butler}' does not match current butler "
                f"'{module._butler_name}'"
            )
        if start_at.tzinfo is None:
            raise ValueError("start_at must be timezone-aware")
        normalized_title = title.strip()
        if not normalized_title:
            raise ValueError("title must be a non-empty string")
        normalized_description = _normalize_optional_text(description)
        normalized_location = _normalize_optional_text(location)
        effective_timezone = (timezone or module._require_config().timezone).strip()
        _ensure_valid_timezone(effective_timezone)
        effective_end = end_at or (start_at + timedelta(minutes=15))
        if effective_end.tzinfo is None:
            raise ValueError("end_at must be timezone-aware when provided")
        if effective_end <= start_at:
            raise ValueError("end_at must be after start_at")
        requested_until_at = until_at
        normalized_rule = _normalize_recurrence_rule(recurrence_rule)
        if until_at is None and normalized_rule is not None:
            until_at = module._rrule_until(normalized_rule)

        normalized_source_hint = module._normalize_butler_event_source_hint(source_hint)
        source_kind = normalized_source_hint
        if source_kind is None:
            if normalized_rule is None and cron is None:
                source_kind = BUTLER_EVENT_SOURCE_REMINDER
            else:
                source_kind = BUTLER_EVENT_SOURCE_SCHEDULED
        if source_kind == BUTLER_EVENT_SOURCE_REMINDER:
            if cron is not None:
                raise ValueError(
                    "cron is not supported for butler_reminder events; "
                    "use source_hint='scheduled_task'"
                )
            if requested_until_at is not None:
                if normalized_rule is None:
                    raise ValueError("until_at requires recurrence_rule for butler_reminder events")
                normalized_rule = _recurrence_lines_bound_until(
                    [normalized_rule],
                    requested_until_at,
                )[0]
                until_at = requested_until_at
            if action.strip() not in {"", _CALENDAR_EVENT_DEFAULT_ACTION}:
                raise ValueError(
                    "custom action is not supported for butler_reminder events; "
                    "use source_hint='scheduled_task'"
                )
            if action_args:
                raise ValueError(
                    "action_args are not supported for butler_reminder events; "
                    "use source_hint='scheduled_task'"
                )

        normalized_request_id = module._normalize_request_id(request_id)
        action_payload = {
            "butler_name": normalized_butler,
            "title": normalized_title,
            "start_at": start_at,
            "end_at": effective_end,
            "timezone": effective_timezone,
            "description": normalized_description,
            "location": normalized_location,
            "recurrence_rule": normalized_rule,
            "cron": cron,
            "until_at": until_at,
            "action": action,
            "action_args": action_args,
            "source_hint": source_kind,
        }
        idempotency_key, replay = await module._prepare_workspace_mutation(
            action_type="workspace_butler_create",
            request_id=normalized_request_id,
            action_payload=action_payload,
            allow_pending_replay=_approval_bypass,
        )
        if replay is not None:
            return replay

        pool = getattr(module._db, "pool", None) if module._db is not None else None
        if pool is None:
            raise RuntimeError("Database pool is not available")

        projection_source_kind = (
            SOURCE_KIND_INTERNAL_REMINDERS
            if source_kind == BUTLER_EVENT_SOURCE_REMINDER
            else SOURCE_KIND_INTERNAL_SCHEDULER
        )
        source_id = await module._resolve_action_source_id(
            source_kind=projection_source_kind,
            lane="butler",
        )

        try:
            if source_kind == BUTLER_EVENT_SOURCE_REMINDER:
                reminder_id, reminder = await module._insert_reminder_to_calendar_events(
                    title=normalized_title,
                    body=None,
                    description=normalized_description,
                    location=normalized_location,
                    starts_at=start_at,
                    ends_at=effective_end,
                    timezone=effective_timezone,
                    recurrence_rule=normalized_rule,
                    entity_ids=[],
                )
                origin_ref = str(reminder_id)
                result: dict[str, Any] = {
                    "status": "created",
                    "source_type": BUTLER_EVENT_SOURCE_REMINDER,
                    "butler_name": module._butler_name,
                    "event_id": str(reminder_id),
                    "reminder_id": str(reminder_id),
                    "reminder": reminder,
                }
            else:
                event_link_id = uuid.uuid4()
                effective_cron = cron
                if effective_cron is None:
                    if normalized_rule is None:
                        raise ValueError(
                            "cron or recurrence_rule is required for scheduled_task events"
                        )
                    effective_cron = module._rrule_to_cron(start_at, normalized_rule)
                args = action_args or {}
                dispatch_mode = str(args.get("dispatch_mode") or "prompt").strip().lower()
                if dispatch_mode not in {"prompt", "job"}:
                    raise ValueError("dispatch_mode must be 'prompt' or 'job'")
                schedule_name = str(args.get("name") or f"calendar-event-{uuid.uuid4().hex[:8]}")
                if dispatch_mode == "job":
                    job_name = str(args.get("job_name") or action).strip()
                    if not job_name:
                        raise ValueError("job_name must be non-empty for job dispatch mode")
                    job_args = args.get("job_args")
                    if job_args is None:
                        job_args = {}
                    if not isinstance(job_args, dict):
                        raise ValueError("job_args must be an object when provided")
                    task_id = await _schedule_create(
                        pool,
                        schedule_name,
                        effective_cron,
                        None,
                        dispatch_mode="job",
                        job_name=job_name,
                        job_args=job_args,
                        timezone=effective_timezone,
                        start_at=start_at,
                        end_at=effective_end,
                        until_at=until_at,
                        display_title=normalized_title,
                        description=normalized_description,
                        location=normalized_location,
                        calendar_event_id=str(event_link_id),
                        stagger_key=module._butler_name,
                    )
                else:
                    # Build a meaningful firing prompt when action is the generic default.
                    # The default "Run butler event" carries no context; the scheduled
                    # session would improvise, causing off-cron freelancing (bu-0g76b).
                    # Replace it with a prompt derived from the event title and start time.
                    effective_action = action.strip()
                    if not effective_action or (effective_action == _CALENDAR_EVENT_DEFAULT_ACTION):
                        try:
                            _tz_obj = ZoneInfo(effective_timezone)
                            _local_start = start_at.astimezone(_tz_obj)
                            _time_str = _local_start.strftime("%H:%M %Z")
                        except Exception:
                            _time_str = start_at.isoformat()
                        _notes = str((action_args or {}).get("notes") or "").strip()
                        _notes_suffix = f" {_notes}" if _notes else ""
                        effective_prompt = (
                            f"Scheduled event: {normalized_title} at {_time_str}.{_notes_suffix}"
                        )
                    else:
                        effective_prompt = effective_action
                    task_id = await _schedule_create(
                        pool,
                        schedule_name,
                        effective_cron,
                        effective_prompt,
                        dispatch_mode="prompt",
                        timezone=effective_timezone,
                        start_at=start_at,
                        end_at=effective_end,
                        until_at=until_at,
                        display_title=normalized_title,
                        description=normalized_description,
                        location=normalized_location,
                        calendar_event_id=str(event_link_id),
                        stagger_key=module._butler_name,
                    )
                origin_ref = str(task_id)
                result = {
                    "status": "created",
                    "source_type": BUTLER_EVENT_SOURCE_SCHEDULED,
                    "butler_name": module._butler_name,
                    "event_id": str(event_link_id),
                    "schedule_id": str(task_id),
                    "cron": effective_cron,
                }

            result["projection_freshness"] = await module._refresh_butler_projection()
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_butler_create",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_APPLIED,
                action_payload=action_payload,
                action_result=result,
                source_id=source_id,
                origin_ref=origin_ref,
            )
            return result
        except Exception as exc:
            error_payload = {"status": "error", "error": str(exc)}
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_butler_create",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_FAILED,
                action_payload=action_payload,
                action_result=error_payload,
                source_id=source_id,
                origin_ref=None,
                error=str(exc),
            )
            return error_payload

    @_tool("butler_events")
    async def calendar_update_butler_event(
        event_id: str,
        title: str | None = None,
        start_at: datetime | None = None,
        end_at: datetime | None = None,
        timezone: str | None = None,
        recurrence_rule: str | None = None,
        cron: str | None = None,
        until_at: datetime | None = None,
        enabled: bool | None = None,
        source_hint: ButlerEventSourceHint | None = None,
        body: str | None = None,
        entity_ids: list[uuid.UUID] | None = None,
        request_id: str | None = None,
        _approval_bypass: bool = False,
    ) -> dict[str, Any]:
        """Update a butler schedule/reminder event.

        ``source_hint`` selects ``scheduled_task`` or ``butler_reminder``.
        ``body`` is stored for calendar-native reminders and remains
        audit-only for scheduled tasks.
        """
        normalized_source_hint = module._normalize_butler_event_source_hint(source_hint)
        normalized_request_id = module._normalize_request_id(request_id)
        action_payload = {
            "event_id": event_id,
            "title": title,
            "start_at": start_at,
            "end_at": end_at,
            "timezone": timezone,
            "recurrence_rule": recurrence_rule,
            "cron": cron,
            "until_at": until_at,
            "enabled": enabled,
            "source_hint": normalized_source_hint,
            "body": body,
            "entity_ids": [str(e) for e in entity_ids] if entity_ids is not None else None,
        }
        idempotency_key, replay = await module._prepare_workspace_mutation(
            action_type="workspace_butler_update",
            request_id=normalized_request_id,
            action_payload=action_payload,
            allow_pending_replay=_approval_bypass,
        )
        if replay is not None:
            return replay

        try:
            source_type, target_id = await module._resolve_butler_event_target(
                event_id=event_id,
                source_hint=normalized_source_hint,
            )
            source_kind = (
                SOURCE_KIND_INTERNAL_REMINDERS
                if source_type == BUTLER_EVENT_SOURCE_REMINDER
                else SOURCE_KIND_INTERNAL_SCHEDULER
            )
            source_id = await module._resolve_action_source_id(
                source_kind=source_kind,
                lane="butler",
            )
            pool = getattr(module._db, "pool", None) if module._db is not None else None
            if pool is None:
                raise RuntimeError("Database pool is not available")

            native_reminder_target: uuid.UUID | None = None
            if source_type == BUTLER_EVENT_SOURCE_REMINDER:
                native_reminder_target = target_id
                if cron is not None:
                    raise ValueError(
                        "cron is not supported for butler_reminder events; "
                        "use source_hint='scheduled_task'"
                    )
                reminder = await module._update_native_reminder_event(
                    reminder_id=native_reminder_target,
                    title=title,
                    body=body,
                    start_at=start_at,
                    end_at=end_at,
                    timezone=timezone,
                    until_at=until_at,
                    recurrence_rule=recurrence_rule,
                    enabled=enabled,
                )
                origin_ref = str(target_id)
                result: dict[str, Any] = {
                    "status": "updated",
                    "source_type": BUTLER_EVENT_SOURCE_REMINDER,
                    "butler_name": module._butler_name,
                    "event_id": str(reminder["id"]),
                    "reminder_id": str(target_id),
                    "reminder": reminder,
                }
            else:
                update_fields: dict[str, Any] = {}
                if title is not None:
                    update_fields["display_title"] = title
                if start_at is not None:
                    update_fields["start_at"] = start_at
                if end_at is not None:
                    update_fields["end_at"] = end_at
                if timezone is not None:
                    _ensure_valid_timezone(timezone)
                    update_fields["timezone"] = timezone
                if until_at is not None:
                    update_fields["until_at"] = until_at
                effective_cron = cron
                normalized_rule = _normalize_recurrence_rule(recurrence_rule)
                if effective_cron is None and normalized_rule is not None:
                    if start_at is None:
                        raise ValueError(
                            "start_at is required when recurrence_rule is provided without cron"
                        )
                    effective_cron = module._rrule_to_cron(start_at, normalized_rule)
                if effective_cron is not None:
                    update_fields["cron"] = effective_cron
                if enabled is not None:
                    update_fields["enabled"] = enabled
                await _schedule_update(
                    pool,
                    target_id,
                    stagger_key=module._butler_name,
                    **update_fields,
                )
                schedule_row = await pool.fetchrow(
                    "SELECT calendar_event_id FROM scheduled_tasks WHERE id = $1",
                    target_id,
                )
                event_link = (
                    str(schedule_row["calendar_event_id"])
                    if schedule_row is not None and schedule_row["calendar_event_id"] is not None
                    else str(target_id)
                )
                origin_ref = str(target_id)
                result = {
                    "status": "updated",
                    "source_type": BUTLER_EVENT_SOURCE_SCHEDULED,
                    "butler_name": module._butler_name,
                    "event_id": event_link,
                    "schedule_id": str(target_id),
                }

            result["projection_freshness"] = await module._refresh_butler_projection()
            # Update entity associations on the projection row if entity_ids provided.
            if entity_ids is not None and native_reminder_target is not None:
                await module._upsert_event_entities(
                    event_id=native_reminder_target,
                    entity_ids=entity_ids,
                )
            elif entity_ids is not None and source_id is not None:
                await module._update_butler_event_entities(
                    source_id=source_id,
                    origin_ref=origin_ref,
                    entity_ids=entity_ids,
                )
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_butler_update",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_APPLIED,
                action_payload=action_payload,
                action_result=result,
                source_id=source_id,
                origin_ref=origin_ref,
            )
            return result
        except Exception as exc:
            error_payload = {"status": "error", "error": str(exc)}
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_butler_update",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_FAILED,
                action_payload=action_payload,
                action_result=error_payload,
                source_id=None,
                origin_ref=None,
                error=str(exc),
            )
            return error_payload

    @_tool("butler_events")
    async def calendar_delete_butler_event(
        event_id: str,
        scope: Literal["this", "following", "series"] = "series",
        instance_start_at: datetime | None = None,
        source_hint: ButlerEventSourceHint | None = None,
        request_id: str | None = None,
        _approval_bypass: bool = False,
    ) -> dict[str, Any]:
        """Delete a butler schedule/reminder event at the requested scope.

        ``source_hint`` selects ``scheduled_task`` or ``butler_reminder``.
        """
        if scope not in {"this", "following", "series"}:
            raise ValueError("scope must be one of: this | following | series")
        if scope != "series":
            if instance_start_at is None:
                raise ValueError("instance_start_at is required for scope 'this'/'following'")
            if instance_start_at.tzinfo is None:
                raise ValueError("instance_start_at must be timezone-aware")
        normalized_source_hint = module._normalize_butler_event_source_hint(source_hint)
        normalized_request_id = module._normalize_request_id(request_id)
        action_payload = {
            "event_id": event_id,
            "scope": scope,
            "instance_start_at": instance_start_at,
            "source_hint": normalized_source_hint,
        }
        idempotency_key, replay = await module._prepare_workspace_mutation(
            action_type="workspace_butler_delete",
            request_id=normalized_request_id,
            action_payload=action_payload,
            allow_pending_replay=_approval_bypass,
        )
        if replay is not None:
            return replay

        tentative_source_kind = (
            SOURCE_KIND_INTERNAL_REMINDERS
            if normalized_source_hint == BUTLER_EVENT_SOURCE_REMINDER
            else SOURCE_KIND_INTERNAL_SCHEDULER
        )
        if not _approval_bypass and scope != "this":
            approval_result = await module._gate_high_impact_mutation(
                action_type="workspace_butler_delete",
                tool_name="calendar_delete_butler_event",
                tool_args={
                    "event_id": event_id,
                    "scope": scope,
                    "instance_start_at": instance_start_at,
                    "source_hint": source_hint,
                    "request_id": normalized_request_id,
                },
                request_id=normalized_request_id,
                idempotency_key=idempotency_key,
                action_payload=action_payload,
                source_kind=tentative_source_kind,
            )
            if approval_result is not None:
                return approval_result

        try:
            source_type, target_id = await module._resolve_butler_event_target(
                event_id=event_id,
                source_hint=normalized_source_hint,
            )
            source_kind = (
                SOURCE_KIND_INTERNAL_REMINDERS
                if source_type == BUTLER_EVENT_SOURCE_REMINDER
                else SOURCE_KIND_INTERNAL_SCHEDULER
            )
            source_id = await module._resolve_action_source_id(
                source_kind=source_kind,
                lane="butler",
            )
            if source_type == BUTLER_EVENT_SOURCE_REMINDER:
                deleted = await module._delete_native_reminder_event(
                    target_id,
                    scope=scope,
                    instance_start_at=instance_start_at,
                )
            else:
                if scope != "series":
                    raise ValueError(
                        "Occurrence-scoped deletion is only supported for calendar-native reminders"
                    )
                pool = getattr(module._db, "pool", None) if module._db is not None else None
                if pool is None:
                    raise RuntimeError("Database pool is not available")
                await _schedule_delete(pool, target_id)
                deleted = True

            if deleted:
                result: dict[str, Any] = {
                    "status": "deleted",
                    "source_type": source_type,
                    "butler_name": module._butler_name,
                    "event_id": event_id,
                }
                mutation_status = MUTATION_STATUS_APPLIED
            else:
                result = {
                    "status": "not_found",
                    "source_type": source_type,
                    "butler_name": module._butler_name,
                    "event_id": event_id,
                }
                mutation_status = MUTATION_STATUS_NOOP
            result["scope"] = scope
            if instance_start_at is not None:
                result["instance_start_at"] = instance_start_at.astimezone(UTC).isoformat()
            result["projection_freshness"] = await module._refresh_butler_projection()
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_butler_delete",
                request_id=normalized_request_id,
                action_status=mutation_status,
                action_payload=action_payload,
                action_result=result,
                source_id=source_id,
                origin_ref=str(target_id),
            )
            return result
        except Exception as exc:
            error_payload = {"status": "error", "error": str(exc)}
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_butler_delete",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_FAILED,
                action_payload=action_payload,
                action_result=error_payload,
                source_id=None,
                origin_ref=None,
                error=str(exc),
            )
            return error_payload

    @_tool("butler_events")
    async def calendar_toggle_butler_event(
        event_id: str,
        enabled: bool,
        source_hint: ButlerEventSourceHint | None = None,
        request_id: str | None = None,
        _approval_bypass: bool = False,
    ) -> dict[str, Any]:
        """Pause/resume a butler schedule/reminder event.

        ``source_hint`` selects ``scheduled_task`` or ``butler_reminder``.
        """
        normalized_source_hint = module._normalize_butler_event_source_hint(source_hint)
        normalized_request_id = module._normalize_request_id(request_id)
        action_payload = {
            "event_id": event_id,
            "enabled": enabled,
            "source_hint": normalized_source_hint,
        }
        idempotency_key, replay = await module._prepare_workspace_mutation(
            action_type="workspace_butler_toggle",
            request_id=normalized_request_id,
            action_payload=action_payload,
            allow_pending_replay=_approval_bypass,
        )
        if replay is not None:
            return replay

        tentative_source_kind = (
            SOURCE_KIND_INTERNAL_REMINDERS
            if normalized_source_hint == BUTLER_EVENT_SOURCE_REMINDER
            else SOURCE_KIND_INTERNAL_SCHEDULER
        )
        if not _approval_bypass:
            approval_result = await module._gate_high_impact_mutation(
                action_type="workspace_butler_toggle",
                tool_name="calendar_toggle_butler_event",
                tool_args={
                    "event_id": event_id,
                    "enabled": enabled,
                    "source_hint": source_hint,
                    "request_id": normalized_request_id,
                },
                request_id=normalized_request_id,
                idempotency_key=idempotency_key,
                action_payload=action_payload,
                source_kind=tentative_source_kind,
            )
            if approval_result is not None:
                return approval_result

        try:
            source_type, target_id = await module._resolve_butler_event_target(
                event_id=event_id,
                source_hint=normalized_source_hint,
            )
            source_kind = (
                SOURCE_KIND_INTERNAL_REMINDERS
                if source_type == BUTLER_EVENT_SOURCE_REMINDER
                else SOURCE_KIND_INTERNAL_SCHEDULER
            )
            source_id = await module._resolve_action_source_id(
                source_kind=source_kind,
                lane="butler",
            )
            if source_type == BUTLER_EVENT_SOURCE_REMINDER:
                reminder = await module._toggle_native_reminder_event(
                    target_id,
                    enabled,
                )
                event_link = str(reminder["id"])
                result: dict[str, Any] = {
                    "status": "updated",
                    "source_type": source_type,
                    "butler_name": module._butler_name,
                    "event_id": event_link,
                    "reminder_id": str(target_id),
                    "enabled": enabled,
                    "reminder": reminder,
                }
            else:
                pool = getattr(module._db, "pool", None) if module._db is not None else None
                if pool is None:
                    raise RuntimeError("Database pool is not available")
                await _schedule_update(
                    pool,
                    target_id,
                    stagger_key=module._butler_name,
                    enabled=enabled,
                )
                row = await pool.fetchrow(
                    "SELECT calendar_event_id FROM scheduled_tasks WHERE id = $1",
                    target_id,
                )
                event_link = (
                    str(row["calendar_event_id"])
                    if row is not None and row["calendar_event_id"] is not None
                    else str(target_id)
                )
                result = {
                    "status": "updated",
                    "source_type": source_type,
                    "butler_name": module._butler_name,
                    "event_id": event_link,
                    "schedule_id": str(target_id),
                    "enabled": enabled,
                }

            result["projection_freshness"] = await module._refresh_butler_projection()
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_butler_toggle",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_APPLIED,
                action_payload=action_payload,
                action_result=result,
                source_id=source_id,
                origin_ref=str(target_id),
            )
            return result
        except Exception as exc:
            error_payload = {"status": "error", "error": str(exc)}
            await module._finalize_workspace_mutation(
                idempotency_key=idempotency_key,
                action_type="workspace_butler_toggle",
                request_id=normalized_request_id,
                action_status=MUTATION_STATUS_FAILED,
                action_payload=action_payload,
                action_result=error_payload,
                source_id=None,
                origin_ref=None,
                error=str(exc),
            )
            return error_payload
