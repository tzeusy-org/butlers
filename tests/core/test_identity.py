"""Tests for src/butlers/identity.py — resolve_contact_by_channel and helpers.

Migration bead 7 (bu-akads): resolve_contact_by_channel now queries
relationship.entity_facts instead of public.contact_info / public.contacts.
contact_id is no longer returned (set to None); entity_id is the primary key.
build_identity_preamble no longer includes contact_id in its output string.

entity-v3 (bu-hvrt1): create_temp_contact NO LONGER asserts the sender's channel
triple to relationship.entity_facts. Switchboard ingress must not write
entity_facts (switchboard-identity invariant); that assertion moved into a
deterministic post-resolution hook in the routing pipeline
(relationship.tools.relationship_assert_fact.assert_sender_channel_fact).

Phase 7 (bu-jnaa3): create_temp_contact now mints ONLY the public.entities row —
no public.contacts row — and returns contact_id=None.

Covers:
- resolve_contact_by_channel: owner, non-owner, unknown→None, DB error→None
- build_identity_preamble: owner, known non-owner with/without entity_id, unknown with temp_id
- create_temp_contact: creates new, returns existing on race, DB error→None
- create_temp_contact: mints the public entity/contact WITHOUT any entity_facts
  assertion (the channel triple is the pipeline hook's responsibility)
"""

from __future__ import annotations

import re
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.identity import (
    ResolvedContact,
    _telegram_username_candidates,
    build_identity_preamble,
    channel_value_for_storage,
    create_temp_contact,
    resolve_contact_by_channel,
)

pytestmark = pytest.mark.unit

# Patch the central writer in its home module — create_temp_contact uses a
# deferred import, so patching the source module is the only stable anchor.
_ASSERT_FACT_PATCH = "butlers.tools.relationship.relationship_assert_fact.relationship_assert_fact"

_OWNER_ID = uuid.uuid4()
_CONTACT_ID = uuid.uuid4()
_ENTITY_ID = uuid.uuid4()


def _make_pool_with_row(row: dict[str, Any] | None) -> Any:
    mock_row = None
    if row is not None:
        mock_row = MagicMock()
        mock_row.__getitem__ = lambda self, k: row[k]
        mock_row.get = lambda k, default=None: row.get(k, default)
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(return_value=mock_row)
    return pool


# ---------------------------------------------------------------------------
# resolve_contact_by_channel
# ---------------------------------------------------------------------------


async def test_resolve_contact_by_channel():
    """Bead 7 cut-over: queries entity_facts triples; entity_id is primary key; contact_id=None.

    - Owner entity: roles=['owner'], contact_id is None post-bead-7
    - Known non-owner entity: entity_id returned
    - Unknown → None
    - DB error → None
    - String UUID coercion (entity_id)
    """
    # Owner entity — rows from entity_facts query: entity_id, name (canonical_name), roles
    pool = _make_pool_with_row({"entity_id": _OWNER_ID, "name": "Owner", "roles": ["owner"]})
    r = await resolve_contact_by_channel(pool, "telegram", "12345")
    assert (
        r is not None
        and r.contact_id is None  # bead 7: no contact_id returned
        and r.entity_id == _OWNER_ID
        and r.roles == ["owner"]
    )

    # Known non-owner entity
    pool2 = _make_pool_with_row({"entity_id": _ENTITY_ID, "name": "Chloe", "roles": []})
    r2 = await resolve_contact_by_channel(pool2, "telegram", "99999")
    assert r2 is not None and r2.contact_id is None and r2.entity_id == _ENTITY_ID

    # Unknown → None
    pool3 = AsyncMock()
    pool3.fetchrow = AsyncMock(return_value=None)
    assert await resolve_contact_by_channel(pool3, "telegram", "99999999") is None

    # DB error → None
    pool4 = AsyncMock()
    pool4.fetchrow = AsyncMock(side_effect=Exception("relation does not exist"))
    assert await resolve_contact_by_channel(pool4, "telegram", "12345") is None

    # String UUID coercion for entity_id
    pool5 = _make_pool_with_row(
        {
            "entity_id": str(_ENTITY_ID),
            "name": "Alice",
            "roles": ["trusted"],
        }
    )
    r5 = await resolve_contact_by_channel(pool5, "email", "alice@example.com")
    assert r5 is not None and isinstance(r5.entity_id, uuid.UUID) and r5.entity_id == _ENTITY_ID


@pytest.mark.parametrize(
    ("channel", "value", "predicate", "roles"),
    [
        ("telegram", "86807245", "has-handle", []),
        ("email", "owner@example.com", "has-email", ["owner"]),
        ("phone", "+15555551234", "has-phone", []),
    ],
)
async def test_resolve_via_entity_facts_triple(channel, value, predicate, roles):
    """Bead 7 (bu-akads): each channel resolves through relationship.entity_facts using
    its channel-specific predicate, returning the authoritative entity_id.

    The channel->predicate mapping (telegram=has-handle, email=has-email,
    phone=has-phone) is the store-split resolution contract.
    """
    pool = _make_pool_with_row({"entity_id": _ENTITY_ID, "name": "Person", "roles": roles})
    result = await resolve_contact_by_channel(pool, channel, value)

    assert result is not None
    assert result.contact_id is None  # entity_id is authoritative post bead 7
    assert result.entity_id == _ENTITY_ID

    # The resolution queries entity_facts with the channel-specific predicate + value.
    query_call = pool.fetchrow.call_args
    assert query_call.args[1] == predicate
    assert query_call.args[2] == value


async def test_resolve_contact_by_channel_maps_telegram_user_client_id():
    """telegram_user_client sender ids resolve via has-handle with telegram: prefix."""
    mock_row = MagicMock()
    mock_row.__getitem__ = lambda self, k: {
        "entity_id": _ENTITY_ID,
        "name": "Chloe Wong",
        "roles": [],
    }[k]
    pool = AsyncMock()
    pool.fetchrow = AsyncMock(side_effect=[None, mock_row])

    result = await resolve_contact_by_channel(pool, "telegram_user_client", "86807245")

    assert result is not None
    assert result.contact_id is None  # bead 7: entity_id is primary
    assert result.entity_id == _ENTITY_ID
    # Second call uses telegram: prefix for the fallback
    assert pool.fetchrow.await_args_list[1].args[2] == "telegram:86807245"


# ---------------------------------------------------------------------------
# build_identity_preamble
# ---------------------------------------------------------------------------


def test_build_identity_preamble():
    """Bead 7: preamble uses entity_id only (contact_id dropped from output string).

    - Owner with entity_id: shows entity_id, no contact_id
    - Owner without entity_id: minimal form, no contact_id
    - Known contact with entity_id: entity_id only
    - Unknown with temp_entity_id: entity_id shown
    - Unknown with temp_contact_id only (create_temp_contact fallback): contact_id shown
    - Unknown without any temp ID: minimal form
    - Null name fallback: 'Unknown Contact'
    """
    # Owner without entity_id — no contact_id in output (bead 7)
    r = ResolvedContact(contact_id=None, name="Owner", roles=["owner"], entity_id=None)
    p = build_identity_preamble(r, "telegram")
    assert "[Source: Owner" in p and "via telegram" in p
    assert "contact_id" not in p

    # Owner with entity_id — entity_id shown, no contact_id
    r_eid = ResolvedContact(contact_id=None, name="Owner", roles=["owner"], entity_id=_ENTITY_ID)
    p_eid = build_identity_preamble(r_eid, "telegram")
    assert f"entity_id: {_ENTITY_ID}" in p_eid
    assert "contact_id" not in p_eid

    # Known contact with entity_id — entity_id only
    r2 = ResolvedContact(contact_id=None, name="Chloe", roles=[], entity_id=_ENTITY_ID)
    p2 = build_identity_preamble(r2, "telegram")
    assert f"entity_id: {_ENTITY_ID}" in p2
    assert "contact_id" not in p2

    # Unknown with temp_entity_id — entity_id shown (preferred over contact_id)
    temp_eid = uuid.uuid4()
    temp_cid = uuid.uuid4()
    p4 = build_identity_preamble(
        None, "telegram", temp_contact_id=temp_cid, temp_entity_id=temp_eid
    )
    assert f"entity_id: {temp_eid}" in p4
    assert "pending disambiguation" in p4

    # Unknown with only temp_contact_id (create_temp_contact fallback)
    temp_id = uuid.uuid4()
    p3 = build_identity_preamble(None, "telegram", temp_contact_id=temp_id)
    assert f"contact_id: {temp_id}" in p3 and "pending disambiguation" in p3

    # Unknown without any temp ID
    p5 = build_identity_preamble(None, "telegram")
    assert "Unknown sender" in p5 and "pending disambiguation" in p5

    # Null name fallback
    r6 = ResolvedContact(contact_id=None, name=None, roles=[], entity_id=None)
    assert "Unknown Contact" in build_identity_preamble(r6, "email")


# ---------------------------------------------------------------------------
# create_temp_contact
# ---------------------------------------------------------------------------


def _make_new_temp_contact_pool(contact_id: uuid.UUID, entity_id: uuid.UUID):
    """Pool mock for create_temp_contact's new-contact path (no existing match).

    entity-v3 (bu-hvrt1) + Phase 7 (bu-jnaa3): create_temp_contact first calls
    resolve_contact_by_channel (pool.fetchrow on entity_facts → None here), then
    acquires a conn to INSERT the entity (conn.fetchval RETURNING id). It no
    longer writes a public.contacts row (the contact object is retired;
    contact_id is always None). It does NOT assert the channel triple — that is
    the routing pipeline's deterministic hook, not this function's job.

    ``contact_id`` is accepted for signature compatibility with the existing
    callers but is unused now that no contacts row is minted.
    """
    pool = MagicMock()
    # resolve_contact_by_channel queries the triple store on the pool → no match.
    pool.fetchrow = AsyncMock(return_value=None)

    conn = AsyncMock()
    # The in-transaction re-resolve issues a SELECT on the triple store → None.
    conn.fetchrow = AsyncMock(return_value=None)
    conn.fetchval = AsyncMock(return_value=entity_id)  # entity INSERT RETURNING id
    conn.execute = AsyncMock()

    mock_transaction = AsyncMock()
    mock_transaction.__aenter__ = AsyncMock(return_value=mock_transaction)
    mock_transaction.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=mock_transaction)

    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool, conn


async def test_create_temp_contact():
    """create_temp_contact mints only an entity and returns it (cut-over path).

    Phase 7 (bu-jnaa3): no public.contacts row is written; contact_id is None.
    """
    entity_id = uuid.uuid4()
    contact_id = uuid.uuid4()
    pool, _conn = _make_new_temp_contact_pool(contact_id, entity_id)

    result = await create_temp_contact(pool, "telegram", "555")

    assert result is not None
    assert result.contact_id is None  # no contacts row minted (entity_id is identity)
    assert result.entity_id == entity_id
    assert result.name == "Unknown (telegram 555)"
    assert result.roles == []


async def test_create_temp_contact_db_error_returns_none():
    """A DB error during creation returns None (graceful degradation)."""
    pool = MagicMock()
    pool.fetchrow = AsyncMock(return_value=None)  # no existing match
    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(side_effect=Exception("connection refused"))
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    assert await create_temp_contact(pool, "telegram", "999") is None


async def test_create_temp_contact_returns_existing_on_conflict():
    """create_temp_contact returns the existing contact if one resolves (race).

    Existing-sender detection now goes through resolve_contact_by_channel (the
    triple store), not a contact_info join.
    """
    existing_entity_id = uuid.uuid4()

    pool = MagicMock()
    # resolve_contact_by_channel: triple lookup returns an active row joined to
    # the owner entity.
    pool.fetchrow = AsyncMock(
        return_value={
            "entity_id": existing_entity_id,
            "name": "Existing Person",
            "roles": ["owner"],
        }
    )

    result = await create_temp_contact(pool, "telegram", "existing-chat")

    assert result is not None
    assert result.contact_id is None  # entity_id is the authoritative key post bead 7
    assert result.entity_id == existing_entity_id
    assert result.name == "Existing Person"
    assert result.roles == ["owner"]


# ---------------------------------------------------------------------------
# create_temp_contact — entity-v3 ownership: NO entity_facts write (bu-hvrt1)
# ---------------------------------------------------------------------------
# entity-v3 reverses the Migration-bead-8 (bu-k9ylx) ownership: create_temp_contact
# no longer asserts the sender's channel triple. Switchboard ingress must never
# write relationship.entity_facts (switchboard-identity invariant). The channel
# triple — the existing-sender dedup key — is now asserted by a deterministic
# post-resolution hook in the routing pipeline
# (relationship.tools.relationship_assert_fact.assert_sender_channel_fact).


class TestCreateTempContactCentralWriter:
    """create_temp_contact mints only the public entity and asserts NO fact."""

    async def test_does_not_call_relationship_assert_fact(self):
        """create_temp_contact must NOT call the central entity_facts writer.

        The channel-triple assertion is the routing pipeline's job (entity-v3,
        bu-hvrt1); minting it here would re-introduce a switchboard-ingress
        entity_facts write.
        """
        contact_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        pool, _conn = _make_new_temp_contact_pool(contact_id, entity_id)

        with patch(_ASSERT_FACT_PATCH, new_callable=AsyncMock) as mock_assert:
            result = await create_temp_contact(pool, "telegram", "12345")

        mock_assert.assert_not_awaited()
        # The public entity is still minted and returned; no contacts row (bu-jnaa3).
        assert result is not None
        assert result.entity_id == entity_id
        assert result.contact_id is None

    async def test_no_contact_info_insert_anywhere(self):
        """No INSERT/UPDATE/DELETE against public.contact_info is ever issued."""
        contact_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        pool, conn = _make_new_temp_contact_pool(contact_id, entity_id)

        await create_temp_contact(pool, "telegram", "12345")

        # Inspect every SQL string passed to conn.execute / conn.fetchrow:
        # none may write public.contact_info.
        calls = [*conn.execute.await_args_list, *conn.fetchrow.await_args_list]
        assert calls, "Expected database calls to be recorded"
        for mock_call in calls:
            sql = mock_call.args[0] if mock_call.args else ""
            assert "contact_info" not in sql.lower(), f"unexpected contact_info SQL: {sql!r}"

    async def test_no_entity_facts_write_dml_anywhere(self):
        """No write-DML against relationship.entity_facts is issued.

        The read-path re-check (resolve_contact_by_channel issues a SELECT/JOIN on
        entity_facts) is legal and expected; only INSERT/UPDATE/DELETE would be a
        switchboard-ingress fact write.
        """
        contact_id = uuid.uuid4()
        entity_id = uuid.uuid4()
        pool, conn = _make_new_temp_contact_pool(contact_id, entity_id)

        await create_temp_contact(pool, "telegram", "12345")

        write_dml = re.compile(
            r"\b(?:insert\s+into|update|delete\s+from)\s+relationship\.entity_facts\b"
        )
        all_calls = [
            *conn.execute.await_args_list,
            *conn.fetchrow.await_args_list,
            *pool.fetchrow.await_args_list,
        ]
        assert all_calls, "Expected database calls to be recorded"
        for mock_call in all_calls:
            sql = (mock_call.args[0] if mock_call.args else "").lower()
            assert not write_dml.search(sql), f"unexpected entity_facts write-DML: {sql!r}"

    async def test_existing_match_short_circuits_before_writes(self):
        """An existing triple match returns immediately without acquiring a conn."""
        existing_entity_id = uuid.uuid4()
        pool = MagicMock()
        pool.fetchrow = AsyncMock(
            return_value={
                "entity_id": existing_entity_id,
                "name": "Existing Person",
                "roles": ["owner"],
            }
        )
        pool.acquire = MagicMock()  # must not be used

        result = await create_temp_contact(pool, "telegram", "777")

        pool.acquire.assert_not_called()
        assert result is not None
        assert result.entity_id == existing_entity_id
        assert result.roles == ["owner"]


# ---------------------------------------------------------------------------
# entity-v3 dedup invariant: 1st-then-2nd message mints exactly one entity
# ---------------------------------------------------------------------------
# This is the safety property the whole replacement-before-removal sequence
# protects: a brand-new sender that messages twice must resolve to a SINGLE
# entity. It only holds if the deterministic pipeline hook
# (assert_sender_channel_fact) writes the channel triple that
# resolve_contact_by_channel reads on the 2nd message. With the triple written by
# the hook (not by create_temp_contact), the 2nd message resolves and never mints
# a duplicate.


def _make_triple_backed_pool(store: dict[tuple[str, str], dict[str, Any]]):
    """A pool whose entity_facts SELECT is backed by an in-memory triple *store*.

    *store* maps ``(predicate, object_value)`` → a row dict
    (``entity_id`` / ``name`` / ``roles``) — exactly the shape
    ``_resolve_entity_by_triple`` returns. ``create_temp_contact``'s entity /
    contact INSERTs are served from the acquired connection; the channel triple
    itself is written by the pipeline hook (mocked into *store* in the test).
    """

    def _lookup(query: str, *args):
        # resolve_contact_by_channel's only fetchrow is the entity_facts join
        # (predicate=$1, object=$2). Serve it from the in-memory store.
        if "entity_facts" in query:
            predicate, object_value = args[0], args[1]
            return store.get((predicate, object_value))
        return None

    pool = MagicMock()
    pool.fetchrow = AsyncMock(side_effect=_lookup)

    conn = AsyncMock()
    conn.fetchrow = AsyncMock(side_effect=_lookup)

    # entity INSERT … RETURNING id mints a fresh entity id each call.
    async def conn_fetchval(query, *args):
        if "INSERT INTO public.entities" in query:
            return uuid.uuid4()
        return None

    conn.fetchval = AsyncMock(side_effect=conn_fetchval)

    async def conn_insert_contact(query, *args):
        if "INSERT INTO public.contacts" in query:
            # args: (name, entity_id, metadata)
            return {"id": uuid.uuid4(), "name": args[0], "entity_id": args[1]}
        # entity_facts SELECT under the txn re-check.
        return _lookup(query, *args)

    conn.fetchrow = AsyncMock(side_effect=conn_insert_contact)
    conn.execute = AsyncMock()

    mock_txn = AsyncMock()
    mock_txn.__aenter__ = AsyncMock(return_value=mock_txn)
    mock_txn.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=mock_txn)

    pool.acquire = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


async def test_second_message_from_new_sender_does_not_mint_duplicate_entity():
    """1st message mints an entity + the hook writes its channel triple; the 2nd
    message resolves to the SAME entity — exactly one entity, no duplicate."""
    from butlers.tools.relationship.relationship_assert_fact import (
        assert_sender_channel_fact,
    )

    channel_type, channel_value = "telegram", "new-sender-42"
    predicate = "has-handle"  # telegram → has-handle
    # The writer normalises telegram handles to the canonical prefixed form, so
    # the dedup triple lands as ``telegram:<bare>`` (bu-oluyt.5).
    stored_value = "telegram:new-sender-42"

    # In-memory triple store shared by the read path (resolve_contact_by_channel)
    # and the deterministic hook's write (mocked relationship_assert_fact).
    store: dict[tuple[str, str], dict[str, Any]] = {}
    pool = _make_triple_backed_pool(store)

    # The hook's central writer records the triple into the shared store, so the
    # next resolve_contact_by_channel finds it (deterministic, no LLM involved).
    async def _record_triple(_pool, subject, pred, obj, **_kwargs):
        store[(pred, obj)] = {"entity_id": subject, "name": None, "roles": []}
        return MagicMock()

    # --- 1st message: unknown sender → mint temp contact, then hook asserts. ---
    first = await create_temp_contact(pool, channel_type, channel_value)
    assert first is not None
    first_entity_id = first.entity_id
    assert first_entity_id is not None
    assert (predicate, stored_value) not in store  # not written by create_temp_contact

    with patch(_ASSERT_FACT_PATCH, new=_record_triple):
        await assert_sender_channel_fact(pool, first_entity_id, channel_type, channel_value)

    # The hook wrote the dedup triple — in canonical prefixed form — keyed to the
    # freshly-minted entity.
    assert (predicate, channel_value) not in store  # never the unprefixed form
    assert store[(predicate, stored_value)]["entity_id"] == first_entity_id

    # --- 2nd message: same sender now resolves; no new entity is minted. ---
    resolved = await resolve_contact_by_channel(pool, channel_type, channel_value)
    assert resolved is not None
    assert resolved.entity_id == first_entity_id

    # create_temp_contact, called again, short-circuits on the existing triple
    # and returns the SAME entity instead of minting a second one.
    second = await create_temp_contact(pool, channel_type, channel_value)
    assert second is not None
    assert second.entity_id == first_entity_id


# ---------------------------------------------------------------------------
# Contract: ingress writes telegram handles in the canonical prefixed form
# (bu-oluyt.5 — Phase 5 of the contact-schema retirement epic)
# ---------------------------------------------------------------------------
# The delivery read path (daemon._resolve_entity_channel_identifier) filters
# has-handle objects on ``LIKE 'telegram:%'``. If assert_sender_channel_fact
# wrote an unprefixed object, an ingress-created telegram contact would be
# NON-deliverable via notify(entity_id). These tests pin the write side to the
# ONE canonical format so the read-side prefix tolerance (PR #2465) is no longer
# load-bearing.


class TestAssertSenderChannelFactPrefixesTelegram:
    """assert_sender_channel_fact stores telegram has-handle objects prefixed."""

    @pytest.mark.parametrize(
        ("channel_type", "raw_value", "expected_object"),
        [
            ("telegram", "206570151", "telegram:206570151"),
            ("telegram_user_client", "206570151", "telegram:206570151"),
            ("telegram_user_id", "206570151", "telegram:206570151"),
            ("telegram_bot", "12345", "telegram:12345"),
            ("telegram_chat_id", "-1001234", "telegram:-1001234"),
            ("telegram_username", "@Tzeusy", "telegram:Tzeusy"),
            # Already-prefixed input must not be double-prefixed (idempotent).
            ("telegram", "telegram:206570151", "telegram:206570151"),
        ],
    )
    async def test_telegram_objects_are_written_prefixed(
        self, channel_type: str, raw_value: str, expected_object: str
    ) -> None:
        from butlers.tools.relationship.relationship_assert_fact import (
            assert_sender_channel_fact,
        )

        entity_id = uuid.uuid4()
        pool = MagicMock()

        with patch(_ASSERT_FACT_PATCH, new_callable=AsyncMock) as mock_assert:
            await assert_sender_channel_fact(pool, entity_id, channel_type, raw_value)

        # Exactly-once fact-write contract: a duplicate write would slip past the
        # await_args unpacking below (which only inspects the LAST call).
        mock_assert.assert_awaited_once()
        # Central writer signature: (pool, subject, predicate, object, ...)
        _pool, subject, predicate, obj = mock_assert.await_args.args
        assert subject == entity_id
        assert predicate == "has-handle"
        assert obj == expected_object
        assert obj.startswith("telegram:")

    async def test_non_telegram_handle_is_not_prefixed(self) -> None:
        """A non-telegram channel value is passed through verbatim (no telegram: prefix)."""
        from butlers.tools.relationship.relationship_assert_fact import (
            assert_sender_channel_fact,
        )

        entity_id = uuid.uuid4()
        pool = MagicMock()

        with patch(_ASSERT_FACT_PATCH, new_callable=AsyncMock) as mock_assert:
            await assert_sender_channel_fact(pool, entity_id, "email", "a@b.com")

        # Exactly-once fact-write contract (a duplicate write would be masked by
        # the await_args unpacking, which only sees the LAST call).
        mock_assert.assert_awaited_once()
        _pool, _subject, predicate, obj = mock_assert.await_args.args
        assert predicate == "has-email"
        assert obj == "a@b.com"
        assert not obj.startswith("telegram:")


# ---------------------------------------------------------------------------
# Telegram username normalization — bu-c4f7f
# ---------------------------------------------------------------------------


class TestChannelValueForStorage:
    """channel_value_for_storage normalises telegram channel values on WRITE.

    This is the write-side counterpart of the read-side telegram-prefix fallback
    in resolve_contact_by_channel, so that storage, resolution, and delivery all
    agree on the canonical ``telegram:<bare>`` form (bu-oluyt.3 / Phase 5).
    """

    def test_telegram_username_is_prefixed_and_at_stripped(self) -> None:
        assert channel_value_for_storage("telegram", "@Tzeusy") == "telegram:Tzeusy"

    def test_telegram_numeric_chat_id_is_prefixed(self) -> None:
        assert channel_value_for_storage("telegram_chat_id", "206570151") == "telegram:206570151"

    def test_telegram_value_is_idempotent(self) -> None:
        assert channel_value_for_storage("telegram", "telegram:206570151") == "telegram:206570151"

    def test_email_is_unchanged(self) -> None:
        assert channel_value_for_storage("email", "a@b.com") == "a@b.com"

    def test_unknown_channel_type_is_unchanged(self) -> None:
        # Empty string / non-telegram types pass through verbatim (the has-*
        # predicate alone can't distinguish a telegram handle from another handle).
        assert channel_value_for_storage("", "somehandle") == "somehandle"
        assert channel_value_for_storage("linkedin", "in/jane") == "in/jane"


class TestTelegramUsernameCandidates:
    """_telegram_username_candidates generates normalised variants in order.

    The canonical storage form (contacts backfill) strips the leading '@'.
    The outbound tool (telegram_send_message) may supply '@Username'.
    Telegram usernames are case-insensitive on the platform.
    """

    def test_at_prefixed_input_produces_bare_as_second_candidate(self) -> None:
        """'@Tzeusy' → first exact, then bare 'Tzeusy', then lowercase variants."""
        candidates = _telegram_username_candidates("@Tzeusy")
        assert candidates[0] == "@Tzeusy"  # exact first
        assert "Tzeusy" in candidates  # @-stripped
        assert "@tzeusy" in candidates  # lowercase with @
        assert "tzeusy" in candidates  # lowercase bare

    def test_bare_input_produces_at_prefixed_as_variant(self) -> None:
        """'Tzeusy' → first exact, then '@Tzeusy', then lowercase variants."""
        candidates = _telegram_username_candidates("Tzeusy")
        assert candidates[0] == "Tzeusy"
        assert "@Tzeusy" in candidates
        assert "tzeusy" in candidates

    def test_numeric_chat_id_is_first_and_only_unique_candidate(self) -> None:
        """A numeric chat id (e.g. '206570151') should yield ONLY itself — no @-prefix."""
        candidates = _telegram_username_candidates("206570151")
        # Numeric chat IDs are not usernames; they must NOT expand to @-prefix variants
        # so that resolve_contact_by_channel makes exactly one DB query and doesn't
        # spuriously try '@206570151'.
        assert candidates == ["206570151"]
        assert "@206570151" not in candidates

    def test_negative_numeric_chat_id_is_exact_match_only(self) -> None:
        """Negative numeric chat IDs (group/supergroup) must also expand to only themselves."""
        candidates = _telegram_username_candidates("-1001234567")
        assert candidates == ["-1001234567"]
        assert "@-1001234567" not in candidates

    def test_no_duplicates(self) -> None:
        """Candidate list must contain no duplicates."""
        for value in ("@Tzeusy", "Tzeusy", "tzeusy", "206570151"):
            candidates = _telegram_username_candidates(value)
            assert len(candidates) == len(set(candidates)), (
                f"Duplicates in candidates for {value!r}: {candidates}"
            )

    def test_lowercase_input_does_not_produce_duplicates(self) -> None:
        """Already-lowercase input: '@tzeusy' normalised candidates have no dupes."""
        candidates = _telegram_username_candidates("@tzeusy")
        assert len(candidates) == len(set(candidates))
        assert "tzeusy" in candidates


class TestTelegramUsernameResolutionNormalization:
    """resolve_contact_by_channel normalises Telegram username @-prefix and case.

    Regression for bu-c4f7f: telegram_send_message with chat_id='@Tzeusy' must
    resolve when the stored entity_facts triple uses bare 'Tzeusy'.
    """

    def _make_pool_for_telegram_storage(
        self,
        stored_value: str,
        entity_id: uuid.UUID,
        name: str = "Owner",
        roles: list[str] | None = None,
    ) -> AsyncMock:
        """Pool that returns an entity row only for the stored (canonical) value.

        Simulates how the DB behaves: exact-match on ``object = $2`` succeeds
        only for the stored canonical value; all other variants return None.
        """
        row = MagicMock()
        row.__getitem__ = lambda self, k: {
            "entity_id": entity_id,
            "name": name,
            "roles": roles or ["owner"],
        }[k]

        def _fetchrow(query: str, predicate: str, value: str) -> MagicMock | None:
            if "entity_facts" in query and value.lower() == stored_value.lower():
                return row
            return None

        pool = AsyncMock()
        pool.fetchrow = AsyncMock(side_effect=_fetchrow)
        return pool

    async def test_at_prefixed_resolves_when_stored_without_at(self) -> None:
        """'@Tzeusy' resolves when stored as 'Tzeusy' (canonical backfill form)."""
        eid = uuid.uuid4()
        pool = self._make_pool_for_telegram_storage("Tzeusy", eid, roles=["owner"])

        result = await resolve_contact_by_channel(pool, "telegram", "@Tzeusy")

        assert result is not None, "@-prefixed username must resolve to stored bare form"
        assert result.entity_id == eid
        assert result.roles == ["owner"]
        assert result.contact_id is None

    async def test_at_prefixed_uppercase_resolves_case_insensitively(self) -> None:
        """'@TZEUSY' resolves when stored as 'Tzeusy' (case-insensitive)."""
        eid = uuid.uuid4()
        pool = self._make_pool_for_telegram_storage("Tzeusy", eid, roles=["owner"])

        result = await resolve_contact_by_channel(pool, "telegram", "@TZEUSY")

        assert result is not None, "@-prefixed uppercase username must resolve"
        assert result.entity_id == eid

    async def test_bare_username_resolves_directly(self) -> None:
        """'Tzeusy' (no @) resolves on the first exact-match attempt."""
        eid = uuid.uuid4()
        pool = self._make_pool_for_telegram_storage("Tzeusy", eid)

        result = await resolve_contact_by_channel(pool, "telegram", "Tzeusy")

        assert result is not None
        assert result.entity_id == eid

    async def test_numeric_chat_id_resolves_directly(self) -> None:
        """A numeric chat_id (e.g. '206570151') resolves on exact-match, not via username path."""
        eid = uuid.uuid4()
        # Numeric IDs are stored and queried exactly; the username normalisation loop
        # does not interfere because the exact-match succeeds on the first try.
        stored = "206570151"
        row = MagicMock()
        row.__getitem__ = lambda self, k: {
            "entity_id": eid,
            "name": "Owner",
            "roles": ["owner"],
        }[k]
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=row)

        result = await resolve_contact_by_channel(pool, "telegram", stored)

        assert result is not None
        assert result.entity_id == eid
        # Exactly one fetchrow call — hit on first exact attempt
        assert pool.fetchrow.await_count == 1

    async def test_telegram_username_channel_type_normalizes_too(self) -> None:
        """channel_type='telegram_username' also applies @-prefix normalization."""
        eid = uuid.uuid4()
        pool = self._make_pool_for_telegram_storage("tzeusy", eid, roles=["owner"])

        result = await resolve_contact_by_channel(pool, "telegram_username", "@Tzeusy")

        assert result is not None, "telegram_username channel type must normalize"
        assert result.entity_id == eid

    async def test_unknown_username_returns_none(self) -> None:
        """An @-prefixed username with no matching entity returns None."""
        pool = AsyncMock()
        pool.fetchrow = AsyncMock(return_value=None)

        result = await resolve_contact_by_channel(pool, "telegram", "@nobody")

        assert result is None
