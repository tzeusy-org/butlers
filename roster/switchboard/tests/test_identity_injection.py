"""Tests for Switchboard entity-first identity injection.

The owner-notification tests exercise the narrow, deterministic seam around a
transitory entity. They deliberately avoid a live delivery transport: the
Switchboard wiring test owns construction of the standard ``notify.v1``
callback, while this module proves the helper only grants that callback one
durable, race-safe attempt.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.identity import ResolvedContact
from butlers.tools.switchboard.identity import inject as identity_inject
from butlers.tools.switchboard.identity.inject import (
    _claim_unknown_sender_notification,
    resolve_and_inject_identity,
)

pytestmark = pytest.mark.unit

_OWNER_ID = uuid.uuid4()
_CONTACT_ID = uuid.uuid4()
_ENTITY_ID = uuid.uuid4()
_TEMP_ENTITY_ID = uuid.uuid4()


def _resolved_owner() -> ResolvedContact:
    return ResolvedContact(
        contact_id=_OWNER_ID,
        name="Owner",
        roles=["owner"],
        entity_id=None,
    )


def _resolved_known() -> ResolvedContact:
    return ResolvedContact(
        contact_id=_CONTACT_ID,
        name="Chloe",
        roles=[],
        entity_id=_ENTITY_ID,
    )


def _temp_entity(*, name: str = "Unknown (telegram 12345)") -> ResolvedContact:
    """Model the post-entity-migration temporary result: no contact ID."""
    return ResolvedContact(
        contact_id=None,
        name=name,
        roles=[],
        entity_id=_TEMP_ENTITY_ID,
        is_unidentified=True,
    )


async def _resolve_unknown(
    pool: AsyncMock,
    *,
    notify_owner_fn: AsyncMock | None = None,
    display_name: str | None = "Chloe L",
) -> object:
    with (
        patch(
            "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "butlers.tools.switchboard.identity.inject.create_temp_contact",
            new=AsyncMock(return_value=_temp_entity()),
        ),
    ):
        return await resolve_and_inject_identity(
            pool,
            "telegram",
            "12345",
            display_name=display_name,
            notify_owner_fn=notify_owner_fn,
        )


async def test_owner_message_gets_owner_preamble():
    """Owner message produces [Source: Owner, via telegram] preamble."""
    pool = AsyncMock()

    with patch(
        "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
        new=AsyncMock(return_value=_resolved_owner()),
    ):
        result = await resolve_and_inject_identity(pool, "telegram", "12345")

    assert result.preamble == "[Source: Owner, via telegram]"
    assert result.is_owner is True
    assert result.is_known is True
    assert result.is_unknown is False
    assert result.contact_id == _OWNER_ID
    assert result.sender_roles == ["owner"]


async def test_known_contact_gets_identity_preamble_with_entity_id():
    """Known contact produces full preamble with entity_id (contact_id excluded)."""
    pool = AsyncMock()

    with patch(
        "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
        new=AsyncMock(return_value=_resolved_known()),
    ):
        result = await resolve_and_inject_identity(pool, "telegram", "99999")

    assert f"contact_id: {_CONTACT_ID}" not in result.preamble
    assert f"entity_id: {_ENTITY_ID}" in result.preamble
    assert "Chloe" in result.preamble
    assert "via telegram" in result.preamble
    assert result.is_owner is False
    assert result.is_known is True
    assert result.entity_id == _ENTITY_ID


async def test_unknown_sender_creates_entity_only_identity_context():
    """An unknown sender is entity-only even when no delivery callback is wired."""
    pool = AsyncMock()

    result = await _resolve_unknown(pool, notify_owner_fn=None)

    assert "pending disambiguation" in result.preamble
    assert str(_TEMP_ENTITY_ID) in result.preamble
    assert result.contact_id is None
    assert result.entity_id == _TEMP_ENTITY_ID
    assert result.is_unknown is True
    assert result.is_known is False
    pool.fetchrow.assert_not_awaited()


async def test_unknown_sender_claims_once_and_notifies_with_safe_entity_review_link():
    """The first durable claimant gets one content-free entity-review notification."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value={"key": "identity:unknown_notified:telegram:12345"})
    notify_owner_fn = AsyncMock()

    result = await _resolve_unknown(
        pool,
        notify_owner_fn=notify_owner_fn,
        display_name=" Chloe\n L ",
    )

    assert result.new_unknown_sender is True
    notify_owner_fn.assert_awaited_once()
    message = notify_owner_fn.await_args.args[0]
    assert "Chloe L" in message
    assert "telegram" in message
    assert "/entities/index?state=unidentified" in message
    assert "12345" not in message
    assert "/contacts" not in message
    assert str(_TEMP_ENTITY_ID) not in message
    assert "inbound message body" not in message

    sql, state_key = pool.fetchrow.await_args.args
    assert "INSERT INTO state" in sql
    assert "ON CONFLICT (key) DO NOTHING" in sql
    assert "RETURNING key" in sql
    assert state_key == "identity:unknown_notified:telegram:12345"


async def test_repeated_unknown_sender_losing_claim_does_not_notify_again():
    """A claim conflict is the durable no-repeat result, not a retry trigger."""
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=None)
    notify_owner_fn = AsyncMock()

    result = await _resolve_unknown(pool, notify_owner_fn=notify_owner_fn)

    assert result.new_unknown_sender is False
    notify_owner_fn.assert_not_awaited()


async def test_claim_failure_is_observable_and_does_not_send_unclaimed_notification(
    caplog: pytest.LogCaptureFixture,
):
    """Claim failures are identifier-blind and cannot turn into owner-send storms."""
    sentinel = "15551234567@s.whatsapp.net"
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=RuntimeError(f"state unavailable for {sentinel}"))
    notify_owner_fn = AsyncMock()

    with (
        patch.object(identity_inject, "resolve_contact_by_channel", AsyncMock(return_value=None)),
        patch.object(
            identity_inject, "create_temp_contact", AsyncMock(return_value=_temp_entity())
        ),
        caplog.at_level(logging.WARNING),
    ):
        result = await resolve_and_inject_identity(
            pool,
            "whatsapp_jid",
            sentinel,
            notify_owner_fn=notify_owner_fn,
        )

    assert result.is_unknown is True
    assert result.new_unknown_sender is False
    notify_owner_fn.assert_not_awaited()
    assert "identity.unknown_sender_notification_claim_failed" in caplog.messages
    assert sentinel not in caplog.text
    assert "15551234567" not in caplog.text
    failure_record = next(
        record
        for record in caplog.records
        if record.message == "identity.unknown_sender_notification_claim_failed"
    )
    assert failure_record.channel_type == "whatsapp_jid"
    assert failure_record.failure_class == "RuntimeError"


async def test_delivery_failure_is_sealed_after_the_claim_and_does_not_block_result(
    caplog: pytest.LogCaptureFixture,
):
    """A transport failure warning is stable and contains no sender identifier."""
    sentinel = "15551234567@s.whatsapp.net"
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(
        return_value={"key": f"identity:unknown_notified:whatsapp_jid:{sentinel}"}
    )
    notify_owner_fn = AsyncMock(side_effect=RuntimeError(f"messenger unavailable for {sentinel}"))

    with (
        patch.object(identity_inject, "resolve_contact_by_channel", AsyncMock(return_value=None)),
        patch.object(
            identity_inject, "create_temp_contact", AsyncMock(return_value=_temp_entity())
        ),
        caplog.at_level(logging.WARNING),
    ):
        result = await resolve_and_inject_identity(
            pool,
            "whatsapp_jid",
            sentinel,
            notify_owner_fn=notify_owner_fn,
        )

    assert result.is_unknown is True
    assert result.new_unknown_sender is True
    notify_owner_fn.assert_awaited_once()
    assert "identity.unknown_sender_notification_failed" in caplog.messages
    assert sentinel not in caplog.text
    assert "15551234567" not in caplog.text
    failure_record = next(
        record
        for record in caplog.records
        if record.message == "identity.unknown_sender_notification_failed"
    )
    assert failure_record.failure_class == "RuntimeError"


async def test_atomic_claim_allows_only_one_concurrent_winner():
    """The claim itself, not timing, makes duplicate owner attempts impossible."""

    class AtomicClaimPool:
        def __init__(self) -> None:
            self._claimed = False
            self._lock = asyncio.Lock()

        async def fetchrow(self, sql: str, state_key: str) -> dict[str, str] | None:
            assert "INSERT INTO state" in sql
            assert state_key == "identity:unknown_notified:telegram:12345"
            async with self._lock:
                if self._claimed:
                    return None
                self._claimed = True
                return {"key": state_key}

    pool = AtomicClaimPool()
    first, second = await asyncio.gather(
        _claim_unknown_sender_notification(pool, "telegram", "12345"),
        _claim_unknown_sender_notification(pool, "telegram", "12345"),
    )

    assert sorted((first, second)) == [False, True]


async def test_empty_channel_value_returns_empty_result():
    """Empty channel_value skips resolution and returns an empty result."""
    pool = AsyncMock()
    result = await resolve_and_inject_identity(pool, "telegram", None)
    assert result.preamble == ""
    assert result.contact_id is None
    assert result.is_owner is False
    assert result.is_known is False
    assert result.is_unknown is False


async def test_batch_resolution_deduplicates_and_reuses_bulk_known_results():
    """REQ-switchboard-identity-002: each distinct batch speaker resolves once."""
    pool = AsyncMock()
    known = _resolved_known()
    unknown_result = MagicMock(
        preamble="[Source: Unknown sender]",
        contact_id=None,
        entity_id=_TEMP_ENTITY_ID,
        sender_roles=None,
        is_owner=False,
        is_known=False,
        is_unknown=True,
        new_unknown_sender=True,
        channel_value="222@s.whatsapp.net",
        display_name=None,
    )
    bulk = AsyncMock(
        return_value={
            ("whatsapp_jid", "111@s.whatsapp.net"): known,
            ("whatsapp_jid", "222@s.whatsapp.net"): None,
        }
    )
    reserve_unknown = AsyncMock(return_value=unknown_result)

    with (
        patch.object(
            identity_inject,
            "resolve_contacts_by_channel_bulk",
            bulk,
            create=True,
        ),
        patch.object(identity_inject, "_inject_unknown_identity", reserve_unknown),
    ):
        results = await identity_inject.resolve_sender_identities(
            pool,
            "whatsapp_user_client",
            [
                "111@s.whatsapp.net",
                "222@s.whatsapp.net",
                "111@s.whatsapp.net",
                "222@s.whatsapp.net",
            ],
            notify_owner_fn=AsyncMock(),
        )

    assert list(results) == ["111@s.whatsapp.net", "222@s.whatsapp.net"]
    assert results["111@s.whatsapp.net"].entity_id == _ENTITY_ID
    assert results["111@s.whatsapp.net"].display_name == "Chloe"
    assert results["222@s.whatsapp.net"] is unknown_result
    bulk.assert_awaited_once_with(
        pool,
        [
            ("whatsapp_jid", "111@s.whatsapp.net"),
            ("whatsapp_jid", "222@s.whatsapp.net"),
        ],
        raise_on_error=True,
    )
    reserve_unknown.assert_awaited_once()
    assert reserve_unknown.await_args.args[1] == "whatsapp_jid"
    assert reserve_unknown.await_args.kwargs.get("display_name") is None


async def test_batch_bulk_failure_does_not_enter_unknown_reservation_path(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-switchboard-identity-002: strict lookup failure cannot mint false entities."""
    sentinels = (
        "15551234567@s.whatsapp.net",
        "15551234567",
        "SELECT secret_phone FROM relationship.entity_facts",
        "postgresql://sentinel-user:sentinel-pass@db.example/sentinel",
        "sentinel inbound message body",
    )
    pool = AsyncMock()
    pool.fetch = AsyncMock(side_effect=RuntimeError(" | ".join(sentinels)))
    reserve_unknown = AsyncMock()

    with (
        patch.object(identity_inject, "_inject_unknown_identity", reserve_unknown),
        caplog.at_level(logging.DEBUG),
        pytest.raises(RuntimeError) as raised,
    ):
        await identity_inject.resolve_sender_identities(
            pool,
            "whatsapp_user_client",
            [sentinels[0]],
        )

    reserve_unknown.assert_not_awaited()
    assert type(raised.value).__name__ == "IdentityResolutionQueryError"
    assert str(raised.value) == "Identity resolution query failed"
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    rendered = f"{raised.value!s}\n{raised.value!r}\n{caplog.text}"
    for sentinel in sentinels:
        assert sentinel not in rendered


async def test_batch_unknown_reservation_skips_redundant_fail_open_lookup():
    """REQ-switchboard-identity-002: a bulk miss uses the real strict reservation seam."""
    sentinel = "15551234567@s.whatsapp.net"
    entity_id = uuid.uuid4()
    pool = MagicMock()
    pool.fetchrow = AsyncMock(
        side_effect=AssertionError("pre-resolved batch miss must not query the pool again")
    )
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=[{"value": {}}, None, None])
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=entity_id)
    transaction = AsyncMock()
    transaction.__aenter__ = AsyncMock(return_value=transaction)
    transaction.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=transaction)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    bulk = AsyncMock(return_value={("whatsapp_jid", sentinel): None})

    with patch.object(identity_inject, "resolve_contacts_by_channel_bulk", bulk):
        results = await identity_inject.resolve_sender_identities(
            pool,
            "whatsapp_user_client",
            [sentinel, sentinel],
        )

    pool.fetchrow.assert_not_awaited()
    identity_queries = [
        call
        for call in conn.fetchrow.await_args_list
        if "relationship.entity_facts" in call.args[0]
    ]
    assert len(identity_queries) == 1
    conn.fetch.assert_awaited_once()
    _sql, canonical_name, metadata = conn.fetchval.await_args.args
    label_prefix = "Unknown WhatsApp sender "
    assert canonical_name.startswith(label_prefix)
    uuid.UUID(canonical_name.removeprefix(label_prefix))
    assert sentinel not in canonical_name
    assert metadata["source_channel"] == "whatsapp_user_client"
    assert metadata["source_value"] == sentinel
    assert results[sentinel].entity_id == entity_id
    assert results[sentinel].channel_value == sentinel

    from butlers.tools.relationship.relationship_assert_fact import assert_sender_channel_fact

    with patch(
        "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact",
        new_callable=AsyncMock,
    ) as assert_fact:
        await assert_sender_channel_fact(
            pool,
            results[sentinel].entity_id,
            "whatsapp_user_client",
            results[sentinel].channel_value,
        )

    assert_fact.assert_awaited_once()
    _pool, subject, predicate, object_value = assert_fact.await_args.args
    assert subject == entity_id
    assert predicate == "has-handle"
    assert object_value == sentinel


async def test_batch_reservation_query_failure_is_strict_and_content_blind(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """REQ-switchboard-identity-002: a fresh race-check outage cannot mint an unknown."""
    sentinel = "15551234567@s.whatsapp.net"
    sentinels = (
        sentinel,
        "15551234567",
        "SELECT secret_phone FROM relationship.entity_facts",
        "postgresql://sentinel-user:sentinel-pass@db.example/sentinel",
        "sentinel inbound message body",
    )
    sentinel_error = " | ".join(sentinels)
    pool = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=[{"value": {}}, RuntimeError(sentinel_error)])
    conn.fetchval = AsyncMock(return_value=uuid.uuid4())
    transaction = AsyncMock()
    transaction.__aenter__ = AsyncMock(return_value=transaction)
    transaction.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=transaction)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    bulk = AsyncMock(return_value={("whatsapp_jid", sentinel): None})

    with (
        patch.object(identity_inject, "resolve_contacts_by_channel_bulk", bulk),
        caplog.at_level(logging.DEBUG),
    ):
        results = await identity_inject.resolve_sender_identities(
            pool,
            "whatsapp_user_client",
            [sentinel],
        )

    conn.fetchval.assert_not_awaited()
    assert results[sentinel].is_unknown is True
    assert results[sentinel].entity_id is None
    assert results[sentinel].channel_value == sentinel
    assert "identity.contact_resolution_query_failed" in caplog.messages
    assert "identity.batch_unknown_reservation_failed" in caplog.messages
    rendered = caplog.text
    for secret in sentinels:
        assert secret not in rendered


async def test_populated_reservation_yields_to_new_canonical_fact() -> None:
    """REQ-switchboard-identity-001: the post-lock canonical lookup wins the race."""
    sentinel = "15551234567@s.whatsapp.net"
    reserved_entity_id = uuid.uuid4()
    confirmed_entity_id = uuid.uuid4()
    call_order: list[str] = []
    pool = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock()

    async def fetchrow(query: str, *_args: object) -> dict[str, object] | None:
        if "SELECT value FROM state" in query:
            call_order.append("reservation")
            return {"value": {"entity_id": str(reserved_entity_id)}}
        if "relationship.entity_facts" in query:
            call_order.append("canonical")
            return {
                "entity_id": confirmed_entity_id,
                "name": "Confirmed Person",
                "roles": [],
                "is_unidentified": False,
            }
        if "FROM public.entities" in query:
            call_order.append("reserved")
            return {
                "canonical_name": "Unknown sender",
                "roles": [],
                "is_unidentified": True,
            }
        raise AssertionError("unexpected query")

    conn.fetchrow = AsyncMock(side_effect=fetchrow)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(side_effect=AssertionError("must not mint another entity"))
    transaction = AsyncMock()
    transaction.__aenter__ = AsyncMock(return_value=transaction)
    transaction.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=transaction)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    bulk = AsyncMock(return_value={("whatsapp_jid", sentinel): None})

    with patch.object(identity_inject, "resolve_contacts_by_channel_bulk", bulk):
        results = await identity_inject.resolve_sender_identities(
            pool,
            "whatsapp_user_client",
            [sentinel],
        )

    assert results[sentinel].entity_id == confirmed_entity_id
    assert results[sentinel].is_known is True
    assert call_order == ["reservation", "canonical"]


async def test_populated_reservation_does_not_reuse_tombstoned_entity() -> None:
    """REQ-switchboard-identity-001: stale reservation targets are never reused."""
    sentinel = "15551234568@s.whatsapp.net"
    reserved_entity_id = uuid.uuid4()
    replacement_entity_id = uuid.uuid4()
    pool = MagicMock()
    conn = AsyncMock()
    conn.execute = AsyncMock()

    async def fetchrow(query: str, *_args: object) -> dict[str, object] | None:
        if "SELECT value FROM state" in query:
            return {"value": {"entity_id": str(reserved_entity_id)}}
        if "relationship.entity_facts" in query:
            return None
        if "FROM public.entities" in query:
            if "merged_into" not in query or "deleted_at" not in query:
                return {
                    "canonical_name": "Tombstoned Unknown",
                    "roles": [],
                    "is_unidentified": True,
                }
            return None
        raise AssertionError("unexpected query")

    conn.fetchrow = AsyncMock(side_effect=fetchrow)
    conn.fetch = AsyncMock(return_value=[])
    conn.fetchval = AsyncMock(return_value=replacement_entity_id)
    transaction = AsyncMock()
    transaction.__aenter__ = AsyncMock(return_value=transaction)
    transaction.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=transaction)
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    bulk = AsyncMock(return_value={("whatsapp_jid", sentinel): None})

    with patch.object(identity_inject, "resolve_contacts_by_channel_bulk", bulk):
        results = await identity_inject.resolve_sender_identities(
            pool,
            "whatsapp_user_client",
            [sentinel],
        )

    assert results[sentinel].entity_id == replacement_entity_id
    assert results[sentinel].entity_id != reserved_entity_id


async def test_second_batch_reuses_transitory_entity_without_displaying_identifier():
    """REQ-switchboard-identity-002: reserved entities stay neutral across batches."""
    sentinel = "15551234567@s.whatsapp.net"
    transitory = MagicMock(
        contact_id=None,
        roles=[],
        entity_id=_TEMP_ENTITY_ID,
        is_unidentified=True,
    )
    transitory.name = sentinel
    first_result = MagicMock(
        preamble=f"[Source: Unknown sender (entity_id: {_TEMP_ENTITY_ID})]",
        contact_id=None,
        entity_id=_TEMP_ENTITY_ID,
        sender_roles=None,
        is_owner=False,
        is_known=False,
        is_unknown=True,
        new_unknown_sender=True,
        channel_value=sentinel,
        display_name=None,
    )
    pair = ("whatsapp_jid", sentinel)
    bulk = AsyncMock(side_effect=[{pair: None}, {pair: transitory}])
    reserve_unknown = AsyncMock(return_value=first_result)

    with (
        patch.object(identity_inject, "resolve_contacts_by_channel_bulk", bulk),
        patch.object(identity_inject, "_inject_unknown_identity", reserve_unknown),
    ):
        first_batch = await identity_inject.resolve_sender_identities(
            AsyncMock(),
            "whatsapp_user_client",
            [sentinel],
        )
        second_batch = await identity_inject.resolve_sender_identities(
            AsyncMock(),
            "whatsapp_user_client",
            [sentinel],
        )

    assert first_batch[sentinel].entity_id == _TEMP_ENTITY_ID
    reused = second_batch[sentinel]
    assert reused.entity_id == _TEMP_ENTITY_ID
    assert reused.is_unknown is True
    assert reused.is_known is False
    assert reused.display_name is None
    assert sentinel not in reused.preamble
    reserve_unknown.assert_awaited_once()


async def test_empty_string_channel_value_returns_empty_result():
    """An empty string channel_value also skips resolution."""
    pool = AsyncMock()
    result = await resolve_and_inject_identity(pool, "telegram", "")

    assert result.preamble == ""
    assert result.contact_id is None
    assert result.is_owner is False
    assert result.is_known is False
    assert result.is_unknown is False


async def test_known_contact_never_claims_or_notifies():
    """Known contacts bypass the owner-notification path entirely."""
    pool = AsyncMock()
    notify_owner_fn = AsyncMock()

    with patch(
        "butlers.tools.switchboard.identity.inject.resolve_contact_by_channel",
        new=AsyncMock(return_value=_resolved_known()),
    ):
        result = await resolve_and_inject_identity(
            pool, "telegram", "99999", notify_owner_fn=notify_owner_fn
        )

    assert result.new_unknown_sender is False
    notify_owner_fn.assert_not_awaited()
    pool.fetchrow.assert_not_awaited()
