"""Calendar sync queue implementation."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Mapping
from typing import Any, Literal

from .constants import (
    FORCE_SYNC_ACTION_TYPE,
    MUTATION_STATUS_APPLIED,
    MUTATION_STATUS_FAILED,
    MUTATION_STATUS_NOOP,
    MUTATION_STATUS_PENDING,
    MUTATION_STATUS_RUNNING,
    logger,
)


class SyncQueueMixin:
    """Existing Calendar sync queue behavior on the owning module instance."""

    @staticmethod
    def _force_sync_action_payload(
        *,
        calendar_id: str | None,
        full: bool,
    ) -> dict[str, Any]:
        """Build the durable queue payload for one force-sync request.

        ``calendar_ids=None`` deliberately means pull every registered provider
        calendar. A queue row can merge multiple targeted calendar ids while a
        global request subsumes every targeted request already waiting.
        """
        normalized_calendar_id = calendar_id.strip() if isinstance(calendar_id, str) else None
        return {
            "calendar_ids": [normalized_calendar_id] if normalized_calendar_id else None,
            "full": bool(full),
        }

    @staticmethod
    def _force_sync_calendar_ids(payload: Mapping[str, Any]) -> list[str] | None:
        """Normalize legacy single-calendar queue payloads to the current list shape."""
        raw_ids = payload.get("calendar_ids")
        if raw_ids is None:
            legacy_id = payload.get("calendar_id")
            if isinstance(legacy_id, str) and legacy_id.strip():
                return [legacy_id.strip()]
            return None
        if not isinstance(raw_ids, list):
            return None
        ids = [value.strip() for value in raw_ids if isinstance(value, str) and value.strip()]
        return list(dict.fromkeys(ids)) or None

    @classmethod
    def _merge_force_sync_payload(
        cls,
        existing: Mapping[str, Any],
        incoming: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Merge compatible pending force-sync work without dropping a target."""
        existing_ids = cls._force_sync_calendar_ids(existing)
        incoming_ids = cls._force_sync_calendar_ids(incoming)
        if existing_ids is None or incoming_ids is None:
            merged_ids: list[str] | None = None
        else:
            merged_ids = list(dict.fromkeys([*existing_ids, *incoming_ids]))
        return {
            "calendar_ids": merged_ids,
            "full": bool(existing.get("full")) or bool(incoming.get("full")),
        }

    @classmethod
    def _force_sync_payload_covers(
        cls,
        existing: Mapping[str, Any],
        incoming: Mapping[str, Any],
    ) -> bool:
        """Return whether an active command already satisfies a new request."""
        if bool(incoming.get("full")) and not bool(existing.get("full")):
            return False
        existing_ids = cls._force_sync_calendar_ids(existing)
        incoming_ids = cls._force_sync_calendar_ids(incoming)
        if existing_ids is None:
            return True
        if incoming_ids is None:
            return False
        return set(incoming_ids).issubset(existing_ids)

    @classmethod
    def _force_sync_command_from_row(cls, row: Mapping[str, Any]) -> dict[str, Any]:
        """Normalize a calendar-action record for queue ownership decisions."""
        request_id = row.get("request_id")
        return {
            "idempotency_key": str(row["idempotency_key"]),
            "request_id": None if request_id is None else str(request_id),
            "action_status": str(row["action_status"]),
            "action_payload": cls._normalize_json_object(row.get("action_payload")),
        }

    async def _load_force_sync_command(
        self,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        if not await self._projection_tables_available():
            return None
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None
        row = await pool.fetchrow(
            """
            SELECT idempotency_key, request_id, action_status, action_payload
            FROM calendar_action_log
            WHERE idempotency_key = $1
            """,
            idempotency_key,
        )
        return None if row is None else self._force_sync_command_from_row(row)

    async def _load_active_force_sync_command(self) -> dict[str, Any] | None:
        """Return this schema's pending command, or its sole running command."""
        if not await self._projection_tables_available():
            return None
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None
        row = await pool.fetchrow(
            """
            SELECT idempotency_key, request_id, action_status, action_payload
            FROM calendar_action_log
            WHERE action_type = $1
              AND action_status IN ($2, $3)
            ORDER BY
                CASE action_status WHEN 'pending' THEN 0 ELSE 1 END,
                created_at ASC
            LIMIT 1
            """,
            FORCE_SYNC_ACTION_TYPE,
            MUTATION_STATUS_PENDING,
            MUTATION_STATUS_RUNNING,
        )
        return None if row is None else self._force_sync_command_from_row(row)

    async def _insert_pending_force_sync_command(
        self,
        *,
        idempotency_key: str,
        request_id: str,
        action_payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Insert a queue row, losing safely to another writer when necessary."""
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None
        row = await pool.fetchrow(
            """
            INSERT INTO calendar_action_log (
                idempotency_key, request_id, action_type, action_status, action_payload
            )
            VALUES ($1, $2, $3, $4, $5)
            ON CONFLICT DO NOTHING
            RETURNING idempotency_key, request_id, action_status, action_payload
            """,
            idempotency_key,
            request_id,
            FORCE_SYNC_ACTION_TYPE,
            MUTATION_STATUS_PENDING,
            self._encode_jsonb(dict(action_payload)),
        )
        return None if row is None else self._force_sync_command_from_row(row)

    async def _merge_pending_force_sync_command(
        self,
        *,
        command: Mapping[str, Any],
        action_payload: Mapping[str, Any],
    ) -> dict[str, Any] | None:
        """Atomically extend a pending command while retaining its correlation id."""
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None
        merged_payload = self._merge_force_sync_payload(
            self._normalize_json_object(command.get("action_payload")),
            action_payload,
        )
        row = await pool.fetchrow(
            """
            UPDATE calendar_action_log
            SET action_payload = $2, updated_at = now()
            WHERE idempotency_key = $1
              AND action_type = $3
              AND action_status = $4
            RETURNING idempotency_key, request_id, action_status, action_payload
            """,
            str(command["idempotency_key"]),
            self._encode_jsonb(merged_payload),
            FORCE_SYNC_ACTION_TYPE,
            MUTATION_STATUS_PENDING,
        )
        return None if row is None else self._force_sync_command_from_row(row)

    @staticmethod
    def _queued_force_sync_result(
        command: Mapping[str, Any],
        *,
        coalesced: bool,
    ) -> dict[str, Any]:
        from . import CalendarModule

        payload = CalendarModule._normalize_json_object(command.get("action_payload"))
        return {
            "status": "queued",
            "request_id": command.get("request_id"),
            "full": bool(payload.get("full")),
            "coalesced": coalesced,
        }

    async def _enqueue_force_sync_command(
        self,
        *,
        calendar_id: str | None,
        full: bool,
        request_id: str | None,
    ) -> dict[str, Any]:
        """Persist or coalesce dashboard sync work without doing provider I/O."""
        if not await self._projection_tables_available():
            return {
                "status": "error",
                "error": "Queued calendar sync requires the calendar action-log migration.",
            }
        if getattr(self._db, "pool", None) is None:
            return {
                "status": "error",
                "error": "Queued calendar sync requires an active database connection.",
            }

        correlation_id = self._normalize_request_id(request_id) or str(uuid.uuid4())
        idempotency_key = self._mutation_idempotency_key(FORCE_SYNC_ACTION_TYPE, correlation_id)
        incoming_payload = self._force_sync_action_payload(calendar_id=calendar_id, full=full)

        async with self._force_sync_queue_lock:
            prior = await self._load_force_sync_command(idempotency_key)
            if prior is not None:
                if prior["action_status"] in {MUTATION_STATUS_PENDING, MUTATION_STATUS_RUNNING}:
                    return self._queued_force_sync_result(prior, coalesced=True)
                if prior["action_status"] in {MUTATION_STATUS_APPLIED, MUTATION_STATUS_NOOP}:
                    return {
                        "status": "completed",
                        "request_id": prior.get("request_id"),
                        "full": bool(prior["action_payload"].get("full")),
                        "idempotent_replay": True,
                    }
                return {
                    "status": "failed",
                    "request_id": prior.get("request_id"),
                    "error": "The previously queued calendar sync failed.",
                    "idempotent_replay": True,
                }

            # A pending row is mergeable. A running row only satisfies a request
            # when it already covers the requested calendars and recovery level.
            for _attempt in range(2):
                active = await self._load_active_force_sync_command()
                if active is not None:
                    if active["action_status"] == MUTATION_STATUS_PENDING:
                        merged = await self._merge_pending_force_sync_command(
                            command=active,
                            action_payload=incoming_payload,
                        )
                        if merged is not None:
                            self._force_sync_queue_wake.set()
                            return self._queued_force_sync_result(merged, coalesced=True)
                        # The worker claimed it between our read and UPDATE.
                        continue
                    if self._force_sync_payload_covers(active["action_payload"], incoming_payload):
                        return self._queued_force_sync_result(active, coalesced=True)

                inserted = await self._insert_pending_force_sync_command(
                    idempotency_key=idempotency_key,
                    request_id=correlation_id,
                    action_payload=incoming_payload,
                )
                if inserted is not None:
                    self._force_sync_queue_wake.set()
                    return self._queued_force_sync_result(inserted, coalesced=False)

            return {
                "status": "error",
                "error": "Calendar sync queue changed concurrently; retry the request.",
            }

    async def _recover_force_sync_commands(self) -> int:
        """Return interrupted queue commands to pending at module startup."""
        if not await self._projection_tables_available():
            return 0
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return 0
        rows = await pool.fetch(
            """
            SELECT idempotency_key, request_id, action_status, action_payload
            FROM calendar_action_log
            WHERE action_type = $1
              AND action_status IN ($2, $3)
            ORDER BY created_at ASC
            """,
            FORCE_SYNC_ACTION_TYPE,
            MUTATION_STATUS_RUNNING,
            MUTATION_STATUS_PENDING,
        )
        commands = [self._force_sync_command_from_row(row) for row in rows]
        running = [
            command for command in commands if command["action_status"] == MUTATION_STATUS_RUNNING
        ]
        if not running:
            return 0

        # A running command may have acquired one pending successor before the
        # daemon stopped. A direct running→pending transition would violate the
        # one-pending index, so retain every requested calendar/full flag in the
        # oldest running action, terminally annotate the successors as
        # coalesced, then requeue that oldest action.
        primary = running[0]
        merged_payload = self._normalize_json_object(primary["action_payload"])
        for command in commands:
            if command is primary:
                continue
            merged_payload = self._merge_force_sync_payload(
                merged_payload,
                self._normalize_json_object(command["action_payload"]),
            )
            await pool.execute(
                """
                UPDATE calendar_action_log
                SET action_status = $2,
                    action_result = $3,
                    error = NULL,
                    applied_at = now(),
                    updated_at = now()
                WHERE idempotency_key = $1
                  AND action_type = $4
                  AND action_status IN ($5, $6)
                """,
                str(command["idempotency_key"]),
                MUTATION_STATUS_NOOP,
                self._encode_jsonb(
                    {
                        "status": "coalesced_after_restart",
                        "coalesced_into": primary["idempotency_key"],
                    }
                ),
                FORCE_SYNC_ACTION_TYPE,
                MUTATION_STATUS_PENDING,
                MUTATION_STATUS_RUNNING,
            )

        await pool.execute(
            """
            UPDATE calendar_action_log
            SET action_status = $2,
                action_payload = $3,
                updated_at = now()
            WHERE idempotency_key = $1
              AND action_type = $4
              AND action_status = $5
            """,
            str(primary["idempotency_key"]),
            MUTATION_STATUS_PENDING,
            self._encode_jsonb(merged_payload),
            FORCE_SYNC_ACTION_TYPE,
            MUTATION_STATUS_RUNNING,
        )
        return len(running)

    async def _claim_next_force_sync_command(self) -> dict[str, Any] | None:
        """Atomically lease the oldest pending command for this module instance."""
        if not await self._projection_tables_available():
            return None
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return None
        row = await pool.fetchrow(
            """
            WITH next_command AS (
                SELECT id
                FROM calendar_action_log
                WHERE action_type = $1
                  AND action_status = $2
                  AND NOT EXISTS (
                      SELECT 1
                      FROM calendar_action_log AS running
                      WHERE running.action_type = $1
                        AND running.action_status = $3
                  )
                ORDER BY created_at ASC
                FOR UPDATE SKIP LOCKED
                LIMIT 1
            )
            UPDATE calendar_action_log AS action
            SET action_status = $3, updated_at = now()
            FROM next_command
            WHERE action.id = next_command.id
            RETURNING action.idempotency_key, action.request_id,
                      action.action_status, action.action_payload
            """,
            FORCE_SYNC_ACTION_TYPE,
            MUTATION_STATUS_PENDING,
            MUTATION_STATUS_RUNNING,
        )
        return None if row is None else self._force_sync_command_from_row(row)

    async def _finalize_force_sync_command(
        self,
        *,
        command: Mapping[str, Any],
        action_status: Literal["applied", "failed"],
        action_result: dict[str, Any] | None,
        error: str | None,
    ) -> None:
        """Persist a terminal outcome only for the worker that owns the lease."""
        pool = getattr(self._db, "pool", None) if self._db is not None else None
        if pool is None:
            return
        await pool.execute(
            """
            UPDATE calendar_action_log
            SET action_status = $2,
                action_result = $3,
                error = $4,
                applied_at = now(),
                updated_at = now()
            WHERE idempotency_key = $1
              AND action_type = $5
              AND action_status = $6
            """,
            str(command["idempotency_key"]),
            action_status,
            None if action_result is None else self._encode_jsonb(action_result),
            error,
            FORCE_SYNC_ACTION_TYPE,
            MUTATION_STATUS_RUNNING,
        )

    async def _run_queued_force_sync_payload(
        self,
        action_payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Execute one merged queue payload while preserving every target calendar."""
        calendar_ids = self._force_sync_calendar_ids(action_payload)
        full = bool(action_payload.get("full"))
        if calendar_ids is None:
            return await self._run_force_sync(calendar_id=None, full=full)
        if len(calendar_ids) == 1:
            return await self._run_force_sync(calendar_id=calendar_ids[0], full=full)

        results = [
            await self._run_force_sync(calendar_id=calendar_id, full=full)
            for calendar_id in calendar_ids
        ]
        errors: list[str] = []
        for calendar_id, result in zip(calendar_ids, results, strict=True):
            result_errors = result.get("errors")
            if isinstance(result_errors, list):
                errors.extend(f"{calendar_id}: {error}" for error in result_errors)
            elif isinstance(result_errors, str) and result_errors:
                errors.append(f"{calendar_id}: {result_errors}")
            if result.get("status") == "error":
                errors.append(f"{calendar_id}: {result.get('error', 'sync failed')}")
        return {
            "status": "sync_completed" if not errors else "error",
            "calendar_ids": calendar_ids,
            "full": full,
            "results": results,
            "errors": errors or None,
        }

    async def _drain_force_sync_commands(self) -> int:
        """Drain queued commands serially; one provider owner never self-fan-outs."""
        processed = 0
        while command := await self._claim_next_force_sync_command():
            processed += 1
            try:
                action_result = await self._run_queued_force_sync_payload(
                    self._normalize_json_object(command.get("action_payload")),
                )
                result_errors = action_result.get("errors")
                failed = action_result.get("status") == "error" or bool(result_errors)
                error = None
                if failed:
                    if isinstance(result_errors, list):
                        error = "; ".join(str(item) for item in result_errors)
                    elif result_errors:
                        error = str(result_errors)
                    else:
                        error = str(action_result.get("error") or "Calendar sync failed")
                await self._finalize_force_sync_command(
                    command=command,
                    action_status=MUTATION_STATUS_FAILED if failed else MUTATION_STATUS_APPLIED,
                    action_result=action_result,
                    error=error,
                )
            except asyncio.CancelledError:
                # Keep the running lease intact. Startup recovery returns it to
                # pending after a graceful stop or process interruption.
                raise
            except Exception as exc:
                logger.exception("Queued calendar sync failed")
                await self._finalize_force_sync_command(
                    command=command,
                    action_status=MUTATION_STATUS_FAILED,
                    action_result=None,
                    error=str(exc),
                )
        return processed

    async def _run_force_sync_queue_worker(self) -> None:
        """Wait for durable commands and drain them outside request lifetimes."""
        while True:
            try:
                processed = await self._drain_force_sync_commands()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Calendar force-sync queue worker failed while claiming work")
                processed = 0
            if processed:
                continue
            await self._force_sync_queue_wake.wait()
            self._force_sync_queue_wake.clear()
