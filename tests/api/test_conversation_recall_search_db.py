"""DB-level acceptance tests for conversation_recall's data layer (bu-0ynlk.9).

Runs ``message_search`` / ``message_thread_window`` (src/butlers/api/conversations.py)
against a real core-migrated PostgreSQL so the tsvector ranking, cross-butler
scoping, cursor stability, and highlight-range extraction are all exercised
end to end, plus the ``GET /api/conversations/messages/search`` router
endpoint that shares the same data layer.

Covers the bead's acceptance criteria 1 and 3:
1. conversation_recall's data layer returns hits ranked across butlers, with
   a snippet containing the term and a deep_link into an allowlisted route;
   an empty query returns [].
3. The router endpoint paginates by cursor per response-conventions, with a
   next_cursor stable across an insert between pages, and highlight ranges.
"""

from __future__ import annotations

import shutil
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.app import create_app
from butlers.api.conversations import message_search, message_thread_window
from butlers.api.db import DatabaseManager
from butlers.api.routers.conversations import _get_db_manager
from butlers.db import register_jsonb_codec
from butlers.testing.migration import create_migrated_test_db, migration_db_name

docker_available = shutil.which("docker") is not None
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not docker_available, reason="Docker not available"),
    pytest.mark.asyncio(loop_scope="session"),
]

SEARCH_PATH = "/api/conversations/messages/search"
BASE_URL = "http://test"


@pytest.fixture(scope="module")
def migrated_db_url(postgres_container) -> str:
    return create_migrated_test_db(
        postgres_container,
        migration_db_name(),
        chains=["core"],
    )


@pytest.fixture
async def pool(postgres_container, migrated_db_url: str):
    p = await asyncpg.create_pool(
        migrated_db_url,
        min_size=1,
        max_size=3,
        init=register_jsonb_codec,
    )
    await p.execute("TRUNCATE TABLE public.dashboard_conversations CASCADE")
    yield p
    await p.close()


@pytest.fixture
def search_app(pool: asyncpg.Pool) -> FastAPI:
    from unittest.mock import MagicMock

    mock_db = MagicMock(spec=DatabaseManager)
    mock_db.credential_shared_pool.return_value = pool

    application = create_app()
    application.dependency_overrides[_get_db_manager] = lambda: mock_db
    return application


async def _seed_conversation(
    pool: asyncpg.Pool, *, butler_name: str, source_channel: str = "dashboard"
) -> uuid.UUID:
    conv_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.dashboard_conversations
            (id, butler_name, title, status, created_at, updated_at, message_count, source_channel)
        VALUES ($1, $2, 'test', 'active', now(), now(), 0, $3)
        """,
        conv_id,
        butler_name,
        source_channel,
    )
    return conv_id


async def _seed_message(
    pool: asyncpg.Pool,
    *,
    conversation_id: uuid.UUID,
    content: str,
    role: str = "user",
    created_at: datetime | None = None,
    session_id: uuid.UUID | None = None,
) -> uuid.UUID:
    msg_id = uuid.uuid4()
    await pool.execute(
        """
        INSERT INTO public.dashboard_messages (id, conversation_id, role, content, created_at, session_id)
        VALUES ($1, $2, $3, $4, COALESCE($5, now()), $6)
        """,
        msg_id,
        conversation_id,
        role,
        content,
        created_at,
        session_id,
    )
    return msg_id


# ---------------------------------------------------------------------------
# 1. Ranked hits across butlers, snippet + deep_link, empty query -> []
# ---------------------------------------------------------------------------


async def test_message_search_returns_hits_across_butlers_ranked(pool: asyncpg.Pool) -> None:
    now = datetime.now(UTC)
    finance_conv = await _seed_conversation(pool, butler_name="finance")
    home_conv = await _seed_conversation(pool, butler_name="home")

    await _seed_message(
        pool,
        conversation_id=finance_conv,
        content="Remind me to email the landlord about rent",
        created_at=now - timedelta(days=6),
    )
    session_id = uuid.uuid4()
    await _seed_message(
        pool,
        conversation_id=home_conv,
        content="Landlord landlord landlord said the lease renews in March",
        role="assistant",
        created_at=now - timedelta(days=5),
        session_id=session_id,
    )
    await _seed_message(
        pool, conversation_id=finance_conv, content="unrelated message about groceries"
    )

    result = await message_search(pool, query="landlord", since=now - timedelta(days=7))

    assert result["items"], "expected at least one hit"
    butler_names = {item["butler_name"] for item in result["items"]}
    assert butler_names == {"finance", "home"}

    # Higher term frequency (3x "landlord") ranks first.
    assert result["items"][0]["butler_name"] == "home"
    assert "landlord" in result["items"][0]["snippet"].lower()
    assert result["items"][0]["deep_link"] == f"/sessions/{session_id}"

    finance_item = next(item for item in result["items"] if item["butler_name"] == "finance")
    assert finance_item["deep_link"] == "/butlers/finance"
    assert finance_item["session_id"] is None


async def test_message_search_empty_query_returns_empty(pool: asyncpg.Pool) -> None:
    result = await message_search(pool, query="   ")
    assert result == {"items": [], "next_cursor": None, "has_more": False}


async def test_message_search_no_match_returns_empty(pool: asyncpg.Pool) -> None:
    conv = await _seed_conversation(pool, butler_name="finance")
    await _seed_message(pool, conversation_id=conv, content="hello world")

    result = await message_search(pool, query="zzz-no-such-term")
    assert result["items"] == []
    assert result["has_more"] is False


async def test_message_search_query_too_long_raises(pool: asyncpg.Pool) -> None:
    with pytest.raises(ValueError, match="512"):
        await message_search(pool, query="x" * 513)


async def test_message_search_filters_by_since_and_channel(pool: asyncpg.Pool) -> None:
    now = datetime.now(UTC)
    dashboard_conv = await _seed_conversation(
        pool, butler_name="finance", source_channel="dashboard"
    )
    telegram_conv = await _seed_conversation(pool, butler_name="finance", source_channel="telegram")

    await _seed_message(
        pool,
        conversation_id=dashboard_conv,
        content="budget review",
        created_at=now - timedelta(days=1),
    )
    await _seed_message(
        pool,
        conversation_id=telegram_conv,
        content="budget check",
        created_at=now - timedelta(days=1),
    )
    await _seed_message(
        pool,
        conversation_id=dashboard_conv,
        content="old budget note",
        created_at=now - timedelta(days=30),
    )

    result = await message_search(
        pool, query="budget", since=now - timedelta(days=7), channel="dashboard"
    )
    assert len(result["items"]) == 1
    assert result["items"][0]["snippet"].lower().startswith("budget review") or (
        "budget review" in result["items"][0]["snippet"].lower()
    )


# ---------------------------------------------------------------------------
# Cursor pagination stability under a concurrent insert
# ---------------------------------------------------------------------------


async def test_message_search_cursor_stable_across_insert(pool: asyncpg.Pool) -> None:
    now = datetime.now(UTC)
    conv = await _seed_conversation(pool, butler_name="finance")

    ids = []
    for i in range(3):
        msg_id = await _seed_message(
            pool,
            conversation_id=conv,
            content=f"widget report number {i}",
            created_at=now - timedelta(minutes=10 - i),
        )
        ids.append(msg_id)

    page1 = await message_search(pool, query="widget", limit=1)
    assert page1["has_more"] is True
    assert page1["next_cursor"] is not None
    page1_ids = {item["message_id"] for item in page1["items"]}

    # A new matching row is inserted "between" page fetches.
    await _seed_message(
        pool, conversation_id=conv, content="widget report number 99", created_at=now
    )

    page2 = await message_search(pool, query="widget", limit=10, cursor=page1["next_cursor"])
    page2_ids = {item["message_id"] for item in page2["items"]}

    # No overlap and no duplicate: the cursor seeks from actual returned
    # values, so the newly inserted row lands at its own sorted position
    # without corrupting the already-issued page.
    assert page1_ids.isdisjoint(page2_ids)


# ---------------------------------------------------------------------------
# message_thread_window
# ---------------------------------------------------------------------------


async def test_message_thread_window_centers_on_anchor(pool: asyncpg.Pool) -> None:
    now = datetime.now(UTC)
    conv = await _seed_conversation(pool, butler_name="finance")
    ids = []
    for i in range(12):
        msg_id = await _seed_message(
            pool,
            conversation_id=conv,
            content=f"message {i}",
            created_at=now - timedelta(minutes=100 - i),
        )
        ids.append(msg_id)

    anchor = ids[6]
    window = await message_thread_window(pool, conv, around_message_id=anchor)

    window_ids = [m["id"] for m in window]
    assert anchor in window_ids
    assert window_ids == ids[1:12]  # 5 before + anchor + 5 after


async def test_message_thread_window_unknown_anchor_returns_empty(pool: asyncpg.Pool) -> None:
    conv = await _seed_conversation(pool, butler_name="finance")
    await _seed_message(pool, conversation_id=conv, content="hi")

    window = await message_thread_window(pool, conv, around_message_id=uuid.uuid4())
    assert window == []


# ---------------------------------------------------------------------------
# 3. Router: GET /api/conversations/messages/search
# ---------------------------------------------------------------------------


async def _search_messages(app: FastAPI, **params) -> dict:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url=BASE_URL
    ) as client:
        resp = await client.get(SEARCH_PATH, params=params)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def test_router_paginates_by_cursor_with_highlight_ranges(
    search_app: FastAPI, pool: asyncpg.Pool
) -> None:
    now = datetime.now(UTC)
    conv = await _seed_conversation(pool, butler_name="finance")
    for i in range(3):
        await _seed_message(
            pool,
            conversation_id=conv,
            content=f"gizmo shipment update {i}",
            created_at=now - timedelta(minutes=10 - i),
        )

    page1 = await _search_messages(search_app, q="gizmo", limit=2)
    assert len(page1["data"]) == 2
    assert page1["meta"]["has_more"] is True
    assert page1["meta"]["next_cursor"] is not None
    assert "offset" not in page1["meta"]
    assert "total" not in page1["meta"]

    first_item = page1["data"][0]
    assert first_item["highlight_ranges"], "expected at least one highlight range"
    start, end = first_item["highlight_ranges"][0]
    assert first_item["snippet"][start:end].lower() == "gizmo"

    page2 = await _search_messages(
        search_app, q="gizmo", limit=2, cursor=page1["meta"]["next_cursor"]
    )
    assert len(page2["data"]) == 1
    assert page2["meta"]["has_more"] is False
    assert page2["meta"]["next_cursor"] is None

    seen_ids = {item["message_id"] for item in page1["data"]} | {
        item["message_id"] for item in page2["data"]
    }
    assert len(seen_ids) == 3


async def test_router_rejects_query_over_max_length(search_app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=search_app), base_url=BASE_URL
    ) as client:
        resp = await client.get(SEARCH_PATH, params={"q": "x" * 513})
    assert resp.status_code == 422


async def test_router_invalid_cursor_returns_422(search_app: FastAPI) -> None:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=search_app), base_url=BASE_URL
    ) as client:
        resp = await client.get(SEARCH_PATH, params={"q": "anything", "cursor": "not-a-cursor"})
    assert resp.status_code == 422
