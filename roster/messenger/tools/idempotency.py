"""Idempotency and deduplication engine for Messenger butler.

Implements the idempotency contract from docs/roles/messenger_butler.md section 7:
- Canonical key derivation (deterministic idempotency key from request params)
- Duplicate terminal handling (return existing result for duplicate keys)
- In-flight coalescing (detect concurrent duplicate requests)
- Provider key propagation (map provider delivery IDs to requests)
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class IdempotencyKey:
    """Canonical idempotency key for delivery requests."""

    key: str
    """The canonical idempotency key string."""

    components: dict[str, Any]
    """The normalized components used to derive the key."""


@dataclass
class DeliveryStatus:
    """Status of a delivery request."""

    delivery_id: UUID
    """The delivery request ID."""

    status: str
    """Current status: pending, in_progress, delivered, failed, dead_lettered."""

    idempotency_key: str
    """The canonical idempotency key."""

    is_terminal: bool
    """Whether the delivery is in a terminal state."""

    terminal_result: dict[str, Any] | None = None
    """Terminal result payload (for delivered/failed states)."""

    provider_delivery_id: str | None = None
    """Provider's delivery ID (when available)."""


class IdempotencyEngine:
    """Idempotency and deduplication engine for delivery requests.

    Enforces exactly-once delivery semantics through:
    - Canonical key derivation from request parameters
    - DB uniqueness constraints on idempotency_key
    - Terminal state replay (return cached result for duplicates)
    - In-flight coalescing (detect concurrent duplicates)
    """

    def __init__(self, pool: Any) -> None:
        """Initialize the idempotency engine.

        Parameters
        ----------
        pool:
            asyncpg connection pool for the messenger database.
        """
        self._pool = pool

    @staticmethod
    def _normalize_target(
        *,
        intent: str,
        channel: str,
        recipient: str | None,
        request_context: dict[str, Any] | None,
    ) -> str:
        """Normalize target identity for idempotency key derivation.

        For 'send' intent, uses explicit recipient.
        For 'reply' intent, derives from request_context lineage.
        """
        if intent == "send":
            if not recipient:
                raise ValueError("Send intent requires explicit recipient")
            return recipient.strip().lower()

        if intent == "reply":
            if not request_context:
                raise ValueError("Reply intent requires request_context")

            # Derive target from lineage
            source_sender = request_context.get("source_sender_identity", "")
            source_thread = request_context.get("source_thread_identity")

            if not source_sender:
                raise ValueError("Reply requires source_sender_identity in request_context")

            # For thread-capable channels, include thread identity
            if source_thread:
                return f"{source_sender}:{source_thread}".strip().lower()

            return source_sender.strip().lower()

        raise ValueError(f"Unknown intent: {intent}")

    @staticmethod
    def _normalize_content(message: str, subject: str | None) -> str:
        """Normalize message content for hash computation.

        Returns deterministic hash of message and subject.
        """
        content = message.strip()
        if subject:
            content = f"{subject.strip()}|{content}"

        # Deterministic hash
        return hashlib.sha256(content.encode("utf-8")).hexdigest()

    @classmethod
    def derive_idempotency_key(
        cls,
        *,
        request_id: str | None,
        origin_butler: str,
        intent: str,
        channel: str,
        recipient: str | None,
        message: str,
        subject: str | None,
        request_context: dict[str, Any] | None,
    ) -> IdempotencyKey:
        """Derive canonical idempotency key from delivery request parameters.

        Key components (in order):
        1. request_id (when present)
        2. origin_butler
        3. intent
        4. channel
        5. normalized target identity
        6. normalized content hash

        Parameters
        ----------
        request_id:
            Optional upstream request ID from request_context.
        origin_butler:
            The butler originating the delivery request.
        intent:
            Delivery intent: 'send' or 'reply'.
        channel:
            Target channel: 'telegram', 'email', etc.
        recipient:
            Explicit recipient (required for 'send', optional for 'reply').
        message:
            Message content.
        subject:
            Optional subject (channel-specific).
        request_context:
            Request context with lineage metadata (required for 'reply').

        Returns
        -------
        IdempotencyKey
            The canonical idempotency key and its components.

        Raises
        ------
        ValueError
            If required fields are missing or invalid.
        """
        # Validate required fields
        if not origin_butler:
            raise ValueError("origin_butler is required")
        if not intent:
            raise ValueError("intent is required")
        if not channel:
            raise ValueError("channel is required")
        if not message:
            raise ValueError("message is required")

        # Normalize components
        normalized_origin = origin_butler.strip().lower()
        normalized_intent = intent.strip().lower()
        normalized_channel = channel.strip().lower()
        normalized_target = cls._normalize_target(
            intent=normalized_intent,
            channel=normalized_channel,
            recipient=recipient,
            request_context=request_context,
        )
        content_hash = cls._normalize_content(message, subject)

        # Build components dict
        components = {
            "request_id": request_id,
            "origin_butler": normalized_origin,
            "intent": normalized_intent,
            "channel": normalized_channel,
            "target": normalized_target,
            "content_hash": content_hash,
        }

        # Build canonical key
        key_parts = [
            f"request_id:{request_id}" if request_id else None,
            f"origin:{normalized_origin}",
            f"intent:{normalized_intent}",
            f"channel:{normalized_channel}",
            f"target:{normalized_target}",
            f"content:{content_hash}",
        ]

        canonical_key = ":".join(part for part in key_parts if part is not None)

        return IdempotencyKey(key=canonical_key, components=components)

    async def check_duplicate(self, idempotency_key: str) -> DeliveryStatus | None:
        """Check if a delivery with this idempotency key already exists.

        Returns the existing delivery status if found, None otherwise.

        Parameters
        ----------
        idempotency_key:
            The canonical idempotency key to check.

        Returns
        -------
        DeliveryStatus | None
            Existing delivery status if found, None if this is a new request.
        """
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT
                    id,
                    idempotency_key,
                    status,
                    terminal_error_class,
                    terminal_error_message,
                    terminal_at
                FROM delivery_requests
                WHERE idempotency_key = $1
                """,
                idempotency_key,
            )

            if row is None:
                return None

            # Check for terminal state
            status = row["status"]
            is_terminal = status in {"delivered", "failed", "dead_lettered"}

            terminal_result = None
            if is_terminal:
                if status == "delivered":
                    # Fetch provider delivery ID from receipts
                    receipt_row = await conn.fetchrow(
                        """
                        SELECT provider_delivery_id
                        FROM delivery_receipts
                        WHERE delivery_request_id = $1
                        AND receipt_type = 'sent'
                        ORDER BY received_at DESC
                        LIMIT 1
                        """,
                        row["id"],
                    )
                    provider_id = receipt_row["provider_delivery_id"] if receipt_row else None

                    terminal_result = {
                        "status": "ok",
                        "delivery": {
                            "channel": None,  # Will be filled by caller
                            "delivery_id": str(row["id"]),
                        },
                    }
                    if provider_id:
                        terminal_result["delivery"]["provider_delivery_id"] = provider_id

                elif status in {"failed", "dead_lettered"}:
                    terminal_result = {
                        "status": "error",
                        "error": {
                            "class": row["terminal_error_class"],
                            "message": row["terminal_error_message"],
                            "retryable": False,
                        },
                    }

            # Get provider delivery ID
            provider_delivery_id = None
            if not is_terminal:
                # For in-progress deliveries, check if we have a provider ID yet
                receipt_row = await conn.fetchrow(
                    """
                    SELECT provider_delivery_id
                    FROM delivery_receipts
                    WHERE delivery_request_id = $1
                    ORDER BY received_at DESC
                    LIMIT 1
                    """,
                    row["id"],
                )
                provider_delivery_id = receipt_row["provider_delivery_id"] if receipt_row else None

            return DeliveryStatus(
                delivery_id=row["id"],
                status=status,
                idempotency_key=row["idempotency_key"],
                is_terminal=is_terminal,
                terminal_result=terminal_result,
                provider_delivery_id=provider_delivery_id,
            )

    async def create_delivery_request(
        self,
        *,
        idempotency_key: str,
        request_id: str | None,
        origin_butler: str,
        channel: str,
        intent: str,
        target_identity: str,
        message_content: str,
        subject: str | None,
        request_envelope: dict[str, Any],
    ) -> UUID:
        """Create a new delivery request with idempotency protection.

        Parameters
        ----------
        idempotency_key:
            Canonical idempotency key.
        request_id:
            Optional upstream request ID.
        origin_butler:
            Butler originating the request.
        channel:
            Target channel.
        intent:
            Delivery intent ('send' or 'reply').
        target_identity:
            Normalized target identity.
        message_content:
            Message content.
        subject:
            Optional subject.
        request_envelope:
            Full request envelope for audit.

        Returns
        -------
        UUID
            The delivery request ID.

        Raises
        ------
        ValueError
            If a delivery with this idempotency key already exists.
        """
        # Parse request_id to UUID if present
        request_id_uuid = None
        if request_id:
            try:
                request_id_uuid = UUID(request_id)
            except ValueError:
                # Invalid UUID format, leave as None
                logger.warning(
                    "Invalid request_id UUID format",
                    extra={"request_id": request_id, "idempotency_key": idempotency_key},
                )

        async with self._pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    """
                    INSERT INTO delivery_requests (
                        idempotency_key,
                        request_id,
                        origin_butler,
                        channel,
                        intent,
                        target_identity,
                        message_content,
                        subject,
                        request_envelope,
                        status,
                        created_at,
                        updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, 'pending', $10, $10
                    )
                    RETURNING id
                    """,
                    idempotency_key,
                    request_id_uuid,
                    origin_butler,
                    channel,
                    intent,
                    target_identity,
                    message_content,
                    subject,
                    json.dumps(request_envelope),
                    datetime.now(UTC),
                )

                return row["id"]

            except Exception as exc:
                # Check if this is a uniqueness violation
                if (
                    "unique constraint" in str(exc).lower()
                    and "idempotency_key" in str(exc).lower()
                ):
                    raise ValueError(
                        f"Delivery request with idempotency key already exists: {idempotency_key}"
                    ) from exc
                raise

    async def record_provider_delivery_id(
        self,
        *,
        delivery_request_id: UUID,
        provider_delivery_id: str,
        receipt_type: str = "sent",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Record provider delivery ID for a delivery request.

        Parameters
        ----------
        delivery_request_id:
            The delivery request ID.
        provider_delivery_id:
            Provider's delivery identifier.
        receipt_type:
            Type of receipt: 'sent', 'delivered', 'read', 'webhook_confirmation'.
        metadata:
            Optional additional metadata.
        """
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO delivery_receipts (
                    delivery_request_id,
                    provider_delivery_id,
                    receipt_type,
                    received_at,
                    metadata
                )
                VALUES ($1, $2, $3, $4, $5::jsonb)
                """,
                delivery_request_id,
                provider_delivery_id,
                receipt_type,
                datetime.now(UTC),
                json.dumps(metadata or {}),
            )

        logger.info(
            "Provider delivery ID recorded",
            extra={
                "delivery_request_id": str(delivery_request_id),
                "provider_delivery_id": provider_delivery_id,
                "receipt_type": receipt_type,
            },
        )

    async def update_delivery_status(
        self,
        *,
        delivery_request_id: UUID,
        status: str,
        error_class: str | None = None,
        error_message: str | None = None,
    ) -> None:
        """Update delivery request status.

        Parameters
        ----------
        delivery_request_id:
            The delivery request ID.
        status:
            New status: 'in_progress', 'delivered', 'failed', 'dead_lettered'.
        error_class:
            Optional error class for failed deliveries.
        error_message:
            Optional error message for failed deliveries.
        """
        is_terminal = status in {"delivered", "failed", "dead_lettered"}

        async with self._pool.acquire() as conn:
            if is_terminal:
                await conn.execute(
                    """
                    UPDATE delivery_requests
                    SET
                        status = $1,
                        terminal_error_class = $2,
                        terminal_error_message = $3,
                        terminal_at = $4,
                        updated_at = $4
                    WHERE id = $5
                    """,
                    status,
                    error_class,
                    error_message,
                    datetime.now(UTC),
                    delivery_request_id,
                )
            else:
                await conn.execute(
                    """
                    UPDATE delivery_requests
                    SET
                        status = $1,
                        updated_at = $2
                    WHERE id = $3
                    """,
                    status,
                    datetime.now(UTC),
                    delivery_request_id,
                )

        logger.info(
            "Delivery status updated",
            extra={
                "delivery_request_id": str(delivery_request_id),
                "status": status,
                "error_class": error_class,
            },
        )
