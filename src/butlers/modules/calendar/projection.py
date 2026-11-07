"""Calendar projection implementation."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from datetime import UTC, date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

from .constants import (
    _INVALID_PROBE_CALENDAR_IDS,
    _OBSOLETE_INTERNAL_SOURCE_KEYS,
    SOURCE_KIND_INTERNAL_REMINDERS,
    SOURCE_KIND_INTERNAL_SCHEDULER,
    SOURCE_KIND_PROVIDER,
    logger,
)
from .models import CalendarEvent, EventStatus, EventVisibility, _normalize_optional_string


class ProjectionMixin:
    """Existing Calendar projection behavior on the owning module instance."""

    @staticmethod
    def _normalize_json_object(value: Any) -> dict[str, Any]:
        """Normalize a DB JSON/JSONB value into a dict for deterministic access."""
        if isinstance(value, Mapping):
            return dict(value)
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
            except json.JSONDecodeError:
                return {}
            if isinstance(parsed, Mapping):
                return dict(parsed)
        return {}

    @classmethod
    def _jsonify_for_storage(cls, value: Any) -> Any:
        """Convert nested values into JSON-serializable primitives."""
        if isinstance(value, Mapping):
            return {str(key): cls._jsonify_for_storage(item) for key, item in value.items()}
        if isinstance(value, list | tuple | set):
            return [cls._jsonify_for_storage(item) for item in value]
        if isinstance(value, datetime | date):
            return value.isoformat()
        if isinstance(value, StrEnum):
            return value.value
        if isinstance(value, uuid.UUID):
            return str(value)
        return value

    @classmethod
    def _encode_jsonb(cls, value: Any) -> Any:
        """Return a JSON-safe Python value for asyncpg JSONB binding.

        Normalises the value via ``_jsonify_for_storage`` and returns a plain
        Python dict/list/scalar.  The asyncpg JSONB codec registered in
        ``db.py`` handles encoding automatically — no ``::jsonb`` cast needed.
        """
        return cls._jsonify_for_storage(value)

    @staticmethod
    def _coerce_datetime(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
        if isinstance(value, str):
            normalized = value.strip()
            if not normalized:
                return None
            if normalized.endswith("Z"):
                normalized = f"{normalized[:-1]}+00:00"
            try:
                parsed = datetime.fromisoformat(normalized)
            except ValueError:
                return None
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        return None

    async def _projection_tables_available(self) -> bool:
        """Return whether unified calendar projection tables are present."""
        if self._projection_tables_available_cache is not None:
            return self._projection_tables_available_cache

        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            self._projection_tables_available_cache = False
            return False

        try:
            row = await pool.fetchrow(
                """
                SELECT
                    to_regclass('calendar_sources') IS NOT NULL AS has_sources,
                    to_regclass('calendar_events') IS NOT NULL AS has_events,
                    to_regclass('calendar_event_instances') IS NOT NULL AS has_instances,
                    to_regclass('calendar_sync_cursors') IS NOT NULL AS has_cursors,
                    to_regclass('calendar_action_log') IS NOT NULL AS has_action_log,
                    EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'calendar_events'
                          AND column_name = 'body'
                    ) AS has_events_body,
                    EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'calendar_events'
                          AND column_name = 'source_butler'
                    ) AS has_events_source_butler,
                    EXISTS (
                        SELECT 1
                        FROM information_schema.columns
                        WHERE table_schema = current_schema()
                          AND table_name = 'calendar_events'
                          AND column_name = 'source_session_id'
                    ) AS has_events_source_session_id
                """
            )
        except Exception as exc:
            logger.debug("Projection table availability check failed: %s", exc, exc_info=True)
            self._projection_tables_available_cache = False
            return False

        if row is None:
            self._projection_tables_available_cache = False
            return False

        try:
            flag_map = {
                "calendar_sources": row["has_sources"],
                "calendar_events": row["has_events"],
                "calendar_event_instances": row["has_instances"],
                "calendar_sync_cursors": row["has_cursors"],
                "calendar_action_log": row["has_action_log"],
                "calendar_events.body": row["has_events_body"],
                "calendar_events.source_butler": row["has_events_source_butler"],
                "calendar_events.source_session_id": row["has_events_source_session_id"],
            }
        except KeyError:
            self._projection_tables_available_cache = False
            return False

        # Treat partially migrated schemas as unavailable so background pollers
        # fail closed instead of repeatedly hitting column-mismatch errors.
        self._projection_tables_available_cache = all(flag is True for flag in flag_map.values())
        if not self._projection_tables_available_cache:
            missing = sorted(name for name, flag in flag_map.items() if flag is not True)
            logger.debug(
                "Calendar projection schema incomplete; disabling projection paths until "
                "migrations catch up: %s",
                ", ".join(missing),
            )
        return self._projection_tables_available_cache

    async def _table_exists(self, table_name: str) -> bool:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return False
        exists = await pool.fetchval("SELECT to_regclass($1) IS NOT NULL", table_name)
        return bool(exists)

    async def _source_sync_enabled(self, source_key: str) -> bool:
        """Return whether the calendar source is enabled as a sync target.

        Reads the per-source ``metadata.sync_enabled`` flag toggled via
        ``POST /api/calendar/sources``. Defaults to ``True`` (enabled) when the
        source row is absent, the projection tables are unavailable, or the flag
        was never set — so the toggle is purely opt-out and never blocks sync by
        omission. Fails open to enabled on any query error.
        """
        if not await self._projection_tables_available():
            return True
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return True
        try:
            row = await pool.fetchrow(
                "SELECT metadata FROM calendar_sources WHERE source_key = $1",
                source_key,
            )
        except Exception as exc:
            logger.debug("Failed to read sync_enabled for '%s': %s", source_key, exc)
            return True
        if row is None:
            return True
        metadata = self._normalize_json_object(row["metadata"])
        return metadata.get("sync_enabled") is not False

    async def _purge_invalid_probe_sources(self) -> None:
        """Delete residual probe/sentinel calendar sources (one-time, idempotent).

        A credential/health probe that historically reached the source-registration
        path persisted a bogus ``provider:<name>:__invalid_check__`` row that
        permanently pollutes the source-freshness ledger.  This removes any such
        row (cascading to its events/instances/cursors); the guard in
        :meth:`_ensure_calendar_source` prevents recreation, so after the first
        clean boot this is a no-op.  Fail-open: errors are logged, never raised.
        """
        if not _INVALID_PROBE_CALENDAR_IDS:
            return
        if not await self._projection_tables_available():
            return
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return
        try:
            result = await pool.execute(
                "DELETE FROM calendar_sources WHERE calendar_id = ANY($1::text[])",
                list(_INVALID_PROBE_CALENDAR_IDS),
            )
            if isinstance(result, str) and not result.endswith(" 0"):
                logger.info("Purged residual probe calendar sources: %s", result)
        except Exception as exc:
            logger.debug("Failed to purge probe calendar sources: %s", exc)

    async def _purge_obsolete_internal_sources(self) -> None:
        """Delete only the known obsolete internal source keys, idempotently."""
        if not await self._projection_tables_available():
            return
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return
        try:
            result = await pool.execute(
                "DELETE FROM calendar_sources WHERE source_key = ANY($1::text[])",
                list(_OBSOLETE_INTERNAL_SOURCE_KEYS),
            )
            if isinstance(result, str) and not result.endswith(" 0"):
                logger.info("Purged obsolete internal calendar sources: %s", result)
        except Exception as exc:
            logger.debug("Failed to purge obsolete internal calendar sources: %s", exc)

    @staticmethod
    def _is_registered_roster_butler(butler_name: str | None) -> bool:
        """Return whether an internal source owner names a real roster butler."""
        normalized = _normalize_optional_string(butler_name)
        if normalized is None or normalized in {".", ".."}:
            return False
        if "/" in normalized or "\\" in normalized:
            return False
        roster_root = Path(__file__).resolve().parents[4] / "roster"
        return (roster_root / normalized / "butler.toml").is_file()

    async def _ensure_calendar_source(
        self,
        *,
        source_key: str,
        source_kind: str,
        lane: Literal["user", "butler"],
        provider: str | None = None,
        calendar_id: str | None = None,
        butler_name: str | None = None,
        display_name: str | None = None,
        writable: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> uuid.UUID | None:
        # Never register a source for a probe/sentinel calendar id — a
        # credential/health check that reaches this path must not leave a bogus
        # ``provider:<name>:__invalid_check__`` row polluting source freshness.
        if calendar_id is not None and calendar_id in _INVALID_PROBE_CALENDAR_IDS:
            logger.debug("Refusing to register probe calendar source '%s'", source_key)
            return None
        if source_kind in {SOURCE_KIND_INTERNAL_SCHEDULER, SOURCE_KIND_INTERNAL_REMINDERS} and not (
            self._is_registered_roster_butler(butler_name)
        ):
            logger.warning(
                "Refusing to register internal calendar source '%s' for invalid roster butler '%s'",
                source_key,
                butler_name,
            )
            return None
        if not await self._projection_tables_available():
            return None
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None

        metadata_json = self._encode_jsonb(metadata or {})
        row = await pool.fetchrow(
            """
            INSERT INTO calendar_sources (
                source_key, source_kind, lane, provider, calendar_id, butler_name,
                display_name, writable, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (source_key) DO UPDATE SET
                source_kind = EXCLUDED.source_kind,
                lane = EXCLUDED.lane,
                provider = EXCLUDED.provider,
                calendar_id = EXCLUDED.calendar_id,
                butler_name = EXCLUDED.butler_name,
                display_name = EXCLUDED.display_name,
                writable = EXCLUDED.writable,
                -- Shallow-merge so operator-set keys (e.g. ``sync_enabled``
                -- toggled via POST /api/calendar/sources) survive the next
                -- discovery/sync upsert instead of being clobbered.
                metadata = COALESCE(calendar_sources.metadata, '{}'::jsonb)
                    || EXCLUDED.metadata,
                updated_at = now()
            RETURNING id
            """,
            source_key,
            source_kind,
            lane,
            provider,
            calendar_id,
            butler_name,
            display_name,
            writable,
            metadata_json,
        )
        return row["id"] if row is not None else None

    async def _load_projection_cursor(
        self,
        *,
        source_id: uuid.UUID,
        cursor_name: str,
    ) -> dict[str, Any] | None:
        if not await self._projection_tables_available():
            return None
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None

        row = await pool.fetchrow(
            """
            SELECT sync_token, checkpoint, full_sync_required,
                   last_synced_at, last_success_at, last_error_at, last_error
            FROM calendar_sync_cursors
            WHERE source_id = $1 AND cursor_name = $2
            """,
            source_id,
            cursor_name,
        )
        if row is None:
            return None

        return {
            "sync_token": row["sync_token"],
            "checkpoint": self._normalize_json_object(row["checkpoint"]),
            "full_sync_required": bool(row["full_sync_required"]),
            "last_synced_at": row["last_synced_at"],
            "last_success_at": row["last_success_at"],
            "last_error_at": row["last_error_at"],
            "last_error": row["last_error"],
        }

    async def _upsert_projection_cursor(
        self,
        *,
        source_id: uuid.UUID | None,
        cursor_name: str,
        sync_token: str | None,
        checkpoint: dict[str, Any],
        full_sync_required: bool,
        last_synced_at: datetime | None,
        last_success_at: datetime | None,
        last_error_at: datetime | None,
        last_error: str | None,
    ) -> None:
        if source_id is None or not await self._projection_tables_available():
            return
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return

        checkpoint_json = self._encode_jsonb(checkpoint)
        await pool.execute(
            """
            INSERT INTO calendar_sync_cursors (
                source_id, cursor_name, sync_token, checkpoint, full_sync_required,
                last_synced_at, last_success_at, last_error_at, last_error
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
            ON CONFLICT (source_id, cursor_name) DO UPDATE SET
                sync_token = EXCLUDED.sync_token,
                checkpoint = EXCLUDED.checkpoint,
                full_sync_required = EXCLUDED.full_sync_required,
                last_synced_at = EXCLUDED.last_synced_at,
                last_success_at = EXCLUDED.last_success_at,
                last_error_at = EXCLUDED.last_error_at,
                last_error = EXCLUDED.last_error,
                updated_at = now()
            """,
            source_id,
            cursor_name,
            sync_token,
            checkpoint_json,
            full_sync_required,
            last_synced_at,
            last_success_at,
            last_error_at,
            last_error,
        )

    async def _record_projection_action(
        self,
        *,
        idempotency_key: str,
        action_type: str,
        action_status: Literal["pending", "running", "applied", "failed", "noop"],
        source_id: uuid.UUID | None,
        origin_ref: str | None,
        action_payload: dict[str, Any],
        action_result: dict[str, Any] | None = None,
        request_id: str | None = None,
        event_id: uuid.UUID | None = None,
        instance_id: uuid.UUID | None = None,
        error: str | None = None,
    ) -> None:
        if not await self._projection_tables_available():
            return
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return

        payload_json = self._encode_jsonb(action_payload)
        result_json = None if action_result is None else self._encode_jsonb(action_result)
        await pool.execute(
            """
            INSERT INTO calendar_action_log (
                idempotency_key, request_id, action_type, action_status,
                source_id, event_id, instance_id, origin_ref,
                action_payload, action_result, error, applied_at
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
            ON CONFLICT (idempotency_key) DO UPDATE SET
                action_status = EXCLUDED.action_status,
                source_id = EXCLUDED.source_id,
                event_id = EXCLUDED.event_id,
                instance_id = EXCLUDED.instance_id,
                origin_ref = EXCLUDED.origin_ref,
                action_payload = EXCLUDED.action_payload,
                action_result = EXCLUDED.action_result,
                error = EXCLUDED.error,
                applied_at = EXCLUDED.applied_at,
                updated_at = now()
            """,
            idempotency_key,
            request_id,
            action_type,
            action_status,
            source_id,
            event_id,
            instance_id,
            origin_ref,
            payload_json,
            result_json,
            error,
            datetime.now(UTC) if action_status in {"applied", "failed", "noop"} else None,
        )

    async def _upsert_projection_event(
        self,
        *,
        source_id: uuid.UUID,
        origin_ref: str,
        title: str,
        timezone: str,
        starts_at: datetime,
        ends_at: datetime,
        status: str,
        all_day: bool = False,
        visibility: str = "default",
        recurrence_rule: str | None = None,
        description: str | None = None,
        body: str | None = None,
        location: str | None = None,
        etag: str | None = None,
        origin_updated_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        source_butler: str | None = None,
        source_session_id: str | None = None,
    ) -> uuid.UUID:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Projection writes require a database pool")

        effective_source_butler = self._resolve_effective_butler_name(source_butler)
        normalized_source_session_id = _normalize_optional_string(source_session_id)
        metadata_json = self._encode_jsonb(metadata or {})
        row = await pool.fetchrow(
            """
            INSERT INTO calendar_events (
                source_id, origin_ref, title, description, body, location, timezone,
                starts_at, ends_at, all_day, status, visibility, recurrence_rule,
                etag, origin_updated_at, metadata, source_butler, source_session_id
            )
            VALUES (
                $1, $2, $3, $4, $5, $6, $7,
                $8, $9, $10, $11, $12, $13,
                $14, $15, $16, $17, $18
            )
            ON CONFLICT (source_id, origin_ref) DO UPDATE SET
                title = EXCLUDED.title,
                description = EXCLUDED.description,
                body = EXCLUDED.body,
                location = EXCLUDED.location,
                timezone = EXCLUDED.timezone,
                starts_at = EXCLUDED.starts_at,
                ends_at = EXCLUDED.ends_at,
                all_day = EXCLUDED.all_day,
                status = EXCLUDED.status,
                visibility = EXCLUDED.visibility,
                recurrence_rule = EXCLUDED.recurrence_rule,
                etag = EXCLUDED.etag,
                origin_updated_at = EXCLUDED.origin_updated_at,
                metadata = EXCLUDED.metadata,
                source_butler = EXCLUDED.source_butler,
                source_session_id = EXCLUDED.source_session_id,
                updated_at = now()
            RETURNING id
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
            all_day,
            status,
            visibility,
            recurrence_rule,
            etag,
            origin_updated_at,
            metadata_json,
            effective_source_butler,
            normalized_source_session_id,
        )
        if row is None:
            raise RuntimeError("Projection upsert did not return calendar_events.id")
        return row["id"]

    async def _upsert_event_entities(
        self,
        *,
        event_id: uuid.UUID,
        entity_ids: list[uuid.UUID],
        clear_entity_ids: bool = False,
    ) -> None:
        """Replace entity associations for a calendar event.

        Deletes all existing links for the event and inserts the new set. An
        empty list is a no-op unless ``clear_entity_ids`` explicitly authorizes
        removal of every existing association.
        """
        if clear_entity_ids and entity_ids:
            raise ValueError("clear_entity_ids requires entity_ids=[]")
        if not entity_ids and not clear_entity_ids:
            return
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM calendar_event_entities WHERE event_id = $1",
                    event_id,
                )
                if entity_ids:
                    await conn.executemany(
                        """
                        INSERT INTO calendar_event_entities (event_id, entity_id)
                        VALUES ($1, $2)
                        ON CONFLICT DO NOTHING
                        """,
                        [(event_id, eid) for eid in entity_ids],
                    )

    async def _fetch_event_entity_ids(
        self,
        *,
        event_id: uuid.UUID,
    ) -> list[uuid.UUID]:
        """Return entity UUIDs linked to a calendar event from the junction table."""
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return []
        rows = await pool.fetch(
            "SELECT entity_id FROM calendar_event_entities WHERE event_id = $1",
            event_id,
        )
        return [row["entity_id"] for row in rows]

    async def _fetch_pre_state_entity_ids(
        self,
        *,
        source_id: uuid.UUID | None,
        origin_ref: str,
        fallback: list[uuid.UUID],
    ) -> list[uuid.UUID]:
        """Load local links for an undo pre-image, with a provider-data fallback.

        Provider event payloads do not carry the local
        ``calendar_event_entities`` associations.  The projection is therefore
        authoritative when available; an unavailable or not-yet-projected row
        falls back to the provider payload so mutations remain fail-open.
        """
        if source_id is None:
            return list(fallback)
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return list(fallback)
        try:
            row = await pool.fetchrow(
                "SELECT id FROM calendar_events WHERE source_id = $1 AND origin_ref = $2",
                source_id,
                origin_ref,
            )
            if row is None:
                return list(fallback)
            return await self._fetch_event_entity_ids(event_id=row["id"])
        except Exception as exc:
            logger.warning(
                "Failed to load projected entity links for calendar undo pre-state "
                "(origin_ref=%s): %s",
                origin_ref,
                exc,
            )
            return list(fallback)

    async def _update_butler_event_entities(
        self,
        *,
        source_id: uuid.UUID,
        origin_ref: str,
        entity_ids: list[uuid.UUID],
    ) -> None:
        """Update entity associations for a butler event's projection row.

        Looks up the calendar_events row by (source_id, origin_ref) and replaces
        its entity links.  Fail-open: errors are logged but do not propagate.
        """
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return
        try:
            row = await pool.fetchrow(
                "SELECT id FROM calendar_events WHERE source_id = $1 AND origin_ref = $2",
                source_id,
                origin_ref,
            )
            if row is None:
                return
            await self._upsert_event_entities(event_id=row["id"], entity_ids=entity_ids)
        except Exception as exc:
            logger.warning(
                "_update_butler_event_entities failed (source_id=%s, origin_ref=%s): %s",
                source_id,
                origin_ref,
                exc,
            )

    async def _upsert_projection_instance(
        self,
        *,
        event_id: uuid.UUID,
        source_id: uuid.UUID,
        origin_instance_ref: str,
        timezone: str,
        starts_at: datetime,
        ends_at: datetime,
        status: str,
        is_exception: bool = False,
        origin_updated_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            raise RuntimeError("Projection writes require a database pool")

        metadata_json = self._encode_jsonb(metadata or {})
        row = await pool.fetchrow(
            """
            INSERT INTO calendar_event_instances (
                event_id, source_id, origin_instance_ref, timezone, starts_at, ends_at,
                status, is_exception, origin_updated_at, metadata
            )
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (event_id, origin_instance_ref) DO UPDATE SET
                timezone = EXCLUDED.timezone,
                starts_at = EXCLUDED.starts_at,
                ends_at = EXCLUDED.ends_at,
                status = EXCLUDED.status,
                is_exception = EXCLUDED.is_exception,
                origin_updated_at = EXCLUDED.origin_updated_at,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            RETURNING id
            """,
            event_id,
            source_id,
            origin_instance_ref,
            timezone,
            starts_at,
            ends_at,
            status,
            is_exception,
            origin_updated_at,
            metadata_json,
        )
        if row is None:
            raise RuntimeError("Projection upsert did not return calendar_event_instances.id")
        return row["id"]

    async def _mark_projection_event_cancelled(
        self,
        *,
        source_id: uuid.UUID,
        origin_ref: str,
        origin_updated_at: datetime | None = None,
    ) -> uuid.UUID | None:
        if not await self._projection_tables_available():
            return None
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None

        row = await pool.fetchrow(
            """
            UPDATE calendar_events
            SET status = 'cancelled',
                origin_updated_at = COALESCE($3, origin_updated_at),
                updated_at = now()
            WHERE source_id = $1 AND origin_ref = $2
            RETURNING id
            """,
            source_id,
            origin_ref,
            origin_updated_at,
        )
        if row is None:
            return None

        event_id: uuid.UUID = row["id"]
        await pool.execute(
            """
            UPDATE calendar_event_instances
            SET status = 'cancelled',
                updated_at = now()
            WHERE event_id = $1
            """,
            event_id,
        )
        return event_id

    async def _mark_projection_source_stale_events_cancelled(
        self,
        *,
        source_id: uuid.UUID,
        seen_origin_refs: list[str],
    ) -> None:
        if not await self._projection_tables_available():
            return
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return

        if seen_origin_refs:
            await pool.execute(
                """
                UPDATE calendar_events
                SET status = 'cancelled',
                    updated_at = now()
                WHERE source_id = $1
                  AND NOT (origin_ref = ANY($2::text[]))
                  AND NOT (metadata->>'native' = 'true')
                """,
                source_id,
                seen_origin_refs,
            )
        else:
            await pool.execute(
                """
                UPDATE calendar_events
                SET status = 'cancelled',
                    updated_at = now()
                WHERE source_id = $1
                  AND NOT (metadata->>'native' = 'true')
                """,
                source_id,
            )

        await pool.execute(
            """
            UPDATE calendar_event_instances AS i
            SET status = 'cancelled',
                updated_at = now()
            FROM calendar_events AS e
            WHERE i.event_id = e.id
              AND e.source_id = $1
              AND e.status = 'cancelled'
            """,
            source_id,
        )

    async def _project_provider_mutation(
        self,
        *,
        source_id: uuid.UUID | None,
        calendar_id: str,
        updated_events: list[CalendarEvent] | None = None,
        cancelled_ids: list[str] | None = None,
        clear_entity_ids: bool = False,
    ) -> None:
        """Write a provider mutation directly to projection tables.

        Bypasses the sync-from-Google round-trip to avoid Google indexing
        latency race conditions.  The next sync cycle reconciles any
        discrepancies.  ``clear_entity_ids`` is reserved for an explicit
        workspace update that intentionally replaces an event's people set
        with zero links; ordinary provider syncs keep their preserve-on-empty
        behavior. Fail-open: errors are logged but do not propagate.
        """
        if source_id is None:
            return
        provider = self._provider
        if provider is None:
            return
        try:
            await self._project_provider_changes(
                source_id=source_id,
                provider_name=provider.name,
                calendar_id=calendar_id,
                updated_events=updated_events or [],
                cancelled_ids=cancelled_ids or [],
                clear_entity_ids=clear_entity_ids,
            )
        except Exception as exc:
            logger.warning(
                "Direct projection of provider mutation failed (calendar_id=%s): %s",
                calendar_id,
                exc,
            )

    async def _project_provider_changes(
        self,
        *,
        source_id: uuid.UUID,
        provider_name: str,
        calendar_id: str,
        updated_events: list[CalendarEvent],
        cancelled_ids: list[str],
        clear_entity_ids: bool = False,
    ) -> None:
        # NOTE: butler-generated events pulled back from Google come from two
        # places: workspace events created via calendar_create_event, and
        # internal scheduled tasks / reminders that _push_internal_events_to_provider
        # mirrors onto the Butlers calendar. Both are persisted here regardless
        # of the butler_generated flag, and the metadata records butler_generated
        # and butler_name for UI differentiation. For pushed internal items the
        # local rows stay authoritative: the next push overwrites any edit made
        # on Google, and a later sync converges the projection.

        for event in updated_events:
            status_value = (
                event.status.value if event.status is not None else EventStatus.confirmed.value
            )
            visibility_value = (
                event.visibility.value
                if event.visibility is not None
                else EventVisibility.default.value
            )
            metadata = {
                "source_type": SOURCE_KIND_PROVIDER,
                "provider": provider_name,
                "calendar_id": calendar_id,
                "butler_generated": event.butler_generated,
                "butler_name": event.butler_name,
                "organizer": event.organizer,
                "transparency": event.transparency,
                "attendees": [self._attendee_to_payload(attendee) for attendee in event.attendees],
                # Projection-only companion: existing attendee/tool payloads stay exact.
                "attendee_status_provenance": [
                    {
                        "email": attendee.email,
                        "response_status": attendee.response_status.value,
                        "response_status_explicit": attendee._response_status_explicit,
                    }
                    for attendee in event.attendees
                ],
                "created_at": event.created_at.isoformat() if event.created_at else None,
                "updated_at": event.updated_at.isoformat() if event.updated_at else None,
            }
            event_db_id = await self._upsert_projection_event(
                source_id=source_id,
                origin_ref=event.event_id,
                title=event.title,
                description=event.description,
                body=event.body,
                location=event.location,
                timezone=event.timezone,
                starts_at=event.start_at,
                ends_at=event.end_at,
                all_day=event.all_day,
                status=status_value,
                visibility=visibility_value,
                recurrence_rule=event.recurrence_rule,
                etag=event.etag,
                origin_updated_at=event.updated_at,
                metadata=metadata,
                source_butler=event.source_butler or event.butler_name or self._butler_name,
                source_session_id=event.source_session_id,
            )
            # Sync cycles preserve links on empty entity sets. A workspace update
            # must opt in with the explicit clear signal before a zero-length set
            # can delete the existing links.
            if event.entity_ids or clear_entity_ids:
                await self._upsert_event_entities(
                    event_id=event_db_id,
                    entity_ids=event.entity_ids,
                    clear_entity_ids=clear_entity_ids,
                )
            origin_instance_ref = f"{event.event_id}:{event.start_at.isoformat()}"
            await self._upsert_projection_instance(
                event_id=event_db_id,
                source_id=source_id,
                origin_instance_ref=origin_instance_ref,
                timezone=event.timezone,
                starts_at=event.start_at,
                ends_at=event.end_at,
                status=status_value,
                is_exception=False,
                origin_updated_at=event.updated_at,
                metadata={"source_type": SOURCE_KIND_PROVIDER, "provider": provider_name},
            )
            # A provider event projects to exactly one instance per sync; drop any
            # older instance of this event left behind by a time-drifted re-sync
            # (different origin_instance_ref) so the ledger converges to one row
            # instead of rendering the same event twice (bu-ssp91).
            await self._prune_superseded_provider_instances(
                event_id=event_db_id,
                keep_origin_instance_ref=origin_instance_ref,
            )

        cancelled_at = datetime.now(UTC)
        for cancelled_id in cancelled_ids:
            await self._mark_projection_event_cancelled(
                source_id=source_id,
                origin_ref=cancelled_id,
                origin_updated_at=cancelled_at,
            )

    async def _prune_superseded_provider_instances(
        self,
        *,
        event_id: uuid.UUID,
        keep_origin_instance_ref: str,
    ) -> None:
        """Delete stale provider instances of *event_id* other than the one just synced.

        A provider (``lane='user'``) event projects to exactly one instance per
        sync via :meth:`_project_provider_changes`, whose ``origin_instance_ref``
        embeds the event start (``f"{event_id}:{start.isoformat()}"``).  When a
        re-sync drifts that start, the ``ON CONFLICT (event_id,
        origin_instance_ref)`` upsert INSERTs a NEW instance while the prior,
        time-drifted instance lingers — so the same event renders twice with two
        different windows.  Deleting every other instance for this event converges
        the ledger to the single occurrence the provider currently reports.
        Fail-open: errors are logged but never propagate into the sync loop.
        """
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return
        try:
            await pool.execute(
                """
                DELETE FROM calendar_event_instances
                WHERE event_id = $1
                  AND origin_instance_ref IS DISTINCT FROM $2
                """,
                event_id,
                keep_origin_instance_ref,
            )
        except Exception as exc:
            logger.debug(
                "Failed to prune superseded provider instances (event_id=%s): %s",
                event_id,
                exc,
            )

    async def _prune_recurring_instances_outside_window(
        self,
        *,
        event_id: uuid.UUID,
        window_start: datetime,
        window_end: datetime,
        retain_overdue_unnotified: bool = False,
    ) -> None:
        """Delete calendar_event_instances for *event_id* that fall outside the window.

        This prevents unbounded row growth as the rolling projection window advances.
        Reminder delivery callers can retain overdue confirmed instances until their
        per-occurrence ``notified_at`` marker is written; scheduler projections keep
        the original strict rolling-window pruning behavior.
        """
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return
        await pool.execute(
            """
            DELETE FROM calendar_event_instances
            WHERE event_id = $1
              AND (
                  starts_at > $3
                  OR (
                      starts_at < $2
                      AND (
                          NOT $4::boolean
                          OR status <> 'confirmed'
                          OR metadata->>'notified_at' IS NOT NULL
                      )
                  )
              )
            """,
            event_id,
            window_start,
            window_end,
            retain_overdue_unnotified,
        )
