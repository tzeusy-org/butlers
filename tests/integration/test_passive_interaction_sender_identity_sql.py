"""Real-Postgres regression for passive-interaction sender extraction.

The two riskiest parts of the batch-sender fix are DB-shaped and structurally
unreachable from mocked-pool tests:

- the ``CROSS JOIN LATERAL`` unnest in ``interaction_sync``'s inbox query,
  which fans the FROM clause out to one row per (message x sender) and so can
  silently inflate the ``message_count`` written into permanent fact metadata
  (``openspec/specs/passive-interaction-sync/spec.md`` fixes its shape); and
- the digits-normalised ``has-phone`` comparison in
  ``identity._resolve_entity_by_phone_digits``, whose whole purpose is to
  tolerate the inconsistent formats real contacts are stored in.

Both are exercised here against a fully migrated Postgres.
"""

from __future__ import annotations

import json
import shutil
import uuid
from datetime import UTC, datetime

import asyncpg
import pytest
import pytest_asyncio

from butlers.identity import _resolve_entity_by_phone_digits

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
]


@pytest.fixture(scope="module")
def migrated_dsn(postgres_container) -> str:
    """Provision a database carrying the core, switchboard and relationship chains.

    ``message_inbox`` lives in the switchboard chain and ``entity_facts`` in the
    relationship one, so the core-only fixture cannot reach either.  The chains
    place ``message_inbox`` in ``public`` here, so it is referenced unqualified
    — this asserts SQL semantics (fan-out safety), not schema placement.
    """
    from butlers.testing.migration import create_migrated_test_db, migration_db_name

    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core", "switchboard", "relationship"],
    )


@pytest_asyncio.fixture(loop_scope="function")
async def pool(migrated_dsn: str):
    """A pool over the migrated database, truncated between tests."""
    pg_pool = await asyncpg.create_pool(migrated_dsn, min_size=1, max_size=4)
    assert pg_pool is not None
    try:
        await pg_pool.execute("TRUNCATE message_inbox")
        await pg_pool.execute("TRUNCATE relationship.entity_facts CASCADE")
        await pg_pool.execute("TRUNCATE public.entities CASCADE")
        yield pg_pool
    finally:
        await pg_pool.close()


# Mirrors the grouping/extraction half of run_interaction_sync's inbox query.
# Kept in sync deliberately: this asserts the SQL semantics (fan-out safety),
# not the job's Python control flow.
INBOX_GROUP_SQL = """
SELECT
    COALESCE(
        request_context ->> 'source_thread_identity',
        request_context ->> 'source_sender_identity'
    )                                                AS thread_identity,
    request_context ->> 'source_channel'             AS source_channel,
    (received_at AT TIME ZONE 'UTC')::date           AS interaction_date,
    array_agg(DISTINCT sender.identity)              AS sender_identities,
    MAX(request_context ->> 'owner_sender_identity') AS owner_sender_identity,
    COUNT(DISTINCT (id, received_at))                AS message_count,
    MAX(
        CASE
            WHEN request_context ->> 'participant_count' IS NOT NULL
            THEN (request_context ->> 'participant_count')::int
            ELSE NULL
        END
    )                                                AS participant_count
FROM message_inbox
CROSS JOIN LATERAL (
    SELECT COALESCE(
        (
            SELECT array_agg(value)
            FROM jsonb_array_elements_text(
                request_context -> 'source_sender_identities'
            ) AS t(value)
        ),
        ARRAY[request_context ->> 'source_sender_identity']
    ) AS identities
) AS batch
CROSS JOIN LATERAL unnest(batch.identities) AS sender(identity)
WHERE direction = 'inbound'
  AND request_context ->> 'source_channel' = ANY($1::text[])
  AND sender.identity IS NOT NULL
  AND sender.identity NOT IN ('unknown', 'multiple')
  AND COALESCE(request_context ->> 'interaction_eligible', 'true') != 'false'
GROUP BY
    COALESCE(
        request_context ->> 'source_thread_identity',
        request_context ->> 'source_sender_identity'
    ),
    request_context ->> 'source_channel',
    (received_at AT TIME ZONE 'UTC')::date
"""


async def _insert_inbox(pool, *, received_at: datetime, request_context: dict) -> None:
    # Mirrors production ingestion: message_inbox is range-partitioned.
    await pool.execute("SELECT switchboard_message_inbox_ensure_partition($1)", received_at)
    await pool.execute(
        """
        INSERT INTO message_inbox
            (id, received_at, request_context, raw_payload, normalized_text, direction)
        VALUES ($1, $2, $3::jsonb, '{}'::jsonb, 'hi', 'inbound')
        """,
        uuid.uuid4(),
        received_at,
        json.dumps(request_context),
    )


def _ctx(**overrides) -> dict:
    ctx = {
        "source_channel": "whatsapp_user_client",
        "source_endpoint_identity": "whatsapp:+6591153887",
        "source_sender_identity": "multiple",
        "source_thread_identity": "6598150802-1386556114@g.us",
    }
    ctx.update(overrides)
    return ctx


async def _seed_entity(pool, *, name: str, phone: str) -> uuid.UUID:
    entity_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO public.entities (id, canonical_name, entity_type) VALUES ($1, $2, 'person')",
        entity_id,
        name,
    )
    await pool.execute(
        """
        INSERT INTO relationship.entity_facts
            (subject, predicate, object, object_kind, validity, src)
        VALUES ($1, 'has-phone', $2, 'literal', 'active', 'interaction_sync')
        """,
        entity_id,
        phone,
    )
    return entity_id


class TestInboxSenderFanOut:
    async def test_batch_row_yields_every_sender_without_inflating_count(self, pool) -> None:
        """Three senders on one message must not report three messages."""
        await _insert_inbox(
            pool,
            received_at=datetime(2026, 8, 1, 12, 50, tzinfo=UTC),
            request_context=_ctx(
                source_sender_identities=[
                    "6598150802@s.whatsapp.net",
                    "6590462306@s.whatsapp.net",
                    "6597881300@s.whatsapp.net",
                ],
                owner_sender_identity="6591153887@s.whatsapp.net",
                participant_count=4,
            ),
        )

        rows = await pool.fetch(INBOX_GROUP_SQL, ["whatsapp_user_client"])

        assert len(rows) == 1
        row = rows[0]
        assert sorted(row["sender_identities"]) == [
            "6590462306@s.whatsapp.net",
            "6597881300@s.whatsapp.net",
            "6598150802@s.whatsapp.net",
        ]
        # The regression: COUNT(*) over the fanned-out FROM would say 3.
        assert row["message_count"] == 1
        assert row["participant_count"] == 4
        assert row["owner_sender_identity"] == "6591153887@s.whatsapp.net"

    async def test_message_count_counts_messages_not_sender_pairs(self, pool) -> None:
        for hour in (10, 11):
            await _insert_inbox(
                pool,
                received_at=datetime(2026, 8, 1, hour, 0, tzinfo=UTC),
                request_context=_ctx(
                    source_sender_identities=[
                        "6598150802@s.whatsapp.net",
                        "6590462306@s.whatsapp.net",
                    ]
                ),
            )

        rows = await pool.fetch(INBOX_GROUP_SQL, ["whatsapp_user_client"])

        assert len(rows) == 1
        # 2 messages x 2 senders = 4 fanned-out rows, but only 2 messages.
        assert rows[0]["message_count"] == 2
        assert len(rows[0]["sender_identities"]) == 2

    async def test_single_message_envelope_falls_back_to_scalar(self, pool) -> None:
        """Back-compat: envelopes without the list keep resolving as before."""
        await _insert_inbox(
            pool,
            received_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            request_context=_ctx(
                source_sender_identity="alice@example.com",
                source_channel="email",
                source_thread_identity="thread-1",
            ),
        )

        rows = await pool.fetch(INBOX_GROUP_SQL, ["email"])

        assert len(rows) == 1
        assert rows[0]["sender_identities"] == ["alice@example.com"]
        assert rows[0]["message_count"] == 1

    async def test_collapsed_sentinel_is_never_treated_as_a_sender(self, pool) -> None:
        """A legacy batch row (no participant list) must drop out entirely."""
        await _insert_inbox(
            pool,
            received_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            request_context=_ctx(),  # source_sender_identity == "multiple"
        )

        rows = await pool.fetch(INBOX_GROUP_SQL, ["whatsapp_user_client"])

        assert rows == []

    async def test_interaction_ineligible_rows_are_excluded(self, pool) -> None:
        await _insert_inbox(
            pool,
            received_at=datetime(2026, 8, 1, 12, 0, tzinfo=UTC),
            request_context=_ctx(
                source_sender_identities=["6598150802@s.whatsapp.net"],
                interaction_eligible="false",
            ),
        )

        rows = await pool.fetch(INBOX_GROUP_SQL, ["whatsapp_user_client"])

        assert rows == []


class TestPhoneDigitsResolution:
    @pytest.mark.parametrize(
        "stored",
        ["+65 9815 0802", "+6598150802", "6598150802", "+65-9815-0802"],
    )
    async def test_jid_phone_matches_any_stored_format(self, pool, stored: str) -> None:
        expected = await _seed_entity(pool, name="Yeo Lay Ha", phone=stored)

        row = await _resolve_entity_by_phone_digits(pool, "6598150802")

        assert row is not None, f"stored format {stored!r} failed to match"
        assert row["entity_id"] == expected

    async def test_local_number_matches_full_e164(self, pool) -> None:
        """Contacts stored without a country code still bridge to a JID."""
        expected = await _seed_entity(pool, name="Owner", phone="91153887")

        row = await _resolve_entity_by_phone_digits(pool, "6591153887")

        assert row is not None
        assert row["entity_id"] == expected

    async def test_one_entity_stored_twice_is_not_ambiguous(self, pool) -> None:
        """Two formats of the same number are one person, not a conflict."""
        entity_id = await _seed_entity(pool, name="Owner", phone="+6591153887")
        await pool.execute(
            """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, validity, src)
            VALUES ($1, 'has-phone', '91153887', 'literal', 'active', 'interaction_sync')
            """,
            entity_id,
        )

        row = await _resolve_entity_by_phone_digits(pool, "6591153887")

        assert row is not None
        assert row["entity_id"] == entity_id

    async def test_two_entities_resolve_to_nobody(self, pool) -> None:
        """Misattributing a permanent fact is worse than recording none."""
        await _seed_entity(pool, name="A", phone="+6598150802")
        await _seed_entity(pool, name="B", phone="98150802")

        assert await _resolve_entity_by_phone_digits(pool, "6598150802") is None

    async def test_different_country_does_not_match(self, pool) -> None:
        """An unbounded suffix would let +60 3 9115 3887 match 91153887.

        Pure digits cannot distinguish a country code from a longer national
        number, so the delta cap resolves the ambiguity by declining to match.
        """
        await _seed_entity(pool, name="Wrong Country", phone="+60391153887")

        assert await _resolve_entity_by_phone_digits(pool, "91153887") is None

    async def test_two_digit_country_code_still_bridges(self, pool) -> None:
        """The cap must not break the owner's own locale (+65)."""
        expected = await _seed_entity(pool, name="SG Local", phone="98150802")

        row = await _resolve_entity_by_phone_digits(pool, "6598150802")

        assert row is not None
        assert row["entity_id"] == expected

    async def test_short_number_is_rejected(self, pool) -> None:
        await _seed_entity(pool, name="Short", phone="1234567")

        assert await _resolve_entity_by_phone_digits(pool, "1234567") is None

    async def test_retracted_fact_does_not_match(self, pool) -> None:
        entity_id = uuid.uuid4()
        await pool.execute(
            "INSERT INTO public.entities (id, canonical_name, entity_type) "
            "VALUES ($1, 'Gone', 'person')",
            entity_id,
        )
        await pool.execute(
            """
            INSERT INTO relationship.entity_facts
                (subject, predicate, object, object_kind, validity, src)
            VALUES ($1, 'has-phone', '+6598150802', 'literal', 'retracted', 'interaction_sync')
            """,
            entity_id,
        )

        assert await _resolve_entity_by_phone_digits(pool, "6598150802") is None
