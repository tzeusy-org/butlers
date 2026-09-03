"""Authoritative producer resolution for Relationship stale-contact signals."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from butlers.core.expected_signals import (
    ExpectedSignalEvaluation,
    ExpectedSignalState,
    upsert_expected_signal,
)
from butlers.identity import resolve_contacts_by_channel_bulk

logger = logging.getLogger(__name__)

_SOURCE_CHANNEL_PRODUCERS = {
    "email": "connector:gmail",
    "telegram_user_client": "connector:telegram_user_client",
    "whatsapp_user_client": "connector:whatsapp_user_client",
}


@dataclass(frozen=True, slots=True)
class StaleContactSignal:
    """One persisted cadence evaluation plus its owner-facing eligibility."""

    contact_id: UUID
    evaluation: ExpectedSignalEvaluation

    @property
    def is_overdue(self) -> bool:
        return self.evaluation.state is ExpectedSignalState.ABSENT


def interaction_sync_attestation(
    *,
    source_channel: str,
    source_endpoint_identity: str | None,
    source_identity: str,
) -> dict[str, str] | None:
    """Build the reserved server attestation for a mapped passive writer.

    Missing endpoint identity deliberately yields no attestation.  The interaction
    remains useful history, but it cannot authorize an absence claim.
    """
    producer = _SOURCE_CHANNEL_PRODUCERS.get(source_channel)
    endpoint = (source_endpoint_identity or "").strip()
    identity = source_identity.strip()
    if producer is None or not endpoint or not identity:
        return None
    return {
        "producer": producer,
        "source_channel": source_channel,
        "source_endpoint_identity": endpoint,
        "source_identity": identity,
        "writer": "interaction_sync",
    }


def owner_attestation(*, principal: str) -> dict[str, str]:
    """Build a server-only attestation for an authenticated owner write."""
    if principal != "owner":
        raise ValueError("owner interaction attestation requires the authenticated owner")
    return {
        "producer": "owner",
        "source_channel": "manual",
        "source_identity": principal,
        "writer": "dashboard_owner",
    }


def _metadata_value(row: Any) -> Any:
    try:
        return row["metadata"]
    except (KeyError, TypeError):
        return None


def _mapped_identity_producer(predicate: object, value: object) -> str | None:
    """Map one active contact identity to an expected-signal producer family."""
    if not isinstance(predicate, str) or not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    if not normalized:
        return None
    if predicate == "has-email":
        return "connector:gmail"
    if predicate == "has-phone":
        return "connector:whatsapp_user_client"
    if predicate != "has-handle":
        return None
    if normalized.startswith("telegram:") and normalized != "telegram:":
        return "connector:telegram_user_client"
    if normalized.endswith("@s.whatsapp.net"):
        return "connector:whatsapp_user_client"
    return None


async def _active_identity_producers(pool: Any, entity_id: UUID) -> set[str] | None:
    """Inventory every active mapped identity; unreadable evidence fails closed."""
    try:
        rows = await pool.fetch(
            """
            SELECT predicate, object
            FROM relationship.entity_facts
            WHERE subject = $1
              AND validity = 'active'
              AND object_kind = 'literal'
              AND predicate IN ('has-email', 'has-handle', 'has-phone')
            ORDER BY predicate, object
            """,
            entity_id,
        )
    except Exception:  # noqa: BLE001 -- incomplete identity evidence cannot authorize absence
        logger.warning(
            "Stale-contact identity inventory unavailable for entity %s",
            entity_id,
            exc_info=True,
        )
        return None
    return {
        producer
        for row in rows
        if (producer := _mapped_identity_producer(row["predicate"], row["object"])) is not None
    }


async def _resolve_latest_producer(
    pool: Any,
    *,
    entity_id: UUID,
    latest_rows: list[Any],
) -> tuple[str, str | None]:
    """Resolve all tied latest observations to one corroborated producer."""
    authorities: set[tuple[str, str | None]] = set()
    connector_pairs: list[tuple[str, str]] = []
    connector_authorities: list[tuple[str, str, str]] = []

    for row in latest_rows:
        metadata = _metadata_value(row)
        if not isinstance(metadata, dict):
            return "unknown", None
        attestation = metadata.get("expected_signal_source")
        if not isinstance(attestation, dict):
            return "unknown", None

        producer = attestation.get("producer")
        writer = attestation.get("writer")
        if producer == "owner":
            if (
                writer != "dashboard_owner"
                or attestation.get("source_channel") != "manual"
                or attestation.get("source_identity") != "owner"
                or attestation.get("source_endpoint_identity") is not None
            ):
                return "unknown", None
            authorities.add(("owner", None))
            continue

        source_channel = attestation.get("source_channel")
        endpoint = attestation.get("source_endpoint_identity")
        source_identity = attestation.get("source_identity")
        if (
            writer != "interaction_sync"
            or not isinstance(source_channel, str)
            or _SOURCE_CHANNEL_PRODUCERS.get(source_channel) != producer
            or not isinstance(endpoint, str)
            or not endpoint.strip()
            or not isinstance(source_identity, str)
            or not source_identity.strip()
        ):
            return "unknown", None
        connector_pairs.append((source_channel, source_identity))
        connector_authorities.append((producer, endpoint.strip(), source_identity))

    if connector_pairs:
        try:
            resolved = await resolve_contacts_by_channel_bulk(
                pool,
                connector_pairs,
                raise_on_error=True,
            )
        except Exception:  # noqa: BLE001 -- unreadable identity evidence fails closed
            logger.warning(
                "Stale-contact identity corroboration unavailable for entity %s",
                entity_id,
                exc_info=True,
            )
            return "unknown", None

        for pair, (producer, endpoint, _source_identity) in zip(
            connector_pairs, connector_authorities, strict=True
        ):
            contact = resolved.get(pair)
            if contact is None or contact.entity_id != entity_id:
                return "unknown", None
            authorities.add((producer, endpoint))

    identity_producers = await _active_identity_producers(pool, entity_id)
    if identity_producers is None:
        return "unknown", None
    attested_producers = {producer for producer, _endpoint in authorities}
    participating_producers = attested_producers | identity_producers
    if len(participating_producers) != 1:
        return "unknown", None
    sole_producer = next(iter(participating_producers))
    if sole_producer.startswith("connector:") and sole_producer not in identity_producers:
        return "unknown", None
    if len(authorities) != 1:
        return "unknown", None
    return next(iter(authorities))


async def evaluate_stale_contact_signal(
    pool: Any,
    *,
    contact_id: UUID,
    entity_id: UUID,
    expected_cadence: timedelta,
    last_observed_at: datetime | None,
    now: datetime | None = None,
) -> StaleContactSignal:
    """Persist one contact's signal after fail-closed producer resolution."""
    producer = "unknown"
    endpoint: str | None = None

    if last_observed_at is not None:
        try:
            latest_rows = await pool.fetch(
                """
                SELECT metadata
                FROM facts
                WHERE entity_id = $1
                  AND predicate LIKE 'interaction_%'
                  AND scope = 'relationship'
                  AND validity = 'active'
                  AND valid_at = $2
                ORDER BY id
                """,
                entity_id,
                last_observed_at,
            )
        except Exception:  # noqa: BLE001 -- unreadable provenance fails closed
            logger.warning(
                "Stale-contact provenance unavailable for entity %s",
                entity_id,
                exc_info=True,
            )
            latest_rows = []
        if latest_rows:
            producer, endpoint = await _resolve_latest_producer(
                pool,
                entity_id=entity_id,
                latest_rows=list(latest_rows),
            )

    evaluation = await upsert_expected_signal(
        pool,
        signal_key=f"relationship:stale-contact:{contact_id}",
        producer=producer,
        producer_endpoint_identity=endpoint,
        expected_cadence=expected_cadence,
        last_observed_at=last_observed_at,
        now=now,
    )
    return StaleContactSignal(contact_id=contact_id, evaluation=evaluation)


__all__ = [
    "StaleContactSignal",
    "evaluate_stale_contact_signal",
    "interaction_sync_attestation",
    "owner_attestation",
]
