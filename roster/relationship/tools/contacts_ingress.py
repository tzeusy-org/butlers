"""Ordinary owning-role Contacts reports and complete channel lookups.

These adapters confer no source authentication or owner authority. Public
arguments cannot select a reporter, confirmation, source or approved executor.
"""

from __future__ import annotations

import uuid
from typing import Any

from butlers.core.fact_authority import FactWriteContext, _current_report
from butlers.tools.relationship.relationship_assert_fact import relationship_assert_fact

_PREDICATES = {
    "email": "has-email",
    "phone": "has-phone",
    "website": "has-website",
    "telegram_username": "has-handle",
    "telegram_user_id": "has-handle",
    "other": "has-handle",
}


def _validate_channel(channel_type: str, channel_value: str) -> str:
    if channel_type not in _PREDICATES or not channel_value or len(channel_value) > 1024:
        raise ValueError("unsupported contacts channel")
    return _PREDICATES[channel_type]


async def report_channel(
    pool: Any, subject: uuid.UUID, channel_type: str, channel_value: str, primary: bool
) -> dict[str, Any]:
    """Report on the existing central transaction with fixed third-party provenance."""
    predicate = _validate_channel(channel_type, channel_value)
    if not isinstance(primary, bool):
        raise ValueError("invalid contacts primary flag")
    subject = uuid.UUID(str(subject))
    # REQ-module-contacts-001; REQ-relationship-facts-002/007.
    token = _current_report.set(FactWriteContext("third_party"))
    try:
        async with pool.acquire() as conn, conn.transaction():
            eligible = await conn.fetchval(
                """SELECT id FROM public.entities WHERE id=$1 AND entity_type='person'
                   AND metadata->>'merged_into' IS NULL
                   AND metadata->>'deleted_at' IS NULL FOR KEY SHARE""",
                subject,
            )
            if eligible is None:
                raise ValueError("contacts subject unavailable")
            result = await relationship_assert_fact(
                pool,
                subject,
                predicate,
                channel_value,
                src="contacts-backfill",
                object_kind="literal",
                primary=primary,
                conn=conn,
            )
            return result.as_dict()
    finally:
        _current_report.reset(token)


async def resolve_channel(
    pool: Any, channel_type: str, channel_value: str, request_id: uuid.UUID
) -> dict[str, Any]:
    """Complete distinct eligible matches; query failure never certifies empty."""
    request_id = uuid.UUID(str(request_id))
    if channel_type not in {"email", "phone"}:
        raise ValueError("unsupported contacts lookup")
    predicate = _validate_channel(channel_type, channel_value)
    normalized = channel_value.strip()
    email = normalized.lower()
    phones = list(
        dict.fromkeys([normalized, "".join(c for c in normalized if c.isdigit() or c == "+")])
    )
    result = {"request_id": str(request_id), "outcome": "unavailable", "entity_id": None}
    try:
        async with pool.acquire() as conn, conn.transaction():
            registered = await conn.fetchval(
                "SELECT predicate FROM relationship.entity_predicate_registry WHERE predicate=$1",
                predicate,
            )
            if registered != predicate:
                return result
            # REQ-module-contacts-002: no first-match LIMIT, candidate or suffix search.
            rows = await conn.fetch(
                """SELECT DISTINCT ef.subject AS entity_id
                   FROM relationship.entity_facts ef
                   JOIN public.entities e ON e.id=ef.subject
                   WHERE ef.predicate=$1 AND ef.object_kind='literal'
                     AND ef.validity='active' AND e.entity_type='person'
                     AND e.metadata->>'merged_into' IS NULL
                     AND e.metadata->>'deleted_at' IS NULL
                     AND (($1='has-email' AND lower(ef.object)=$2)
                          OR ($1='has-phone' AND ef.object=ANY($3::text[])))
                   ORDER BY ef.subject""",
                predicate,
                email,
                phones,
            )
            ids = {uuid.UUID(str(row["entity_id"])) for row in rows}
    except Exception:
        return result
    if len(ids) == 1:
        return {**result, "outcome": "unique", "entity_id": str(next(iter(ids)))}
    return {**result, "outcome": "ambiguous" if ids else "empty"}
