"""Real-Postgres Telegram ``dsk1`` tap -> decision intent, through the real auth gate.

bu-ckkpz.3, REQ-owner-decision-desk-004. The connector verifies the owner
channel and the HMAC against the prompt row the dashboard API returns, then
records the intent through the connector-scoped ``choose`` route. The owner's
dashboard intent route and the connector's prompt routes are not
interchangeable.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import asyncpg
import httpx
import pytest
from fastapi import FastAPI

from butlers.api.owner_auth.config import OwnerAuthConfig
from butlers.api.owner_auth.http import OwnerAuthMiddleware
from butlers.api.routers import decisions as decisions_router
from butlers.connectors import telegram_bot as telegram_bot_module
from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig
from butlers.core import decision_desk
from butlers.core.approval_callbacks import (
    APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER,
    APPROVAL_CALLBACK_SECRET_KEY,
)
from butlers.core.decision_callbacks import mint_decision_callback_token
from butlers.credential_store import CredentialStore, ensure_secrets_schema
from butlers.jobs.decision_routing import _reserve
from tests.api.auth_helpers import _DomainOwnerState
from tests.core.test_decision_desk import _applied_reopening_intent, _reopening_digest
from tests.decision_desk_helpers import (
    NOW,
    make_bead,
    make_digest,
    migrated_switchboard_db,
    switchboard_pool,
)

pytestmark = [
    pytest.mark.integration,
    pytest.mark.asyncio(loop_scope="session"),
    pytest.mark.skipif(shutil.which("docker") is None, reason="Docker not available"),
]

_CREATED_AT = datetime(2026, 10, 8, 8, 0, tzinfo=UTC)
_SECRET = "test-only-decision-callback-secret"
_DASHBOARD_API_KEY = "test-only-dashboard-api-key"
_CONNECTOR_TOKEN = "test-only-callback-connector-token"
_API = "http://dashboard-api:41200"


class _DbManager:
    def __init__(self, pool: Any) -> None:
        self._pool = pool

    def pool(self, butler_name: str) -> Any:
        assert butler_name == "switchboard"
        return self._pool

    def credential_shared_pool(self) -> Any:
        # This migrated test database deliberately holds the shared credential
        # table as well as Switchboard. Production uses the shared-pool API.
        return self._pool


class _CallbackHttpClient:
    """Route dashboard requests through ASGI and record Telegram API calls."""

    def __init__(self, app: FastAPI) -> None:
        self._dashboard = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=_API)
        self.telegram_calls: list[tuple[str, dict[str, Any]]] = []
        self.dashboard_calls: list[tuple[str, dict[str, Any], httpx.Response]] = []

    async def get(self, url: str, **kwargs: Any) -> httpx.Response:
        assert url.startswith(f"{_API}/")
        return await self._dashboard.get(url, **kwargs)

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        if url.startswith(f"{_API}/"):
            response = await self._dashboard.post(url, **kwargs)
            self.dashboard_calls.append((url, kwargs, response))
            return response
        self.telegram_calls.append((url.rsplit("/", 1)[-1], kwargs.get("json", {})))
        return httpx.Response(200, request=httpx.Request("POST", url), json={"ok": True})

    async def close(self) -> None:
        await self._dashboard.aclose()


def _app(pool: Any) -> FastAPI:
    app = FastAPI()
    app.state.approval_callback_connector_token = _CONNECTOR_TOKEN
    app.state.owner_auth_service = _DomainOwnerState(_DASHBOARD_API_KEY)
    app.add_middleware(OwnerAuthMiddleware, config=OwnerAuthConfig.from_env(_DASHBOARD_API_KEY))
    app.include_router(decisions_router.router)
    app.dependency_overrides[decisions_router._get_db_manager] = lambda: _DbManager(pool)
    return app


@pytest.fixture(scope="module")
def desk_db_url(postgres_container) -> str:
    return migrated_switchboard_db(postgres_container)


@pytest.fixture
async def pool(desk_db_url):
    pool = await switchboard_pool(desk_db_url)
    try:
        await ensure_secrets_schema(pool)
        await CredentialStore(pool).store(APPROVAL_CALLBACK_SECRET_KEY, _SECRET)
        yield pool
    finally:
        await pool.close()


@pytest.fixture
def digest():
    with patch.object(
        decisions_router, "compute_decision_digest", return_value=make_digest(make_bead())
    ) as mocked:
        yield mocked


@pytest.fixture
def owner(monkeypatch):
    resolver = AsyncMock(return_value=(SimpleNamespace(roles=["owner"]), True))
    monkeypatch.setattr(telegram_bot_module, "resolve_owner_channel_via_definer", resolver)
    return resolver


async def _prompt(pool: asyncpg.Pool) -> UUID:
    return await pool.fetchval(
        "INSERT INTO switchboard.decision_prompts "
        "(bead_id, options, default_option, created_at, delivery_outcome) "
        "VALUES ('bu-test1', $1::jsonb, 'Hold', $2, 'delivered') RETURNING id",
        json.dumps(["Ship it", "Hold"]),
        _CREATED_AT,
    )


def _connector(pool: Any) -> tuple[TelegramBotConnector, _CallbackHttpClient]:
    connector = TelegramBotConnector(
        TelegramBotConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="telegram:bot:1",
            telegram_token="test-token",
            internal_api_url=_API,
            approval_callback_secret=_SECRET,
            approval_callback_connector_token=_CONNECTOR_TOKEN,
        ),
        db_pool=MagicMock(),
        cursor_pool=MagicMock(),
    )
    http_client = _CallbackHttpClient(_app(pool))
    connector._http_client = http_client
    return connector, http_client


def _tap(token: str, query_id: str = "cbq-1") -> dict[str, Any]:
    return {
        "callback_query": {
            "id": query_id,
            "data": token,
            "from": {"id": 9001},
            "message": {"chat": {"id": 9001}, "message_id": 44},
        }
    }


def _token(prompt_id: UUID, index: int, created_at: datetime = _CREATED_AT) -> str:
    return mint_decision_callback_token(
        prompt_id=prompt_id, option_index=index, created_at=created_at, secret=_SECRET
    )


async def _intents(pool) -> list[tuple[str, str, str, str]]:
    rows = await pool.fetch(
        "SELECT option, source, actor, status FROM switchboard.decision_intents"
    )
    return [(r["option"], r["source"], r["actor"], r["status"]) for r in rows]


async def test_owner_tap_records_a_pending_intent_and_clears_the_keyboard(
    pool, digest, owner
) -> None:
    prompt_id = await _prompt(pool)
    connector, http_client = _connector(pool)
    try:
        handled = await connector._process_update(_tap(_token(prompt_id, 1)))
        repeat = await connector._maybe_handle_decision_callback(
            _tap(_token(prompt_id, 1), "cbq-2")
        )
    finally:
        await http_client.close()

    assert handled is None and repeat is True
    assert await _intents(pool) == [("Hold", "telegram", "owner@telegram", "pending")]
    calls = http_client.telegram_calls
    assert [name for name, _ in calls] == [
        "answerCallbackQuery",
        "editMessageText",
        "editMessageReplyMarkup",
        "answerCallbackQuery",
        "editMessageText",
        "editMessageReplyMarkup",
    ]
    assert calls[0][1]["text"] == "Choice recorded."
    assert calls[3][1]["text"] == "Already handled."
    assert calls[1][1]["text"] == "Decision recorded: Hold\nPending update to the tracker."
    assert calls[2][1]["reply_markup"] is None
    assert len(http_client.dashboard_calls) == 2
    for _, kwargs, response in http_client.dashboard_calls:
        assert kwargs["json"] == {"option_index": 1, "token": _token(prompt_id, 1)}
        assert kwargs["headers"][APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER] == _CONNECTOR_TOKEN
        assert kwargs["headers"]["X-Butlers-Decision-Actor"] == "owner@telegram"
        assert response.status_code == 200
    first, second = [call[2].json() for call in http_client.dashboard_calls]
    assert first["meta"]["created"] is True and second["meta"]["created"] is False
    assert first["data"]["id"] == second["data"]["id"]
    async with pool.acquire() as connection:
        persisted = repr(await connection.fetch("SELECT * FROM switchboard.decision_intents"))
        persisted += repr(await connection.fetch("SELECT * FROM switchboard.decision_prompts"))
    assert _token(prompt_id, 1) not in persisted and _SECRET not in persisted


async def test_conflicting_second_tap_is_refused(pool, digest, owner) -> None:
    prompt_id = await _prompt(pool)
    connector, http_client = _connector(pool)
    try:
        await connector._maybe_handle_decision_callback(_tap(_token(prompt_id, 0)))
        await connector._maybe_handle_decision_callback(_tap(_token(prompt_id, 1), "cbq-2"))
    finally:
        await http_client.close()

    assert await _intents(pool) == [("Ship it", "telegram", "owner@telegram", "pending")]
    assert http_client.telegram_calls[3] == (
        "answerCallbackQuery",
        {
            "callback_query_id": "cbq-2",
            "text": "Already handled.",
        },
    )


@pytest.mark.parametrize("case", ["forged", "wrong_binding", "non_owner", "non_primary"])
async def test_unverified_taps_record_nothing(pool, digest, owner, case) -> None:
    prompt_id = await _prompt(pool)
    token = _token(prompt_id, 0)
    if case == "forged":
        token = token.rsplit(":", 1)[0] + ":" + "0" * 16
    elif case == "wrong_binding":
        token = _token(prompt_id, 0, created_at=datetime(2026, 1, 1, tzinfo=UTC))
    elif case == "non_owner":
        owner.return_value = None
    else:
        owner.return_value = (SimpleNamespace(roles=["owner"]), False)
    connector, http_client = _connector(pool)
    try:
        handled = await connector._maybe_handle_decision_callback(_tap(token))
    finally:
        await http_client.close()

    assert handled is True
    assert await _intents(pool) == []
    assert http_client.telegram_calls == [
        ("answerCallbackQuery", {"callback_query_id": "cbq-1", "text": ""})
    ]


async def test_closed_decision_tap_says_so(pool, owner) -> None:
    prompt_id = await _prompt(pool)
    connector, http_client = _connector(pool)
    try:
        with patch.object(decisions_router, "compute_decision_digest", return_value=make_digest()):
            await connector._maybe_handle_decision_callback(_tap(_token(prompt_id, 0)))
    finally:
        await http_client.close()

    assert await _intents(pool) == []
    assert http_client.telegram_calls[0][1]["text"] == "Already handled."
    assert [name for name, _ in http_client.telegram_calls] == [
        "answerCallbackQuery",
        "editMessageReplyMarkup",
    ]


async def test_changed_options_tap_points_at_the_dashboard(pool, owner) -> None:
    prompt_id = await _prompt(pool)
    connector, http_client = _connector(pool)
    reordered = make_digest(make_bead(options=("Hold", "Ship it")))
    try:
        with patch.object(decisions_router, "compute_decision_digest", return_value=reordered):
            await connector._maybe_handle_decision_callback(_tap(_token(prompt_id, 0)))
    finally:
        await http_client.close()

    assert await _intents(pool) == []
    assert http_client.telegram_calls[0][1]["text"] == "Options changed; open the dashboard."


async def test_unreachable_api_keeps_the_keyboard(pool, digest, owner) -> None:
    prompt_id = await _prompt(pool)
    connector, http_client = _connector(pool)
    connector._submit_decision_choice = AsyncMock(return_value=(None, None))
    try:
        await connector._maybe_handle_decision_callback(_tap(_token(prompt_id, 0)))
    finally:
        await http_client.close()

    assert [name for name, _ in http_client.telegram_calls] == ["answerCallbackQuery"]
    assert http_client.telegram_calls[0][1]["text"].startswith("Couldn't record that")


@pytest.mark.parametrize("failure", ["network", "server_error"])
async def test_unreachable_prompt_lookup_says_retry(pool, digest, owner, failure) -> None:
    prompt_id = await _prompt(pool)
    connector, http_client = _connector(pool)
    real_get = http_client.get

    async def failing_get(url: str, **kwargs: Any) -> httpx.Response:
        if failure == "network":
            raise httpx.ConnectError("dashboard-api down")
        return httpx.Response(503, request=httpx.Request("GET", url))

    http_client.get = failing_get  # type: ignore[method-assign]
    try:
        await connector._maybe_handle_decision_callback(_tap(_token(prompt_id, 0)))
    finally:
        http_client.get = real_get  # type: ignore[method-assign]
        await http_client.close()

    assert await _intents(pool) == []
    assert [name for name, _ in http_client.telegram_calls] == ["answerCallbackQuery"]
    assert http_client.telegram_calls[0][1]["text"].startswith("Couldn't record that")


async def test_unknown_prompt_gets_only_the_generic_acknowledgement(pool, digest, owner) -> None:
    connector, http_client = _connector(pool)
    stranger = UUID("99999999-9999-4999-8999-999999999999")
    try:
        await connector._maybe_handle_decision_callback(_tap(_token(stranger, 0)))
    finally:
        await http_client.close()

    assert http_client.telegram_calls == [
        ("answerCallbackQuery", {"callback_query_id": "cbq-1", "text": ""})
    ]


async def test_credentials_are_not_interchangeable(pool, digest) -> None:
    prompt_id = await _prompt(pool)
    connector_headers = {APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: _CONNECTOR_TOKEN}
    owner_headers = {"X-API-Key": _DASHBOARD_API_KEY}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(pool)), base_url=_API
    ) as client:
        connector_on_owner_route = await client.post(
            "/api/decisions/bu-test1/intent",
            json={"option": "Hold"},
            headers={**connector_headers, "X-Butlers-Decision-Actor": "owner@telegram"},
        )
        owner_on_choose = await client.post(
            f"/api/decisions/prompts/{prompt_id}/choose",
            json={"option_index": 0, "token": _token(prompt_id, 0)},
            headers={**owner_headers, "X-Butlers-Decision-Actor": "owner@telegram"},
        )
        connector_without_actor = await client.post(
            f"/api/decisions/prompts/{prompt_id}/choose",
            json={"option_index": 0, "token": _token(prompt_id, 0)},
            headers=connector_headers,
        )
        connector_on_digest = await client.get("/api/decisions", headers=connector_headers)
        connector_wrong_actor = await client.post(
            f"/api/decisions/prompts/{prompt_id}/choose",
            json={"option_index": 0, "token": _token(prompt_id, 0)},
            headers={**connector_headers, "X-Butlers-Decision-Actor": "owner@dashboard"},
        )
        token_without_credential = await client.post(
            f"/api/decisions/prompts/{prompt_id}/choose",
            json={"option_index": 0, "token": _token(prompt_id, 0)},
        )
        owner_records = await client.post(
            "/api/decisions/bu-test1/intent", json={"option": "Hold"}, headers=owner_headers
        )

    assert connector_on_owner_route.status_code == 401
    assert owner_on_choose.status_code == 403
    assert connector_without_actor.status_code == 403
    assert connector_wrong_actor.status_code == 403
    assert token_without_credential.status_code == 401
    assert connector_on_digest.status_code == 401
    assert owner_records.status_code == 200
    assert owner_records.json()["data"]["status"] == "pending"
    assert owner_records.json()["meta"]["created"] is True
    assert await _intents(pool) == [("Hold", "dashboard", "owner@dashboard", "pending")]
    # Start another explicit choice in this isolated fixture after proving all
    # original owner-route outcomes; caller-supplied attribution is ignored.
    await pool.execute("DELETE FROM switchboard.decision_intents")
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(pool)), base_url=_API
    ) as client:
        spoof = await client.post(
            f"/api/decisions/prompts/{prompt_id}/choose",
            json={"option_index": 0, "token": _token(prompt_id, 0), "actor": "forged-actor"},
            headers={**connector_headers, "X-Butlers-Decision-Actor": "owner@telegram"},
        )
    assert spoof.status_code == 200
    assert await _intents(pool) == [("Ship it", "telegram", "owner@telegram", "pending")]


async def test_token_bearing_choose_transport_failure_is_private(
    pool, digest, owner, monkeypatch, caplog
) -> None:
    prompt_id = await _prompt(pool)
    token = _token(prompt_id, 0)
    connector, http_client = _connector(pool)
    real_post = http_client.post
    buffered = MagicMock()
    ingestion = AsyncMock()
    monkeypatch.setattr(connector._filtered_event_buffer, "record", buffered)
    monkeypatch.setattr(connector, "_submit_to_ingest", ingestion)

    async def failing_post(url, **kwargs):
        if url.startswith(f"{_API}/"):
            assert kwargs["json"]["token"] == token
            request = httpx.Request("POST", url, json=kwargs["json"])
            raise httpx.ConnectError(token + _SECRET + _CONNECTOR_TOKEN, request=request)
        return await real_post(url, **kwargs)

    monkeypatch.setattr(http_client, "post", failing_post)
    try:
        await connector._process_update(_tap(token))
    finally:
        await http_client.close()
    assert await _intents(pool) == []
    buffered.assert_not_called()
    ingestion.assert_not_called()
    assert [name for name, _ in http_client.telegram_calls] == ["answerCallbackQuery"]
    assert http_client.telegram_calls[0][1]["text"].startswith("Couldn't record that")
    records = [record for record in caplog.records if record.name == telegram_bot_module.__name__]
    assert records and all(record.exc_info is None for record in records)
    for private in (token, token[-12:], _SECRET, _CONNECTOR_TOKEN):
        assert private not in caplog.text


async def test_unencodable_tap_is_acknowledged_without_error_buffering(pool, monkeypatch) -> None:
    connector, http_client = _connector(pool)
    buffered = MagicMock()
    ingestion = AsyncMock()
    monkeypatch.setattr(connector._filtered_event_buffer, "record", buffered)
    monkeypatch.setattr(connector, "_submit_to_ingest", ingestion)
    try:
        assert await connector._process_update(_tap("dsk1:\ud800")) is None
    finally:
        await http_client.close()
    assert http_client.telegram_calls == [
        ("answerCallbackQuery", {"callback_query_id": "cbq-1", "text": ""})
    ]
    assert await _intents(pool) == []
    buffered.assert_not_called()
    ingestion.assert_not_called()
    assert http_client.dashboard_calls == []


def _choose_headers():
    return {
        APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: _CONNECTOR_TOKEN,
        "X-Butlers-Decision-Actor": "owner@telegram",
    }


async def _committed_rows(pool):
    # Independent acquisitions ensure transaction-local visibility cannot
    # masquerade as a durable commit (or conceal a rejected request's writes).
    async with pool.acquire() as connection:
        intents = await connection.fetch("SELECT * FROM switchboard.decision_intents ORDER BY id")
        prompts = await connection.fetch("SELECT * FROM switchboard.decision_prompts ORDER BY id")
    return [dict(row) for row in intents], [dict(row) for row in prompts]


@pytest.mark.parametrize("case", ["forged", "binding-mismatch", "secret-unavailable"])
async def test_rejected_choose_has_no_persisted_side_effect(
    pool, tmp_path, monkeypatch, case
) -> None:
    prompt_id = await _prompt(pool)
    original = await _applied_reopening_intent(
        pool, finished_at=NOW - timedelta(minutes=10), prompt_id=prompt_id
    )
    qualified = _reopening_digest(tmp_path, finished_at=original["finished_at"])
    monkeypatch.setattr(decisions_router, "compute_decision_digest", lambda: qualified)
    reconcile = AsyncMock(wraps=decision_desk._reconcile_reopened)
    monkeypatch.setattr(decision_desk, "_reconcile_reopened", reconcile)
    await decision_desk.record_decision_intent(
        pool,
        bead_id="bu-other",
        option="Hold",
        source="dashboard",
        actor="owner@dashboard",
        digest=make_digest(make_bead("bu-other")),
    )
    reconcile.reset_mock()
    token = _token(prompt_id, 0)
    if case == "forged":
        token = token.rsplit(":", 1)[0] + ":" + "0" * 16
    elif case == "binding-mismatch":
        token = _token(prompt_id, 1)
    else:
        await pool.execute(
            "DELETE FROM butler_secrets WHERE secret_key = $1", APPROVAL_CALLBACK_SECRET_KEY
        )
    before_intents, before_prompts = await _committed_rows(pool)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(pool)), base_url=_API
    ) as client:
        rejected = await client.post(
            f"/api/decisions/prompts/{prompt_id}/choose",
            json={"option_index": 0, "token": token},
            headers=_choose_headers(),
        )
        assert rejected.status_code == (503 if case == "secret-unavailable" else 403)
        assert rejected.json()["detail"] == (
            "callback_verification_unavailable"
            if case == "secret-unavailable"
            else "invalid_callback_token"
        )
        reconcile.assert_not_awaited()
        assert await _committed_rows(pool) == (before_intents, before_prompts)
        await CredentialStore(pool).store(APPROVAL_CALLBACK_SECRET_KEY, _SECRET)
        consumed = await client.post(
            f"/api/decisions/prompts/{prompt_id}/choose",
            json={"option_index": 0, "token": _token(prompt_id, 0)},
            headers=_choose_headers(),
        )
    assert consumed.status_code == 409
    assert consumed.json()["detail"] == "intent_conflict:superseded"
    reconcile.assert_awaited_once()
    after_intents, after_prompts = await _committed_rows(pool)
    assert after_prompts == before_prompts
    assert len(after_intents) == len(before_intents) == 2
    previous = next(row for row in before_intents if row["id"] == original["id"])
    superseded = next(row for row in after_intents if row["id"] == original["id"])
    assert previous["status"] == "applied" and superseded["status"] == "superseded"
    assert superseded["updated_at"] > previous["updated_at"]
    assert {
        key: value for key, value in superseded.items() if key not in {"status", "updated_at"}
    } == {key: value for key, value in previous.items() if key not in {"status", "updated_at"}}
    assert [row for row in after_intents if row["id"] != original["id"]] == [
        row for row in before_intents if row["id"] != original["id"]
    ]


@pytest.mark.parametrize("order", ["reservation-first", "choice-first"])
async def test_choose_and_rereservation_share_one_locked_generation(
    pool, digest, monkeypatch, order
) -> None:
    prompt_id = await _prompt(pool)
    await pool.execute(
        "UPDATE switchboard.decision_prompts SET delivery_outcome = 'not_attempted' WHERE id = $1",
        prompt_id,
    )
    fresh_time = _CREATED_AT + timedelta(microseconds=1)
    fresh_bead = make_bead(options=("Fresh choice", "Hold"))
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(pool)), base_url=_API
    ) as client:

        async def choose():
            return await client.post(
                f"/api/decisions/prompts/{prompt_id}/choose",
                json={"option_index": 0, "token": _token(prompt_id, 0)},
                headers=_choose_headers(),
            )

        if order == "reservation-first":
            reservation = await _reserve(pool, fresh_bead, now=fresh_time)
            assert reservation.prompt_id == prompt_id and reservation.created_at == fresh_time
            response = await asyncio.wait_for(choose(), timeout=15)
            assert response.status_code == 403
            assert response.json()["detail"] == "invalid_callback_token"
            assert await _intents(pool) == []
        else:
            recorded = asyncio.Event()
            release = asyncio.Event()
            reservation_started = asyncio.Event()
            pids = {}
            record = decision_desk._record_locked

            async def hold_record(connection, **kwargs):
                result = await record(connection, **kwargs)
                pids["choose"] = connection.get_server_pid()
                recorded.set()
                await asyncio.wait_for(release.wait(), timeout=15)
                return result

            monkeypatch.setattr(decision_desk, "_record_locked", hold_record)

            async def reserve():
                async with pool.acquire() as connection:
                    pids["reserve"] = connection.get_server_pid()
                    reservation_started.set()
                    return await _reserve(connection, fresh_bead, now=fresh_time)

            choice = asyncio.create_task(choose())
            reservation_task = None
            try:
                await asyncio.wait_for(recorded.wait(), timeout=15)
                reservation_task = asyncio.create_task(reserve())
                await asyncio.wait_for(reservation_started.wait(), timeout=15)
                # Observe PostgreSQL's real lock waiter, rather than inferring
                # serialization from a sleep or a task that merely started.
                async with asyncio.timeout(15):
                    async with pool.acquire() as observer:
                        while pids["choose"] not in await observer.fetchval(
                            "SELECT pg_blocking_pids($1)", pids["reserve"]
                        ):
                            assert not reservation_task.done()
                            await asyncio.sleep(0)
                assert not reservation_task.done() and not choice.done()
                release.set()
                response = await asyncio.wait_for(choice, timeout=15)
                reservation = await asyncio.wait_for(reservation_task, timeout=15)
            finally:
                release.set()
                for task in (choice, reservation_task):
                    if task is not None and not task.done():
                        task.cancel()
                await asyncio.gather(
                    *[task for task in (choice, reservation_task) if task is not None],
                    return_exceptions=True,
                )
            assert response.status_code == 200 and response.json()["meta"]["created"] is True
            assert reservation.prompt_id == prompt_id and reservation.created_at == fresh_time
            assert await _intents(pool) == [("Ship it", "telegram", "owner@telegram", "pending")]
    _, prompts = await _committed_rows(pool)
    assert len(prompts) == 1 and prompts[0]["id"] == prompt_id
    assert prompts[0]["created_at"] == fresh_time
    assert json.loads(prompts[0]["options"]) == ["Fresh choice", "Hold"]
