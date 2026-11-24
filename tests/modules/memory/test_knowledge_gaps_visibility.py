"""Focused controls for generated-fact visibility in knowledge-gap matching."""

from __future__ import annotations

import uuid

import pytest

from butlers.modules.memory.knowledge_gaps import _existing_answer

pytestmark = pytest.mark.unit


class _FactConnection:
    def __init__(self, *, generated: bool) -> None:
        self.generated = generated
        self.query = ""

    async def fetchval(self, query: str, *_args):
        assert "to_regclass('chronicler_mem.facts_readable')" in query
        return True

    async def fetchrow(self, query: str, *_args):
        self.query = query
        if self.generated and "facts_readable" in query:
            return None
        return {
            "id": uuid.UUID("11111111-2222-3333-4444-555555555555"),
            "content": "quiet run",
            "content_authority": "system",
            "authority_entity_id": None,
        }


async def test_generated_fact_does_not_make_knowledge_gap_answerable() -> None:
    conn = _FactConnection(generated=True)

    answer = await _existing_answer(
        conn,
        "chronicler_mem",
        uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        "sleep-debt",
    )

    assert answer is None
    assert "facts_readable" in conn.query


async def test_ordinary_fact_remains_a_knowledge_gap_answer() -> None:
    conn = _FactConnection(generated=False)

    answer = await _existing_answer(
        conn,
        "chronicler_mem",
        uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        "home_city",
    )

    assert answer is not None
    assert answer["value"] == "quiet run"
    assert "facts_readable" in conn.query
