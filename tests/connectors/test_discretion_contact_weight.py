"""Discretion must weigh WhatsApp senders as the people they actually are.

``ContactWeightResolver`` decides how much benefit of the doubt a message gets:
a weight below ``weight_fail_open`` means an LLM failure silently discards the
message, and a low weight biases the LLM toward IGNORE. Two independent defects
made *every* WhatsApp sender — family included — resolve as ``unknown`` (0.3):

1. the connector passed the raw bridge JID, which is usually an opaque
   ``"<lid>@lid"`` (or carries a device ordinal) and matches nothing; and
2. the phone cross-reference compared the JID's bare digits to ``has-phone``
   with exact equality, while stored numbers keep their source formatting
   (``"+65 9815 0802"``).

Live effect: 88% of WhatsApp batches were dropped before ingest, so no
interaction facts were minted and Dunbar under-counted the owner's closest
contacts (specs ``passive-interaction-sync``, ``dunbar-tier-scoring``).
"""

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest

from butlers.connectors.discretion import ContactWeightResolver, WeightTier

pytestmark = pytest.mark.unit


def _pool_matching(stored_predicate: str, stored_object: str, roles: list[str]) -> Any:
    """Pool whose exact-triple query matches only one (predicate, object) pair."""
    entity_id = uuid.uuid4()

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        if len(args) >= 2 and args[0] == stored_predicate and args[1] == stored_object:
            return {"entity_id": entity_id, "name": "Contact", "roles": roles}
        return None

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.fetch = AsyncMock(return_value=[])
    return pool


def _pool_with_digits_match(stored_phone: str, roles: list[str]) -> Any:
    """Pool where has-phone is stored formatted, so only a digits match works.

    Mirrors production: the exact-equality ``fetchrow`` misses, and the
    digits-normalised ``fetch`` in ``_resolve_entity_by_phone_digits`` hits.
    """
    entity_id = uuid.uuid4()
    digits = "".join(ch for ch in stored_phone if ch.isdigit())

    async def _fetchrow(query: str, *args: Any) -> dict | None:
        # Exact equality against the formatted stored value never matches the
        # bare digits a JID yields.
        if len(args) >= 2 and args[1] == stored_phone:
            return {"entity_id": entity_id, "name": "Contact", "roles": roles}
        return None

    async def _fetch(query: str, *args: Any) -> list[dict]:
        if args and str(args[0]) == digits:
            return [{"entity_id": entity_id, "name": "Contact", "roles": roles}]
        return []

    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=_fetchrow)
    pool.fetch = AsyncMock(side_effect=_fetch)
    return pool


class TestPhoneFormattingFallback:
    """A JID yields bare digits; has-phone keeps its source formatting."""

    async def test_formatted_stored_number_resolves(self) -> None:
        pool = _pool_with_digits_match("+65 9815 0802", roles=[])
        resolver = ContactWeightResolver(pool)

        weight = await resolver.resolve("whatsapp_jid", "6598150802@s.whatsapp.net")

        assert weight == WeightTier().known, (
            "a known contact stored as '+65 9815 0802' must not weigh as a stranger"
        )

    async def test_family_role_reaches_inner_circle(self) -> None:
        pool = _pool_with_digits_match("+6598150802", roles=["family"])
        resolver = ContactWeightResolver(pool)

        weight = await resolver.resolve("whatsapp_jid", "6598150802@s.whatsapp.net")

        assert weight == WeightTier().inner_circle

    async def test_device_suffixed_jid_resolves(self) -> None:
        """ "<phone>:33@s.whatsapp.net" is the same person on a second device."""
        pool = _pool_with_digits_match("+6591153887", roles=["owner"])
        resolver = ContactWeightResolver(pool)

        weight = await resolver.resolve("whatsapp_jid", "6591153887:33@s.whatsapp.net")

        assert weight == WeightTier().owner

    async def test_genuinely_unknown_number_stays_unknown(self) -> None:
        pool = _pool_with_digits_match("+6512345678", roles=[])
        resolver = ContactWeightResolver(pool)

        weight = await resolver.resolve("whatsapp_jid", "6598150802@s.whatsapp.net")

        assert weight == WeightTier().unknown

    async def test_exact_stored_match_still_works(self) -> None:
        """Back-compat: an exactly-stored number never reaches the fallback."""
        pool = _pool_matching("has-phone", "6598150802", roles=[])
        resolver = ContactWeightResolver(pool)

        weight = await resolver.resolve("whatsapp_jid", "6598150802@s.whatsapp.net")

        assert weight == WeightTier().known

    async def test_handle_triple_takes_precedence(self) -> None:
        pool = _pool_matching("has-handle", "telegram:123", roles=["family"])
        resolver = ContactWeightResolver(pool)

        weight = await resolver.resolve("telegram_chat_id", "123")

        assert weight == WeightTier().inner_circle


# ---------------------------------------------------------------------------
# Connector: which identity is weighed
# ---------------------------------------------------------------------------


class TestBatchWeightIdentity:
    """A batch must be weighed by its participants, not the raw last sender."""

    def _connector(self) -> Any:
        from butlers.connectors.whatsapp_user_client import (
            WhatsAppUserClientConnector,
            WhatsAppUserClientConnectorConfig,
        )

        return WhatsAppUserClientConnector(
            config=WhatsAppUserClientConnectorConfig(
                switchboard_mcp_url="http://localhost:1/mcp",
                endpoint_identity="whatsapp:+6591153887",
            ),
            db_pool=AsyncMock(),
        )

    async def test_batch_weight_is_the_highest_participant(self) -> None:
        """A group is worth its closest member, not whoever spoke last.

        Weighing the last sender means one stranger's message in a family
        thread drops the whole batch to `unknown`.
        """
        conn = self._connector()
        weights = {
            "6598150802@s.whatsapp.net": WeightTier().inner_circle,
            "6512345678@s.whatsapp.net": WeightTier().unknown,
        }
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(side_effect=lambda _t, v: weights.get(v, 0.3))
        conn._weight_resolver = resolver

        weight = await conn._resolve_batch_weight(
            {
                "6598150802@s.whatsapp.net": "Mummy",
                "6512345678@s.whatsapp.net": "Stranger",
            }
        )

        assert weight == WeightTier().inner_circle

    async def test_participants_are_already_normalised(self) -> None:
        """Raw '<lid>@lid' can never resolve; the translated form must be used."""
        conn = self._connector()
        seen: list[str] = []
        resolver = AsyncMock()

        async def _resolve(_t: str, v: str) -> float:
            seen.append(v)
            return 0.3

        resolver.resolve = _resolve
        conn._weight_resolver = resolver

        await conn._resolve_batch_weight({"6598150802@s.whatsapp.net": "Mummy"})

        assert seen == ["6598150802@s.whatsapp.net"]
        assert not any(v.endswith("@lid") for v in seen)

    async def test_no_participants_falls_back_to_forwarding(self) -> None:
        """An unresolvable batch must not be silently down-weighted to dropping."""
        conn = self._connector()
        conn._weight_resolver = AsyncMock()

        assert await conn._resolve_batch_weight({}) == 1.0

    async def test_no_resolver_forwards(self) -> None:
        conn = self._connector()
        conn._weight_resolver = None

        assert await conn._resolve_batch_weight({"x@s.whatsapp.net": "x"}) == 1.0

    async def test_owner_is_excluded_from_the_batch_weight(self) -> None:
        """Owner weight is 1.0 — at weight_bypass, which skips the LLM entirely.

        Including the owner would make every thread they have ever replied in
        bypass discretion unconditionally: a dispatch-cost decision smuggled in
        via an identity lookup. The weight scores the other side of the
        conversation.
        """
        conn = self._connector()
        weights = {
            "6591153887@s.whatsapp.net": WeightTier().owner,
            "6512345678@s.whatsapp.net": WeightTier().unknown,
        }
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(side_effect=lambda _t, v: weights[v])
        conn._weight_resolver = resolver

        weight = await conn._resolve_batch_weight(
            {"6591153887@s.whatsapp.net": "Me", "6512345678@s.whatsapp.net": "Stranger"},
            "6591153887@s.whatsapp.net",
        )

        assert weight == WeightTier().unknown

    async def test_owner_only_batch_forwards(self) -> None:
        """Nobody left to weigh — bias toward forwarding, not suppression."""
        conn = self._connector()
        resolver = AsyncMock()
        resolver.resolve = AsyncMock(return_value=WeightTier().owner)
        conn._weight_resolver = resolver

        weight = await conn._resolve_batch_weight(
            {"6591153887@s.whatsapp.net": "Me"}, "6591153887@s.whatsapp.net"
        )

        assert weight == 1.0
        resolver.resolve.assert_not_awaited()
