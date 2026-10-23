"""bu-q7vx1q.76: a gap born answerable from a Relationship entity fact carries its stored authority.

``_existing_answer`` used to stamp ``authority None`` on the ``relationship.entity_facts`` branch,
so an owner-confirmed fact's notice read "Reported by a third party, not verified". It now reads the
row's stored authority the way the closure path does: confirmed -> owner (with the confirmer),
otherwise ``content_authority``, with NULL failing closed to ``third_party``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

from butlers.modules.memory import knowledge_gaps

_FACT = uuid.UUID("00000000-0000-0000-0000-0000000000f1")
_ENTITY = uuid.UUID("00000000-0000-0000-0000-0000000000e1")
_CONFIRMER = uuid.UUID("00000000-0000-0000-0000-0000000000c1")
_SENDER = uuid.UUID("00000000-0000-0000-0000-0000000000d1")


def _conn(authority_row: dict | None) -> AsyncMock:
    """facts miss, entity_facts hit, then the stored-authority read."""
    conn = AsyncMock()
    conn.fetchrow = AsyncMock(
        side_effect=[None, {"id": _FACT, "object": "Sample value"}, authority_row]
    )
    return conn


def _stored(**overrides) -> dict:
    row = {
        "content_authority": None,
        "authority_entity_id": None,
        "confirmed_at": None,
        "confirmed_by_entity_id": None,
    }
    row.update(overrides)
    return row


async def _answer(authority_row: dict | None):
    return await knowledge_gaps._existing_answer(
        _conn(authority_row), "relationship", _ENTITY, "has-email"
    )


async def test_an_owner_confirmed_fact_is_born_owner_authored():
    answer = await _answer(
        _stored(
            content_authority="third_party",
            authority_entity_id=_SENDER,
            confirmed_at="2026-10-01T00:00:00+00:00",
            confirmed_by_entity_id=_CONFIRMER,
        )
    )

    assert answer["ref"] == f"entity_fact:{_FACT}"
    assert (answer["authority"], answer["authority_entity_id"]) == ("owner", _CONFIRMER)


async def test_a_confirmed_fact_notice_reads_now_known():
    answer = await _answer(_stored(confirmed_at="2026-10-01T00:00:00+00:00"))
    gap = {
        "question_summary": "Sample question",
        "answered_value": answer["value"],
        "answered_by_ref": answer["ref"],
        "answered_authority": answer["authority"],
        "answered_authority_entity_id": answer["authority_entity_id"],
    }

    notice = await knowledge_gaps._notice_text(AsyncMock(), gap, datetime(2026, 10, 2, tzinfo=UTC))

    assert "Now known: Sample value" in notice


async def test_an_unconfirmed_third_party_fact_stays_reported():
    answer = await _answer(_stored(content_authority="third_party", authority_entity_id=_SENDER))

    assert (answer["authority"], answer["authority_entity_id"]) == ("third_party", _SENDER)


async def test_a_legacy_null_authority_fails_closed_to_third_party():
    answer = await _answer(_stored())

    assert (answer["authority"], answer["authority_entity_id"]) == ("third_party", None)


async def test_a_row_that_stopped_being_active_leaves_the_gap_open():
    assert await _answer(None) is None
