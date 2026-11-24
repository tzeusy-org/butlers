from __future__ import annotations

import uuid

import pytest

from butlers.core.sessions import session_complete, session_create
from butlers.testing.fake_pool import fake_pool
from butlers.testing.records import row


@pytest.mark.asyncio
async def test_session_create_strips_untranslatable_prompt_chars() -> None:
    pool = fake_pool(fetchval=uuid.uuid4(), execute="SELECT 1")

    await session_create(
        pool,
        prompt="hello\x00\ud83dworld",
        trigger_source="tick",
        request_id=str(uuid.uuid4()),
    )

    _query, *args = pool.fetchval.await_args.args
    assert args[0] == "helloworld"


@pytest.mark.asyncio
async def test_session_complete_sanitizes_jsonb_and_text_payloads() -> None:
    session_id = uuid.uuid4()
    pool = fake_pool(fetchrow=row(id=session_id, model=None), execute="INSERT 0 1")

    await session_complete(
        pool,
        session_id,
        output="done\x00\ud83d",
        tool_calls=[
            {
                "name": "tool\x00\ud83d",
                "arguments": {"value": "bad\x00\ud83dtext", "items": ["ok", "\x00\ud83d"]},
            }
        ],
        duration_ms=12,
        success=False,
        error="boom\x00\ud83d",
        cost={"raw": "cost\x00\ud83d"},
    )

    _query, *args = pool.fetchrow.await_args.args
    assert args[1] == "done"
    # tool_calls and cost are passed as Python objects (asyncpg JSONB codec handles encoding)
    assert args[2] == [
        {
            "name": "tool",
            "arguments": {"value": "badtext", "items": ["ok", ""]},
        }
    ]
    assert args[4] == {"raw": "cost"}
    assert args[6] == "boom"
