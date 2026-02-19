"""Mailbox module — local message queue for inter-butler and external communication.

Provides five MCP tools for managing a butler's local mailbox:
- mailbox_post: Insert a new message
- mailbox_list: Query messages with filters
- mailbox_read: Fetch full message (auto-marks unread as read)
- mailbox_update_status: Change message status
- mailbox_stats: Aggregate counts by status
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from typing import Any

import asyncpg
from pydantic import BaseModel

from butlers.modules.base import Module

logger = logging.getLogger(__name__)

KNOWN_CHANNELS = {"mcp", "telegram", "email", "api", "scheduler", "system"}
VALID_STATUSES = {"unread", "read", "actioned", "archived"}


class MailboxConfig(BaseModel):
    """Configuration for the Mailbox module."""

    # No special config needed; placeholder for future options.
    pass


class MailboxModule(Module):
    """Mailbox module providing local message management MCP tools."""

    def __init__(self) -> None:
        self._pool: asyncpg.Pool | None = None

    @property
    def name(self) -> str:
        return "mailbox"

    @property
    def config_schema(self) -> type[BaseModel]:
        return MailboxConfig

    @property
    def dependencies(self) -> list[str]:
        return []

    def migration_revisions(self) -> str | None:
        return "mailbox"

    async def register_tools(self, mcp: Any, config: Any, db: Any) -> None:
        """Register all five mailbox MCP tools."""
        self._pool = db
        module = self  # capture for closures

        @mcp.tool()
        async def mailbox_post(
            sender: str,
            sender_channel: str,
            body: str,
            subject: str | None = None,
            priority: int = 2,
            metadata: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            """Post a message to the butler's mailbox. Returns the message UUID."""
            return await module._mailbox_post(
                module._get_pool(),
                sender=sender,
                sender_channel=sender_channel,
                body=body,
                subject=subject,
                priority=priority,
                metadata=metadata,
            )

        @mcp.tool()
        async def mailbox_list(
            status: str | None = None,
            sender: str | None = None,
            limit: int = 50,
            offset: int = 0,
        ) -> list[dict[str, Any]]:
            """List messages with optional filters, ordered by created_at DESC."""
            return await module._mailbox_list(
                module._get_pool(),
                status=status,
                sender=sender,
                limit=limit,
                offset=offset,
            )

        @mcp.tool()
        async def mailbox_read(message_id: str) -> dict[str, Any]:
            """Read a message by ID. Auto-marks unread messages as read."""
            return await module._mailbox_read(module._get_pool(), message_id)

        @mcp.tool()
        async def mailbox_update_status(message_id: str, status: str) -> dict[str, Any]:
            """Update a message's status."""
            return await module._mailbox_update_status(module._get_pool(), message_id, status)

        @mcp.tool()
        async def mailbox_stats() -> dict[str, int]:
            """Get message counts grouped by status."""
            return await module._mailbox_stats(module._get_pool())

    async def on_startup(self, config: Any, db: Any, credential_store: Any = None) -> None:
        """Store the DB pool reference."""
        self._pool = db

    async def on_shutdown(self) -> None:
        """No persistent resources to clean up."""
        self._pool = None

    # ------------------------------------------------------------------
    # Implementation helpers
    # ------------------------------------------------------------------

    async def _mailbox_post(
        self,
        pool: asyncpg.Pool,
        sender: str,
        sender_channel: str,
        body: str,
        subject: str | None = None,
        priority: int = 2,
        metadata: dict[str, Any] | str | None = None,
    ) -> dict[str, Any]:
        """Compatibility helper for mailbox_post contract used in integration tests."""
        if sender_channel not in KNOWN_CHANNELS:
            logger.warning(
                "Unknown sender_channel '%s' — accepting but may indicate a bug",
                sender_channel,
            )

        columns = await _mailbox_columns(pool)
        body_type = columns.get("body")
        metadata_type = columns.get("metadata")

        body_payload: Any = body if isinstance(body, dict) else {"text": body}
        metadata_payload = _normalize_json_input(metadata)

        body_value: Any
        body_expr: str
        if body_type == "jsonb":
            body_value = json.dumps(body_payload)
            body_expr = "$4::jsonb"
        else:
            body_value = body if isinstance(body, str) else json.dumps(body_payload)
            body_expr = "$4"

        metadata_value: Any
        metadata_expr: str
        if metadata_type == "jsonb":
            metadata_value = json.dumps(metadata_payload)
            metadata_expr = "$6::jsonb"
        else:
            metadata_value = json.dumps(metadata_payload)
            metadata_expr = "$6"

        row = await pool.fetchrow(
            f"""
            INSERT INTO mailbox (sender, sender_channel, subject, body, priority, metadata)
            VALUES ($1, $2, $3, {body_expr}, $5, {metadata_expr})
            RETURNING id, created_at
            """,
            sender,
            sender_channel,
            subject,
            body_value,
            priority,
            metadata_value,
        )

        return {
            "message_id": str(row["id"]),
            "created_at": row["created_at"].isoformat(),
        }

    async def _mailbox_list(
        self,
        pool: asyncpg.Pool,
        status: str | None = None,
        sender: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Compatibility helper for mailbox_list contract used in integration tests."""
        columns = await _mailbox_columns(pool)
        base_cols = [
            "id",
            "sender",
            "sender_channel",
            "subject",
            "body",
            "priority",
            "status",
            "metadata",
            "created_at",
        ]
        optional_cols = ["read_at", "actioned_at", "archived_at", "updated_at"]
        select_cols = base_cols + [col for col in optional_cols if col in columns]

        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if status is not None:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1

        if sender is not None:
            conditions.append(f"sender = ${idx}")
            params.append(sender)
            idx += 1

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT {", ".join(select_cols)}
            FROM mailbox
            {where}
            ORDER BY created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        params.extend([limit, offset])

        rows = await pool.fetch(query, *params)
        return [_row_to_dict_impl(row, parse_json_fields=True) for row in rows]

    async def _mailbox_read(self, pool: asyncpg.Pool, message_id: str) -> dict[str, Any]:
        """Compatibility helper for mailbox_read contract used in integration tests."""
        try:
            msg_uuid = uuid.UUID(message_id)
        except ValueError:
            return {"error": f"Message {message_id} not found"}

        row = await pool.fetchrow("SELECT * FROM mailbox WHERE id = $1", msg_uuid)
        if row is None:
            return {"error": f"Message {message_id} not found"}

        if row["status"] == "unread":
            await self._mailbox_update_status(pool, message_id, "read")
            row = await pool.fetchrow("SELECT * FROM mailbox WHERE id = $1", msg_uuid)

        if row is None:
            return {"error": f"Message {message_id} not found"}

        return _row_to_dict_impl(row, parse_json_fields=True)

    async def _mailbox_update_status(
        self,
        pool: asyncpg.Pool,
        message_id: str,
        status: str,
    ) -> dict[str, Any]:
        """Compatibility helper for mailbox_update_status contract used in integration tests."""
        if status not in VALID_STATUSES:
            return {"error": f"Invalid status '{status}'"}

        try:
            msg_uuid = uuid.UUID(message_id)
        except ValueError:
            return {"error": f"Message {message_id} not found"}

        columns = await _mailbox_columns(pool)

        updates = ["status = $2"]
        if "updated_at" in columns:
            updates.append("updated_at = now()")
        if status == "read" and "read_at" in columns:
            updates.append("read_at = COALESCE(read_at, now())")
        if status == "actioned":
            if "read_at" in columns:
                updates.append("read_at = COALESCE(read_at, now())")
            if "actioned_at" in columns:
                updates.append("actioned_at = COALESCE(actioned_at, now())")
        if status == "archived" and "archived_at" in columns:
            updates.append("archived_at = COALESCE(archived_at, now())")

        query = f"""
            UPDATE mailbox
            SET {", ".join(updates)}
            WHERE id = $1
            RETURNING *
        """
        row = await pool.fetchrow(query, msg_uuid, status)
        if row is None:
            return {"error": f"Message {message_id} not found"}
        return _row_to_dict_impl(row, parse_json_fields=True)

    async def _mailbox_stats(self, pool: asyncpg.Pool) -> dict[str, int]:
        """Compatibility helper for mailbox_stats contract used in integration tests."""
        rows = await pool.fetch(
            "SELECT status, COUNT(*)::int AS count FROM mailbox GROUP BY status"
        )
        stats = {"unread": 0, "read": 0, "actioned": 0, "archived": 0}
        for row in rows:
            status = row["status"]
            if status in stats:
                stats[status] = row["count"]
        stats["total"] = sum(stats.values())
        return stats

    def _get_pool(self) -> asyncpg.Pool:
        """Return the DB pool, raising if not initialised."""
        if self._pool is None:
            raise RuntimeError("MailboxModule not initialised — no DB pool available")
        return self._pool

    async def _post(
        self,
        sender: str,
        sender_channel: str,
        body: str,
        subject: str | None = None,
        priority: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert a message into the mailbox and return its UUID."""
        if sender_channel not in KNOWN_CHANNELS:
            logger.warning(
                "Unknown sender_channel '%s' — accepting but may indicate a bug",
                sender_channel,
            )

        pool = self._get_pool()
        meta_json = json.dumps(metadata or {})

        row = await pool.fetchrow(
            """
            INSERT INTO mailbox (sender, sender_channel, subject, body, priority, metadata)
            VALUES ($1, $2, $3, $4, $5, $6::jsonb)
            RETURNING id, created_at
            """,
            sender,
            sender_channel,
            subject,
            body,
            priority,
            meta_json,
        )

        return {
            "id": str(row["id"]),
            "created_at": row["created_at"].isoformat(),
        }

    async def _list(
        self,
        status: str | None = None,
        sender: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Query mailbox messages with optional filters."""
        pool = self._get_pool()

        conditions: list[str] = []
        params: list[Any] = []
        idx = 1

        if status is not None:
            conditions.append(f"status = ${idx}")
            params.append(status)
            idx += 1

        if sender is not None:
            conditions.append(f"sender = ${idx}")
            params.append(sender)
            idx += 1

        where = ""
        if conditions:
            where = "WHERE " + " AND ".join(conditions)

        query = f"""
            SELECT id, sender, sender_channel, subject, body, priority,
                   status, metadata, read_at, archived_at, created_at, updated_at
            FROM mailbox
            {where}
            ORDER BY created_at DESC
            LIMIT ${idx} OFFSET ${idx + 1}
        """
        params.extend([limit, offset])

        rows = await pool.fetch(query, *params)
        return [_row_to_dict(row) for row in rows]

    async def _read(self, message_id: str) -> dict[str, Any]:
        """Fetch a full message, auto-marking unread -> read."""
        pool = self._get_pool()
        msg_uuid = uuid.UUID(message_id)

        row = await pool.fetchrow(
            "SELECT * FROM mailbox WHERE id = $1",
            msg_uuid,
        )
        if row is None:
            return {"error": f"Message {message_id} not found"}

        # Auto-mark unread → read
        if row["status"] == "unread":
            now = datetime.now(UTC)
            await pool.execute(
                """
                UPDATE mailbox
                SET status = 'read', read_at = $2, updated_at = $2
                WHERE id = $1
                """,
                msg_uuid,
                now,
            )
            # Return updated values
            result = _row_to_dict(row)
            result["status"] = "read"
            result["read_at"] = now.isoformat()
            result["updated_at"] = now.isoformat()
            return result

        return _row_to_dict(row)

    async def _update_status(self, message_id: str, status: str) -> dict[str, Any]:
        """Change a message's status and set relevant timestamps."""
        pool = self._get_pool()
        msg_uuid = uuid.UUID(message_id)

        now = datetime.now(UTC)

        # Set status-specific timestamp columns
        extra_set = ""
        if status == "read":
            extra_set = ", read_at = $3"
        elif status == "archived":
            extra_set = ", archived_at = $3"

        query = f"""
            UPDATE mailbox
            SET status = $2, updated_at = $3{extra_set}
            WHERE id = $1
            RETURNING *
        """

        row = await pool.fetchrow(query, msg_uuid, status, now)
        if row is None:
            return {"error": f"Message {message_id} not found"}

        return _row_to_dict(row)

    async def _stats(self) -> dict[str, int]:
        """Return message counts grouped by status."""
        pool = self._get_pool()
        rows = await pool.fetch(
            "SELECT status, COUNT(*)::int AS count FROM mailbox GROUP BY status"
        )
        return {row["status"]: row["count"] for row in rows}


def _row_to_dict(row: asyncpg.Record) -> dict[str, Any]:
    """Convert an asyncpg Record to a JSON-serialisable dict."""
    return _row_to_dict_impl(row, parse_json_fields=False)


def _normalize_json_input(value: dict[str, Any] | str | None) -> dict[str, Any]:
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


async def _mailbox_columns(pool: asyncpg.Pool) -> dict[str, str]:
    rows = await pool.fetch(
        """
        SELECT column_name, udt_name
        FROM information_schema.columns
        WHERE table_schema = current_schema() AND table_name = 'mailbox'
        """
    )
    return {row["column_name"]: row["udt_name"] for row in rows}


def _row_to_dict_impl(
    row: asyncpg.Record,
    *,
    parse_json_fields: bool,
) -> dict[str, Any]:
    """Convert an asyncpg Record to a JSON-serialisable dict."""
    d: dict[str, Any] = {}
    for key, value in dict(row).items():
        if isinstance(value, uuid.UUID):
            d[key] = str(value)
        elif isinstance(value, datetime):
            d[key] = value.isoformat()
        elif parse_json_fields and key in {"body", "metadata"} and isinstance(value, str):
            try:
                d[key] = json.loads(value)
            except json.JSONDecodeError:
                d[key] = value
        else:
            d[key] = value
    return d
