"""The owner's Vouch and Block verbs on an invitation organizer (REQ-module-calendar-042).

A verb is an ordinary ingestion rule, scoped to the calendar connector only (never ``global``), so
a calendar Block cannot silently block the same sender's email or messages. The scope grammar has
no wildcard (``connector:<type>:<endpoint_identity>`` is matched exactly), so one rule is written
per registered calendar endpoint, in one transaction. An endpoint registered later is still
covered for trust: the verdict reads every calendar scope, so a rule in any of them decides the
stamp; only the connector's ingest-time suppression is per endpoint.

A Vouch removes the Block rules this verb wrote and a Block removes the Vouch rules it wrote;
rules the owner wrote any other way, and every ``global`` rule, are left alone and still win.
After the rules change, the organizer's stored events are re-stamped at once instead of waiting
for the sweep.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from .organizer_trust import (
    BLOCK_CREATED_BY,
    VOUCH_CREATED_BY,
    load_policy_view,
    stamp_provider_metadata,
)

logger = logging.getLogger(__name__)

Verb = Literal["vouch", "block"]

CONNECTOR_TYPE = "google_calendar"
_RULE_PRIORITY = 10

_VERB_RULE: dict[Verb, tuple[str, str]] = {
    # verb -> (created_by, action) of the rule it writes
    "vouch": (VOUCH_CREATED_BY, "pass_through"),
    "block": (BLOCK_CREATED_BY, "block"),
}
_OPPOSITE: dict[Verb, str] = {"vouch": BLOCK_CREATED_BY, "block": VOUCH_CREATED_BY}


class NoCalendarEndpoint(RuntimeError):
    """No calendar connector endpoint is registered, so there is no scope to write a rule in."""


@dataclass(frozen=True, slots=True)
class DecisionResult:
    verb: Verb
    endpoints: int
    rules_written: int
    rules_removed: int


def normalize_address(value: object) -> str:
    return value.strip().casefold() if isinstance(value, str) else ""


async def apply_decision(pool: Any, address: str, verb: Verb) -> DecisionResult:
    """Write the verb's rule in every calendar scope and remove the opposite verb's rules.

    ``pool`` is the Switchboard pool, which owns ``ingestion_rules`` and ``connector_registry``.
    Idempotent: repeating a verb writes nothing new.
    """
    address = normalize_address(address)
    if not address or "@" not in address:
        raise ValueError("an organizer address is required")
    created_by, action = _VERB_RULE[verb]
    async with pool.acquire() as conn, conn.transaction():
        identities = await conn.fetch(
            "SELECT DISTINCT endpoint_identity FROM switchboard.connector_registry"
            " WHERE connector_type = $1",
            CONNECTOR_TYPE,
        )
        scopes = sorted(
            f"connector:{CONNECTOR_TYPE}:{row['endpoint_identity']}"
            for row in identities
            if row["endpoint_identity"]
        )
        if not scopes:
            raise NoCalendarEndpoint(CONNECTOR_TYPE)
        removed = await conn.fetch(
            """
            UPDATE switchboard.ingestion_rules
            SET deleted_at = now(), updated_at = now()
            WHERE deleted_at IS NULL
              AND created_by = $1
              AND rule_type = 'sender_address'
              AND starts_with(scope, $2)
              AND lower(btrim(condition ->> 'address')) = $3
            RETURNING id
            """,
            _OPPOSITE[verb],
            f"connector:{CONNECTOR_TYPE}:",
            address,
        )
        written = 0
        for scope in scopes:
            exists = await conn.fetchval(
                """
                SELECT 1 FROM switchboard.ingestion_rules
                WHERE deleted_at IS NULL AND enabled = TRUE
                  AND scope = $1 AND created_by = $2 AND rule_type = 'sender_address'
                  AND lower(btrim(condition ->> 'address')) = $3
                """,
                scope,
                created_by,
                address,
            )
            if exists:
                continue
            await conn.execute(
                """
                INSERT INTO switchboard.ingestion_rules
                    (scope, rule_type, condition, action, priority, enabled, name, created_by)
                VALUES ($1, 'sender_address', $2::jsonb, $3, $4, TRUE, $5, $6)
                """,
                scope,
                {"address": address},  # the pool's jsonb codec encodes the dict
                action,
                _RULE_PRIORITY,
                f"Calendar {verb}",
                created_by,
            )
            written += 1
    return DecisionResult(verb, len(scopes), written, len(removed))


async def restamp_organizer(pool: Any, address: str) -> int:
    """Re-stamp every stored provider event this organizer sent, now. Returns rows updated.

    Reads the policy fresh (no cache) so the verb takes effect on this call. A policy that cannot
    be read stamps ``unknown`` and flags the row for the sweep to retry, never a trusted verdict.
    """
    address = normalize_address(address)
    rows = await pool.fetch(
        """
        -- quarantine-exempt: re-stamping the verdict; reads metadata, returns no event text
        SELECT id, metadata, event_type
        FROM calendar_events
        WHERE metadata ->> 'source_type' = 'provider_event'
          AND lower(btrim(COALESCE(metadata ->> 'organizer', ''))) = $1
        """,
        address,
    )
    if not rows:
        return 0
    policy = await load_policy_view(pool)
    cache: dict[str, Any] = {}
    for row in rows:
        raw = row["metadata"]
        metadata = json.loads(raw) if isinstance(raw, str) else dict(raw or {})
        stamp = await stamp_provider_metadata(
            pool, metadata, event_type=row["event_type"], cache=cache, policy=policy
        )
        await pool.execute(
            "UPDATE calendar_events SET metadata = metadata || $2::jsonb WHERE id = $1",
            row["id"],
            stamp,
        )
    return len(rows)
