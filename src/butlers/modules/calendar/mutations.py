"""Calendar mutations implementation."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from butlers.calendar_action_result import reconstruct_action_result
from butlers.core.audit import write_audit_entry
from butlers.core.permissions import CALENDAR_WRITE_PERMISSION, require_permission

from .constants import (
    MUTATION_STATUS_APPLIED,
    MUTATION_STATUS_FAILED,
    MUTATION_STATUS_NOOP,
    MUTATION_STATUS_PENDING,
    SOURCE_KIND_INTERNAL_REMINDERS,
    SOURCE_KIND_INTERNAL_SCHEDULER,
    SOURCE_KIND_PROVIDER,
)
from .models import AttendeeInfo, CalendarEvent, CalendarEventCreate


class MutationsMixin:
    """Existing Calendar mutations behavior on the owning module instance."""

    async def _propose_event(
        self,
        *,
        butler_name: str | None = None,
        title: str,
        start_at: datetime,
        end_at: datetime,
        description: str | None = None,
        location: str | None = None,
        timezone: str = "UTC",
        source_event_id: str | None = None,
        source_snippet: str | None = None,
        confidence: float | None = None,
        entity_ids: list[uuid.UUID] | None = None,
    ) -> uuid.UUID:
        """Insert a PENDING calendar_event_proposals row; return existing id on duplicate source_event_id."""  # noqa: E501
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Database pool is not available")

        resolved_butler_name = self._resolve_effective_butler_name(butler_name)
        resolved_entity_ids = entity_ids or []

        row = await pool.fetchrow(
            """
            INSERT INTO calendar_event_proposals (
                butler_name, title, start_at, end_at, description, location,
                timezone, source_event_id, source_snippet, confidence, entity_ids
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
            ON CONFLICT (source_event_id) WHERE source_event_id IS NOT NULL
            DO NOTHING
            RETURNING id
            """,
            resolved_butler_name,
            title,
            start_at,
            end_at,
            description,
            location,
            timezone,
            source_event_id,
            source_snippet,
            confidence,
            resolved_entity_ids,
        )

        if row is not None:
            return row["id"]

        # Conflict on source_event_id — return the existing proposal id.
        existing_id = await pool.fetchval(
            "SELECT id FROM calendar_event_proposals WHERE source_event_id = $1",
            source_event_id,
        )
        if existing_id is None:
            raise RuntimeError(
                f"Expected existing proposal for source_event_id={source_event_id!r}"
            )
        return existing_id

    async def _emit_calendar_audit(
        self,
        action: str,
        event_title: str | None,
        calendar_id: str | None,
        *,
        result: str = "success",
        error: str | None = None,
    ) -> None:
        """Fire-and-forget egress audit entry for a Google Calendar API write.

        Parameters
        ----------
        action:
            One of ``"create"``, ``"update"``, ``"delete"``.
        event_title:
            Human-readable event title (may be ``None`` for delete paths).
        calendar_id:
            Provider calendar ID that was mutated.
        """
        await write_audit_entry(
            self._audit_pool,
            self._butler_name,
            "google_calendar_write",
            {
                "action": action,
                "event_title": event_title,
                "calendar_id": calendar_id,
            },
            result=result,
            error=error,
        )

    async def _require_calendar_write_permission(self) -> None:
        """Enforce the public.permissions ``calendar.write`` grant for this butler.

        The Settings → Permissions matrix governs whether this butler may create,
        update, or delete calendar events. A cell flipped to granted=false blocks
        the write outright via :class:`PermissionDenied` (an authorization
        decision). Mirrors the spawn gate: consult the matrix at the decision
        point, before any provider write. Unavailable permission state refuses before
        any calendar write.
        """
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        await require_permission(
            pool, self._butler_name, CALENDAR_WRITE_PERMISSION, fail_closed=True
        )

    async def create_user_event(
        self,
        *,
        title: str,
        start_at: datetime,
        end_at: datetime,
        description: str | None = None,
    ) -> None:
        """Programmatic event creation for inter-module use (e.g. auto-creating meal events).

        Silently returns if the provider is not initialised (calendar module not ready).

        Branded as butler-authored: the title is stamped with the butler-event
        prefix via :meth:`_ensure_butler_title` and butler-generated/name private
        metadata is attached via :meth:`_build_butler_private_metadata`, matching
        the blessed MCP ``calendar_create_event`` path. The event defaults to the
        dedicated Butlers calendar via ``_resolve_calendar_id(None,
        for_create=True)``.

        Permissions-matrix enforcement (public.permissions: calendar.write): this
        path writes to the provider without going through the 3 blessed MCP
        calendar tools, so it must consult the matrix itself. Reuses the same
        :meth:`_require_calendar_write_permission` helper the MCP tools use, so a
        butler with ``calendar.write`` revoked cannot create events via this
        sibling path. Unavailable permission state refuses before calendar writes.
        """
        if self._provider is None:
            return
        await self._require_calendar_write_permission()
        calendar_id = self._resolve_calendar_id(None, for_create=True)
        payload = CalendarEventCreate(
            title=self._ensure_butler_title(title),
            start_at=start_at,
            end_at=end_at,
            description=description,
            private_metadata=self._build_butler_private_metadata(butler_name=self._butler_name),
        )
        await self._provider.create_event(calendar_id=calendar_id, payload=payload)

    @staticmethod
    def _normalize_request_id(request_id: str | None) -> str | None:
        if request_id is None:
            return None
        normalized = request_id.strip()
        return normalized or None

    @staticmethod
    def _mutation_idempotency_key(action_type: str, request_id: str | None) -> str:
        from . import CalendarModule

        normalized_request_id = CalendarModule._normalize_request_id(request_id)
        if normalized_request_id is not None:
            return f"{action_type}:request:{normalized_request_id}"
        return f"{action_type}:generated:{uuid.uuid4()}"

    async def _table_columns(self, table_name: str) -> set[str]:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return set()
        rows = await pool.fetch(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema() AND table_name = $1
            """,
            table_name,
        )
        return {str(row["column_name"]) for row in rows}

    async def _load_projection_action(
        self, idempotency_key: str
    ) -> tuple[str, dict[str, Any] | None, str | None] | None:
        if not await self._projection_tables_available():
            return None
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None
        row = await pool.fetchrow(
            """
            SELECT action_status, action_result, error
            FROM calendar_action_log
            WHERE idempotency_key = $1
            """,
            idempotency_key,
        )
        if row is None:
            return None
        status = str(row["action_status"])
        result = None
        raw_action_result = row["action_result"]
        if raw_action_result is not None:
            normalized = reconstruct_action_result(raw_action_result)
            if isinstance(raw_action_result, list):
                # Legacy corrupted row (bu-x92jw): heal it back to a proper
                # jsonb object now that we've reconstructed it, so future
                # readers (this method and the undo guard in
                # calendar_workspace.py) see a correct object again. Guarded
                # on jsonb_typeof so this stays idempotent under a racing
                # repair of the same row.
                await pool.execute(
                    """
                    UPDATE calendar_action_log
                    SET action_result = $2, updated_at = now()
                    WHERE idempotency_key = $1
                      AND jsonb_typeof(action_result) = 'array'
                    """,
                    idempotency_key,
                    normalized,
                )
            if normalized:
                result = normalized
        error = row["error"]
        return status, result, None if error is None else str(error)

    async def _prepare_workspace_mutation(
        self,
        *,
        action_type: str,
        request_id: str | None,
        action_payload: dict[str, Any],
        allow_pending_replay: bool = False,
    ) -> tuple[str, dict[str, Any] | None]:
        idempotency_key = self._mutation_idempotency_key(action_type, request_id)
        existing = await self._load_projection_action(idempotency_key)
        if existing is not None:
            status, existing_result, error = existing
            if (
                status in {MUTATION_STATUS_APPLIED, MUTATION_STATUS_NOOP}
                and existing_result is not None
            ):
                replay_result = dict(existing_result)
                replay_result["idempotent_replay"] = True
                return idempotency_key, replay_result
            if status == MUTATION_STATUS_FAILED:
                return idempotency_key, {
                    "status": "error",
                    "error": error or "Mutation failed",
                    "idempotent_replay": True,
                }
            if status == MUTATION_STATUS_PENDING and not allow_pending_replay:
                return idempotency_key, {
                    "status": "pending",
                    "message": "Mutation already queued for processing",
                    "idempotent_replay": True,
                }

        await self._record_projection_action(
            idempotency_key=idempotency_key,
            action_type=action_type,
            action_status=MUTATION_STATUS_PENDING,
            source_id=None,
            origin_ref=None,
            action_payload=action_payload,
            request_id=self._normalize_request_id(request_id),
        )
        return idempotency_key, None

    async def _finalize_workspace_mutation(
        self,
        *,
        idempotency_key: str,
        action_type: str,
        request_id: str | None,
        action_status: Literal["pending", "applied", "failed", "noop"],
        action_payload: dict[str, Any],
        action_result: dict[str, Any] | None,
        source_id: uuid.UUID | None = None,
        origin_ref: str | None = None,
        error: str | None = None,
    ) -> None:
        await self._record_projection_action(
            idempotency_key=idempotency_key,
            action_type=action_type,
            action_status=action_status,
            source_id=source_id,
            origin_ref=origin_ref,
            action_payload=action_payload,
            action_result=action_result,
            request_id=self._normalize_request_id(request_id),
            error=error,
        )

    async def _refresh_user_projection(self, calendar_id: str) -> dict[str, Any]:
        if not await self._projection_tables_available():
            return await self._projection_freshness_metadata()
        await self._sync_calendar(calendar_id)
        return await self._projection_freshness_metadata()

    async def _refresh_butler_projection(self) -> dict[str, Any]:
        await self._project_internal_sources()
        await self._push_internal_events_to_provider()
        return await self._projection_freshness_metadata()

    async def _resolve_action_source_id(
        self,
        *,
        source_kind: str,
        lane: Literal["user", "butler"],
        calendar_id: str | None = None,
    ) -> uuid.UUID | None:
        resolved_butler_name = self._resolve_effective_butler_name()
        if source_kind == SOURCE_KIND_PROVIDER:
            provider = self._require_provider()
            resolved_calendar = calendar_id or self._require_config().calendar_id
            summary = await provider.get_calendar_summary(resolved_calendar)
            display_name = summary or resolved_calendar
            return await self._ensure_calendar_source(
                source_key=f"provider:{provider.name}:{resolved_calendar}",
                source_kind=SOURCE_KIND_PROVIDER,
                lane="user",
                provider=provider.name,
                calendar_id=resolved_calendar,
                butler_name=self._butler_name,
                display_name=display_name,
                writable=True,
                metadata={"projection": "provider_sync"},
            )
        if source_kind == SOURCE_KIND_INTERNAL_SCHEDULER:
            return await self._ensure_calendar_source(
                source_key=f"internal_scheduler:{resolved_butler_name}",
                source_kind=SOURCE_KIND_INTERNAL_SCHEDULER,
                lane="butler",
                provider="internal",
                butler_name=resolved_butler_name,
                display_name=f"{resolved_butler_name} schedules",
                writable=True,
                metadata={"projection": "scheduler"},
            )
        if source_kind == SOURCE_KIND_INTERNAL_REMINDERS:
            return await self._ensure_calendar_source(
                source_key=f"internal_reminders:{resolved_butler_name}",
                source_kind=SOURCE_KIND_INTERNAL_REMINDERS,
                lane="butler",
                provider="internal",
                butler_name=resolved_butler_name,
                display_name=f"{resolved_butler_name} reminders",
                writable=True,
                metadata={"projection": "reminders"},
            )
        return None

    @staticmethod
    def _attendee_to_payload(attendee: AttendeeInfo) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "email": attendee.email,
            "display_name": attendee.display_name,
            "response_status": attendee.response_status.value,
            "optional": attendee.optional,
            "organizer": attendee.organizer,
            "self": attendee.self_,
            "comment": attendee.comment,
        }
        return payload

    @staticmethod
    def _capture_event_pre_state(
        event: CalendarEvent,
        calendar_id: str,
        *,
        entity_ids: list[uuid.UUID] | None = None,
    ) -> dict[str, Any]:
        """Capture the pre-mutation event pre-image for reversible mutations.

        Stored under the ``pre_state`` key of a mutation's ``action_result`` so the
        dashboard undo endpoint can reverse-apply an inverse mutation
        (``calendar_update_event`` to restore, or ``calendar_create_event`` to
        recreate) without an extra provider round-trip.  Reuses the
        already-fetched ``existing_event`` — no additional provider call. Local
        entity links may be supplied from the projection because providers do not
        return them in their event payloads.

        Only the fields an inverse mutation needs are captured; attendees are
        flattened to their email addresses (the shape both inverse tools accept).
        """
        return {
            "event_id": event.event_id,
            "calendar_id": calendar_id,
            "title": event.title,
            "start_at": event.start_at.isoformat(),
            "end_at": event.end_at.isoformat(),
            "timezone": event.timezone,
            "all_day": event.all_day,
            "description": event.description,
            "body": event.body,
            "location": event.location,
            "attendees": [a.email for a in event.attendees],
            "recurrence_rule": event.recurrence_rule,
            "color_id": event.color_id,
            "entity_ids": [
                str(entity_id)
                for entity_id in (event.entity_ids if entity_ids is None else entity_ids)
            ],
        }

    @staticmethod
    def _event_to_payload(event: CalendarEvent) -> dict[str, Any]:
        from . import CalendarModule

        return {
            "event_id": event.event_id,
            "title": event.title,
            "start_at": event.start_at.isoformat(),
            "end_at": event.end_at.isoformat(),
            "timezone": event.timezone,
            "all_day": event.all_day,
            "description": event.description,
            "body": event.body,
            "location": event.location,
            "attendees": [CalendarModule._attendee_to_payload(a) for a in event.attendees],
            "recurrence_rule": event.recurrence_rule,
            "color_id": event.color_id,
            "butler_generated": event.butler_generated,
            "butler_name": event.butler_name,
            "source_butler": event.source_butler,
            "source_session_id": event.source_session_id,
            "entity_ids": [str(e) for e in event.entity_ids],
            "status": event.status.value if event.status is not None else None,
            "organizer": event.organizer,
            "visibility": event.visibility.value if event.visibility is not None else None,
            "etag": event.etag,
            "created_at": event.created_at.isoformat() if event.created_at is not None else None,
            "updated_at": event.updated_at.isoformat() if event.updated_at is not None else None,
        }
