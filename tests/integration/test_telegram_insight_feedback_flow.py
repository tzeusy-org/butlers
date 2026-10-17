"""Real migrated PostgreSQL, real owner-auth boundary, scripted Telegram transport.

The broker lock owns signature binding, latest-verdict deduplication and replay
handling; no mocked pool can establish these guarantees.
"""

from __future__ import annotations

import asyncio
import shutil
import sys
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.connectors import telegram_bot as bot_module
from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig
from butlers.core.approval_callbacks import APPROVAL_CALLBACK_SECRET_KEY
from butlers.core.insight_feedback_callbacks import mint_insight_feedback_token
from butlers.credential_store import CredentialStore, ensure_secrets_schema
from tests.api.auth_helpers import _DomainOwnerState
from tests.api.test_switchboard_insights import _get_db_dep
from tests.decision_desk_helpers import migrated_switchboard_db

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]
CREATED = datetime(2026, 10, 16, 8, tzinfo=UTC)
SECRET = "test-only-insight-feedback-secret"
CREDENTIAL = "test-only-insight-connector-credential"
API = "http://dashboard-api:41200"


class _DB:
    def __init__(self, pool):
        self._pool = pool

    def pool(self, name):
        assert name == "switchboard"
        return self._pool

    def credential_shared_pool(self):
        return self._pool


def _app(pool):
    dependency = _get_db_dep()
    app = FastAPI()
    app.state.approval_callback_connector_token = CREDENTIAL
    app.state.owner_auth_service = _DomainOwnerState("test-only-dashboard-key")
    app.add_middleware(
        OwnerAuthMiddleware, config=OwnerAuthConfig.from_env("test-only-dashboard-key")
    )
    app.include_router(sys.modules["switchboard_api_router"].router)
    app.dependency_overrides[dependency] = lambda: _DB(pool)
    return app


class _HTTP:
    def __init__(self, app):
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=API)
        self.calls = []
        self.failure = None

    async def get(self, url, **kwargs):
        if self.failure == "get":
            return httpx.Response(503, json={"detail": "unavailable"})
        return await self.client.get(url, **kwargs)

    async def post(self, url, **kwargs):
        if url.startswith(API):
            if self.failure == "post":
                return httpx.Response(503, json={"detail": "unavailable"})
            if self.failure == "network":
                raise httpx.ConnectError("synthetic transport failure")
            return await self.client.post(url, **kwargs)
        name = url.rsplit("/", 1)[-1]
        self.calls.append((name, kwargs["json"]))
        return httpx.Response(200, json={"ok": True})


@pytest.fixture(scope="module")
def feedback_db(postgres_container):
    return migrated_switchboard_db(postgres_container)


@pytest.fixture
async def pool(feedback_db):
    pool = await asyncpg.create_pool(
        feedback_db, min_size=1, max_size=4, server_settings={"search_path": "switchboard,public"}
    )
    await pool.execute(
        "TRUNCATE public.insight_feedback, public.insight_candidates, public.insight_cooldowns CASCADE"
    )
    await ensure_secrets_schema(pool)
    await CredentialStore(pool).store(APPROVAL_CALLBACK_SECRET_KEY, SECRET)
    try:
        yield pool
    finally:
        await pool.close()


@pytest.fixture
async def bot(pool, monkeypatch):
    owner = AsyncMock(return_value=(SimpleNamespace(roles=["owner"]), True))
    monkeypatch.setattr(bot_module, "resolve_owner_channel_via_definer", owner)
    connector = TelegramBotConnector(
        TelegramBotConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="telegram:bot:1",
            telegram_token="test-token",
            internal_api_url=API,
            approval_callback_secret=SECRET,
            approval_callback_connector_token=CREDENTIAL,
        ),
        db_pool=MagicMock(),
        cursor_pool=MagicMock(),
    )
    http = _HTTP(_app(pool))
    connector._http_client = http
    try:
        yield connector, http, owner
    finally:
        await http.client.aclose()


async def _candidate(pool):
    return await pool.fetchval(
        "INSERT INTO public.insight_candidates "
        "(origin_butler, priority, category, dedup_key, expires_at, message, created_at) "
        "VALUES ('general', 80, 'reminder', 'general:reminder:2026-10', $1, 'Private insight', $2) RETURNING id",
        CREATED + timedelta(days=5),
        CREATED,
    )


def _token(insight_id, verdict, created_at=CREATED):
    return mint_insight_feedback_token(
        insight_id=insight_id, verdict=verdict, created_at=created_at, secret=SECRET
    )


def _tap(insight_id, verdict, query_id="cbq-1"):
    rows = [
        [
            {"text": label, "callback_data": _token(insight_id, value)}
            for label, value in [("Useful", "useful"), ("Not now", "not_now"), ("Never", "never")]
        ]
    ]
    return {
        "callback_query": {
            "id": query_id,
            "data": _token(insight_id, verdict),
            "from": {"id": 9001},
            "message": {
                "chat": {"id": 9001},
                "message_id": 44,
                "text": "Private insight",
                "reply_markup": {"inline_keyboard": rows},
            },
        }
    }


async def _feedback(pool):
    return await pool.fetch(
        "SELECT verdict, actor, snooze_until, evidence_ref FROM public.insight_feedback ORDER BY decided_at, id"
    )


async def test_owner_feedback_snoozes_marks_reverses_and_retries_without_duplicate(pool, bot):
    connector, http, _ = bot
    insight_id = await _candidate(pool)
    before = datetime.now(UTC)
    await connector._process_update(_tap(insight_id, "not_now"))
    rows = await _feedback(pool)
    assert len(rows) == 1
    assert (rows[0]["verdict"], rows[0]["actor"], rows[0]["evidence_ref"]) == (
        "not_now",
        "owner@telegram",
        "telegram:cbq-1",
    )
    assert (
        before + timedelta(hours=72)
        <= rows[0]["snooze_until"]
        <= datetime.now(UTC) + timedelta(hours=72)
    )
    assert http.calls[-1][0] == "editMessageReplyMarkup"
    marked = http.calls[-1][1]["reply_markup"]["inline_keyboard"][0]
    assert [b["text"] for b in marked] == ["Useful", "✓ Not now", "Never"]
    await connector._maybe_handle_insight_feedback_callback(_tap(insight_id, "never", "cbq-2"))
    assert await pool.fetchval("SELECT reason FROM public.insight_cooldowns") == "never"
    await connector._maybe_handle_insight_feedback_callback(_tap(insight_id, "useful", "cbq-3"))
    assert await pool.fetchval("SELECT count(*) FROM public.insight_cooldowns") == 0
    await connector._maybe_handle_insight_feedback_callback(_tap(insight_id, "useful", "cbq-4"))
    assert len(await _feedback(pool)) == 3
    # A delayed replay of the *original* Not now query must not undo Useful.
    await connector._maybe_handle_insight_feedback_callback(_tap(insight_id, "not_now"))
    assert [r["verdict"] for r in await _feedback(pool)] == ["not_now", "never", "useful"]
    assert http.calls[-2][1]["text"] == "Already recorded."
    assert http.calls[-1][1]["reply_markup"]["inline_keyboard"][0][0]["text"] == "✓ Useful"


async def test_hostile_callbacks_cannot_write_or_edit(pool, bot):
    connector, http, owner = bot
    insight_id = await _candidate(pool)
    for case in ("non_owner", "non_primary", "other_chat", "forged", "wrong_binding", "malformed"):
        tap = _tap(insight_id, "useful")
        owner.return_value = (SimpleNamespace(roles=["owner"]), True)
        if case == "non_owner":
            owner.return_value = None
        elif case == "non_primary":
            owner.return_value = (SimpleNamespace(roles=["owner"]), False)
        elif case == "other_chat":
            tap["callback_query"]["message"]["chat"]["id"] = 777
        elif case == "forged":
            raw = tap["callback_query"]["data"]
            tap["callback_query"]["data"] = raw[:-16] + "0" * 16
        elif case == "wrong_binding":
            tap["callback_query"]["data"] = _token(
                insight_id, "useful", CREATED - timedelta(seconds=1)
            )
        else:
            tap["callback_query"]["data"] = "ifb1:\ud800"
        assert await connector._maybe_handle_insight_feedback_callback(tap)
        assert await _feedback(pool) == []
    assert all(name == "answerCallbackQuery" and data["text"] == "" for name, data in http.calls)


async def test_transient_callback_failures_keep_keyboard_and_database(pool, bot):
    connector, http, _ = bot
    insight_id = await _candidate(pool)
    for failure in ("get", "post", "network"):
        http.failure = failure
        await connector._maybe_handle_insight_feedback_callback(_tap(insight_id, "never"))
        assert await _feedback(pool) == []
        assert http.calls[-1][0] == "answerCallbackQuery"
        assert "try again" in http.calls[-1][1]["text"]
    assert not any(name == "editMessageReplyMarkup" for name, _ in http.calls)


async def test_purged_insight_loses_only_its_own_digest_row(pool, bot):
    connector, http, _ = bot
    insight_id = await _candidate(pool)
    sibling = UUID("12345678-1234-5678-1234-567812345678")
    tap = _tap(insight_id, "useful")
    sibling_row = _tap(sibling, "useful")["callback_query"]["message"]["reply_markup"][
        "inline_keyboard"
    ][0]
    tap["callback_query"]["message"]["reply_markup"]["inline_keyboard"].append(sibling_row)
    await pool.execute("DELETE FROM public.insight_candidates WHERE id=$1", insight_id)
    await connector._maybe_handle_insight_feedback_callback(tap)
    assert await _feedback(pool) == []
    assert http.calls[-2][1]["text"] == "No longer available."
    assert http.calls[-1][1]["reply_markup"]["inline_keyboard"] == [sibling_row]
    await connector._maybe_handle_insight_feedback_callback(
        _tap(insight_id, "useful", "purged-alone")
    )
    assert http.calls[-1][1]["reply_markup"] is None


async def test_concurrent_duplicate_taps_append_once(pool, bot):
    connector, _, _ = bot
    insight_id = await _candidate(pool)
    await asyncio.gather(
        *(
            connector._maybe_handle_insight_feedback_callback(
                _tap(insight_id, "never", f"race-{i}")
            )
            for i in range(4)
        )
    )
    assert [r["verdict"] for r in await _feedback(pool)] == ["never"]


async def test_callback_api_independently_rejects_token_binding_forgery_and_body_authority(
    pool, bot
):
    _, http, _ = bot
    insight_id = await _candidate(pool)
    path = f"/api/switchboard/insights/{insight_id}/telegram-feedback"
    headers = {
        "X-Butlers-Approval-Callback-Token": CREDENTIAL,
        "X-Butlers-Decision-Actor": "owner@telegram",
    }
    valid = {
        "verdict": "useful",
        "token": _token(insight_id, "useful"),
        "callback_query_id": "direct",
    }
    for body in (
        {**valid, "actor": "forged"},
        {**valid, "snooze_until": "2099-01-01"},
        {**valid, "verdict": "never"},
        {**valid, "token": _token(insight_id, "useful", CREATED - timedelta(seconds=1))},
    ):
        response = await http.client.post(path, headers=headers, json=body)
        assert response.status_code in (403, 422)
        assert valid["token"] not in response.text
        assert await _feedback(pool) == []
    # Unavailable signing custody fails closed before any broker write.
    with patch("butlers.credential_store.CredentialStore.resolve", AsyncMock(return_value=None)):
        response = await http.client.post(path, headers=headers, json=valid)
    assert response.status_code == 503
    assert await _feedback(pool) == []


async def test_digest_verdict_marks_only_target_and_keeps_other_actions(pool, bot):
    connector, http, _ = bot
    insight_id = await _candidate(pool)
    tap = _tap(insight_id, "never")
    sibling = UUID("12345678-1234-5678-1234-567812345678")
    sibling_row = _tap(sibling, "useful")["callback_query"]["message"]["reply_markup"][
        "inline_keyboard"
    ][0]
    tap["callback_query"]["message"]["reply_markup"]["inline_keyboard"].append(sibling_row)
    await connector._maybe_handle_insight_feedback_callback(tap)
    marked = http.calls[-1][1]["reply_markup"]["inline_keyboard"]
    assert len(marked) == 2 and marked[1] == sibling_row
    assert [b["text"] for b in marked[0]] == ["Useful", "Not now", "✓ Never"]
    assert [r["verdict"] for r in await _feedback(pool)] == ["never"]
