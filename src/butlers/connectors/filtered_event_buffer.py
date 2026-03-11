"""In-memory filtered event buffer for connector poll cycles.

Connectors accumulate filtered or errored events in a ``FilteredEventBuffer``
during each poll cycle.  When the cycle completes the buffer is flushed to
``connectors.filtered_events`` in a single batch INSERT.  No database write
occurs until ``flush()`` is called.

If the process crashes before ``flush()`` is called the buffered events are
lost; this is intentional — filtered events are operational visibility data,
not an audit trail.

Typical usage inside a connector::

    from butlers.connectors.filtered_event_buffer import FilteredEventBuffer

    buf = FilteredEventBuffer(
        connector_type="gmail",
        endpoint_identity="gmail:user:alice@example.com",
    )

    # During poll cycle — record filtered / errored messages
    buf.record(
        external_message_id="msg-1",
        source_channel="email",
        sender_identity="sender@example.com",
        subject_or_preview="Hello",
        filter_reason=FilteredEventBuffer.reason_label_exclude("CATEGORY_PROMOTIONS"),
        full_payload=buf.full_payload(...),
    )

    # After poll cycle — single batch INSERT then clear
    await buf.flush(pool)

Filter-reason format helpers
----------------------------
``reason_label_exclude(label)``
    ``label_exclude:<label>``

``reason_policy_rule(scope, action, rule_type)``
    ``<scope>:<action>:<rule_type>``  e.g. ``global_rule:skip:sender_domain``

``reason_validation_error()``
    ``validation_error``

``reason_submission_error()``
    ``submission_error``

Full-payload shape
------------------
``full_payload(...)`` returns a dict shaped as an ``ingest.v1`` envelope
(without ``schema_version`` — always assumed to be ``ingest.v1`` on replay)::

    {
        "source": {"channel": ..., "provider": ..., "endpoint_identity": ...},
        "event":  {"external_event_id": ..., "external_thread_id": ..., "observed_at": ...},
        "sender": {"identity": ...},
        "payload": {"raw": ..., "normalized_text": ...},
        "control": {"policy_tier": ...},
    }
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# SQL
# ---------------------------------------------------------------------------

_INSERT_SQL = """\
INSERT INTO connectors.filtered_events (
    received_at,
    connector_type,
    endpoint_identity,
    external_message_id,
    source_channel,
    sender_identity,
    subject_or_preview,
    filter_reason,
    status,
    full_payload,
    error_detail
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
"""

# ---------------------------------------------------------------------------
# FilteredEventBuffer
# ---------------------------------------------------------------------------


class FilteredEventBuffer:
    """Accumulate filtered/errored events in memory, flush in one batch INSERT.

    Args:
        connector_type: Connector type string (e.g. ``"gmail"``).
        endpoint_identity: Endpoint identity string (e.g. ``"gmail:user:alice@gmail.com"``).
    """

    def __init__(self, connector_type: str, endpoint_identity: str) -> None:
        self._connector_type = connector_type
        self._endpoint_identity = endpoint_identity
        self._rows: list[tuple[Any, ...]] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def record(
        self,
        *,
        external_message_id: str,
        source_channel: str,
        sender_identity: str,
        subject_or_preview: str | None,
        filter_reason: str,
        full_payload: dict[str, Any],
        status: str = "filtered",
        error_detail: str | None = None,
        received_at: datetime | None = None,
    ) -> None:
        """Append one event to the in-memory buffer.

        No database I/O occurs here.  Call :meth:`flush` after the poll cycle
        to persist all accumulated events.

        Args:
            external_message_id: Provider-assigned message ID.
            source_channel: Channel string (e.g. ``"email"``).
            sender_identity: Normalised sender identity string.
            subject_or_preview: Subject line or message preview (optional).
            filter_reason: Reason string — use the ``reason_*`` helpers.
            full_payload: Envelope-shaped dict (use :meth:`full_payload` helper).
            status: Row status; default ``"filtered"``, use ``"error"`` for
                processing failures.
            error_detail: Exception message or validation error text (optional).
            received_at: Override the received timestamp (defaults to now UTC).
        """
        ts = received_at if received_at is not None else datetime.now(UTC)
        self._rows.append(
            (
                ts,
                self._connector_type,
                self._endpoint_identity,
                external_message_id,
                source_channel,
                sender_identity,
                subject_or_preview,
                filter_reason,
                status,
                json.dumps(full_payload),
                error_detail,
            )
        )

    async def flush(self, pool: asyncpg.Pool) -> None:
        """Batch-INSERT all buffered events then clear the buffer.

        A single ``executemany`` call is issued so there is exactly one network
        round-trip per flush regardless of buffer size.

        If the buffer is empty the call is a no-op (no SQL executed).

        Flush failures are logged as warnings and do **not** raise; unflushed
        events are silently dropped.  This is intentional — filtered events are
        operational visibility data and their loss is acceptable.

        Args:
            pool: asyncpg pool with access to the ``connectors`` schema.
        """
        if not self._rows:
            return

        rows_to_flush = list(self._rows)

        try:
            async with pool.acquire() as conn:
                await conn.executemany(_INSERT_SQL, rows_to_flush)
            self._rows.clear()
            logger.debug(
                "Flushed %d filtered events: connector_type=%s, endpoint=%s",
                len(rows_to_flush),
                self._connector_type,
                self._endpoint_identity,
            )
        except Exception:
            logger.warning(
                "Failed to flush %d filtered events (connector_type=%s, endpoint=%s); "
                "events dropped",
                len(rows_to_flush),
                self._connector_type,
                self._endpoint_identity,
                exc_info=True,
            )

    def __len__(self) -> int:
        """Return the number of buffered (not yet flushed) events."""
        return len(self._rows)

    # ------------------------------------------------------------------
    # Filter-reason helpers
    # ------------------------------------------------------------------

    @staticmethod
    def reason_label_exclude(label: str) -> str:
        """Return filter reason for a label-exclusion filter.

        Format: ``label_exclude:<label_name>``

        Args:
            label: The label name that triggered the exclusion
                (e.g. ``"CATEGORY_PROMOTIONS"``).
        """
        return f"label_exclude:{label}"

    @staticmethod
    def reason_policy_rule(scope: str, action: str, rule_type: str) -> str:
        """Return filter reason for an ingestion-policy rule match.

        Format: ``<scope>:<action>:<rule_type>``
        Examples: ``global_rule:skip:sender_domain``,
        ``connector_rule:block:subject_pattern``

        Args:
            scope: Rule scope, e.g. ``"global_rule"`` or ``"connector_rule"``.
            action: Rule action, e.g. ``"skip"`` or ``"block"``.
            rule_type: Rule type, e.g. ``"sender_domain"``.
        """
        return f"{scope}:{action}:{rule_type}"

    @staticmethod
    def reason_validation_error() -> str:
        """Return filter reason for an envelope validation failure.

        Status should be ``"error"`` when using this reason.
        """
        return "validation_error"

    @staticmethod
    def reason_submission_error() -> str:
        """Return filter reason for a Switchboard submission failure.

        Status should be ``"error"`` when using this reason.
        """
        return "submission_error"

    # ------------------------------------------------------------------
    # Full-payload helper
    # ------------------------------------------------------------------

    @staticmethod
    def full_payload(
        *,
        channel: str,
        provider: str,
        endpoint_identity: str,
        external_event_id: str,
        external_thread_id: str | None,
        observed_at: str,
        sender_identity: str,
        raw: Any,
        normalized_text: str | None = None,
        policy_tier: str | None = None,
    ) -> dict[str, Any]:
        """Build an ``ingest.v1``-shaped payload dict for storage.

        ``schema_version`` is intentionally omitted — on replay it is always
        assumed to be ``ingest.v1``.

        Args:
            channel: Source channel (e.g. ``"email"``).
            provider: Provider name (e.g. ``"gmail"``).
            endpoint_identity: Endpoint identity of the connector.
            external_event_id: Provider-assigned event/message ID.
            external_thread_id: Provider-assigned thread ID (optional).
            observed_at: ISO-8601 timestamp when the event was observed.
            sender_identity: Normalised sender identity string.
            raw: Raw provider payload (any JSON-serialisable value).
            normalized_text: Normalised text body (optional).
            policy_tier: Policy tier assigned to the message (optional).

        Returns:
            Dict shaped as an ``ingest.v1`` envelope body.
        """
        return {
            "source": {
                "channel": channel,
                "provider": provider,
                "endpoint_identity": endpoint_identity,
            },
            "event": {
                "external_event_id": external_event_id,
                "external_thread_id": external_thread_id,
                "observed_at": observed_at,
            },
            "sender": {
                "identity": sender_identity,
            },
            "payload": {
                "raw": raw,
                "normalized_text": normalized_text,
            },
            "control": {
                "policy_tier": policy_tier,
            },
        }
