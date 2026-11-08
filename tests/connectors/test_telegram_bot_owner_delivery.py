"""Telegram ``od1`` canonical owner-delivery taps and stale-card retraction (bu-dhloi1.13)."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.connectors import telegram_bot as telegram_bot_module
from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig
from butlers.core.approval_callbacks import APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER
from butlers.core.owner_delivery import mint_token

_CONNECTOR_TOKEN = "test-only-approval-callback-connector-token"
_API = "http://dashboard-api:41200"
_STALE_ID = "aaaaaaaa-0000-4000-8000-000000000001"
_BROKEN_ID = "aaaaaaaa-0000-4000-8000-000000000002"


def _response(*, status_code: int = 200, body: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status_code
    response.json.return_value = body if body is not None else {"data": {}}
    return response


def _connector() -> TelegramBotConnector:
    return TelegramBotConnector(
        TelegramBotConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="telegram:bot:1",
            telegram_token="test-token",
            internal_api_url=_API,
            approval_callback_secret="test-only-secret",
            approval_callback_connector_token=_CONNECTOR_TOKEN,
        ),
        db_pool=MagicMock(),
        cursor_pool=MagicMock(),
    )


def _update(data: str) -> dict:
    return {
        "update_id": 73,
        "callback_query": {
            "id": "cbq1",
            "data": data,
            "from": {"id": 9001},
            "message": {"chat": {"id": 9001}, "message_id": 44},
        },
    }


class _Http:
    """Dashboard + Telegram double recording every call."""

    def __init__(self, action: MagicMock, retractions: list[dict[str, Any]]) -> None:
        self.action = action
        self.retractions = retractions
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.gets: list[str] = []
        self.telegram_failures: set[int] = set()

    async def get(self, url: str, **kwargs: Any) -> MagicMock:
        self.gets.append(url)
        assert kwargs["headers"] == {APPROVAL_CALLBACK_CONNECTOR_TOKEN_HEADER: _CONNECTOR_TOKEN}
        return _response(body={"data": self.retractions})

    async def post(self, url: str, **kwargs: Any) -> MagicMock:
        body = kwargs.get("json", {})
        self.posts.append((url, body))
        if url.endswith("/api/owner-delivery/actions"):
            return self.action
        if url.startswith(_API):
            return _response(body={"data": {"recorded": True}})
        if body.get("message_id") in self.telegram_failures:
            raise RuntimeError("telegram said no")
        return _response(body={"ok": True})

    def telegram(self, method: str) -> list[dict[str, Any]]:
        return [body for url, body in self.posts if url.endswith(f"/{method}")]

    def dashboard(self, suffix: str) -> list[tuple[str, dict[str, Any]]]:
        return [(url, body) for url, body in self.posts if url.startswith(f"{_API}{suffix}")]


@pytest.fixture
def owner_resolver(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    resolver = AsyncMock(return_value=(SimpleNamespace(roles=["owner"]), True))
    monkeypatch.setattr(telegram_bot_module, "resolve_owner_channel_via_definer", resolver)
    return resolver


async def test_owner_tap_redeems_once_then_retracts_every_other_stale_card(
    owner_resolver: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    token, _ = mint_token()
    http = _Http(
        _response(body={"data": {"status": "executed"}}),
        [
            {"presentation_id": _STALE_ID, "route_ref": "9001", "provider_ref": "55"},
            {"presentation_id": _BROKEN_ID, "route_ref": "9001", "provider_ref": None},
        ],
    )
    connector = _connector()
    connector._http_client = http  # type: ignore[assignment]

    with caplog.at_level(logging.DEBUG):
        # The real dispatcher: an od1 tap is control plane, never Switchboard ingest.
        connector._normalize_to_ingest_v1 = AsyncMock(  # type: ignore[method-assign]
            side_effect=AssertionError("owner-delivery taps never ingest")
        )
        await connector._process_update(_update(token))

    owner_resolver.assert_awaited_once_with(connector._db_pool, "telegram_bot", "9001")
    actions = http.dashboard("/api/owner-delivery/actions")
    assert actions == [(f"{_API}/api/owner-delivery/actions", {"token": token})]
    # The tapped card shows the outcome; the other live card is retracted.
    edited = {body["message_id"]: body["text"] for body in http.telegram("editMessageText")}
    assert edited[44] == "✅ Approved"
    assert edited[55] == telegram_bot_module._OWNER_DELIVERY_RETRACTED_TEXT
    # A text edit without reply_markup drops the keyboard itself; no markup call follows it.
    assert http.telegram("editMessageReplyMarkup") == []
    # Each retraction attempt is receipted honestly; an unaddressable card is not success.
    reports = {
        url.rsplit("/", 1)[-1]: body
        for url, body in http.dashboard("/api/owner-delivery/retractions/")
    }
    assert reports == {
        _STALE_ID: {"retracted": True, "reason": None},
        _BROKEN_ID: {"retracted": False, "reason": "provider_ref_missing"},
    }
    assert token not in caplog.text and token.split(":", 1)[1] not in caplog.text


async def test_failed_provider_retraction_is_reported_not_claimed(
    owner_resolver: AsyncMock,
) -> None:
    token, _ = mint_token()
    http = _Http(
        _response(body={"data": {"status": "rejected"}}),
        [{"presentation_id": _STALE_ID, "route_ref": "9001", "provider_ref": "55"}],
    )
    http.telegram_failures.add(55)
    connector = _connector()
    connector._http_client = http  # type: ignore[assignment]

    assert await connector._maybe_handle_owner_delivery_callback(_update(token)) is True

    reports = http.dashboard("/api/owner-delivery/retractions/")
    assert reports == [
        (
            f"{_API}/api/owner-delivery/retractions/{_STALE_ID}",
            {"retracted": False, "reason": "provider_error"},
        )
    ]


@pytest.mark.parametrize("reason", ["stale_generation", "subject_resolved", "token_unknown"])
async def test_refused_tap_decides_nothing_and_only_drops_its_buttons(
    owner_resolver: AsyncMock, reason: str
) -> None:
    token, _ = mint_token()
    http = _Http(
        _response(
            status_code=409, body={"detail": {"error": "owner_delivery_refused", "reason": reason}}
        ),
        [],
    )
    connector = _connector()
    connector._http_client = http  # type: ignore[assignment]

    assert await connector._maybe_handle_owner_delivery_callback(_update(token)) is True

    assert http.telegram("editMessageText") == []
    assert [body["message_id"] for body in http.telegram("editMessageReplyMarkup")] == [44]


async def test_changed_call_tells_the_owner_nothing_ran(owner_resolver: AsyncMock) -> None:
    token, _ = mint_token()
    http = _Http(
        _response(
            status_code=409,
            body={"detail": {"error": "owner_delivery_refused", "reason": "action_changed"}},
        ),
        [],
    )
    connector = _connector()
    connector._http_client = http  # type: ignore[assignment]

    assert await connector._maybe_handle_owner_delivery_callback(_update(token)) is True

    [edit] = http.telegram("editMessageText")
    assert edit["text"].startswith("⚠️ Changed since shown")


async def test_non_owner_tap_never_reaches_the_action_route(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        telegram_bot_module, "resolve_owner_channel_via_definer", AsyncMock(return_value=None)
    )
    token, _ = mint_token()
    http = _Http(_response(body={"data": {"status": "executed"}}), [])
    connector = _connector()
    connector._http_client = http  # type: ignore[assignment]

    assert await connector._maybe_handle_owner_delivery_callback(_update(token)) is True

    assert http.dashboard("/api/owner-delivery") == []
    assert http.gets == []
    assert [body["callback_query_id"] for body in http.telegram("answerCallbackQuery")] == ["cbq1"]


async def test_other_callbacks_are_not_owner_delivery(owner_resolver: AsyncMock) -> None:
    connector = _connector()
    connector._http_client = _Http(_response(), [])  # type: ignore[assignment]
    assert await connector._maybe_handle_owner_delivery_callback(_update("apr1:x:a:y")) is False
    assert await connector._maybe_handle_owner_delivery_callback({"message": {}}) is False
    owner_resolver.assert_not_awaited()


class _TelegramRefusal(_Http):
    """Telegram double that answers one message id with a real-shaped HTTP 400."""

    def __init__(self, *args: Any, description: str, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.description = description
        self.refuse_text_edit_for: int | None = None

    async def post(self, url: str, **kwargs: Any) -> MagicMock:
        body = kwargs.get("json", {})
        if (
            url.startswith("http")
            and not url.startswith(_API)
            and body.get("message_id") == self.refuse_text_edit_for
        ):
            self.posts.append((url, body))
            response = httpx.Response(
                400,
                json={"ok": False, "error_code": 400, "description": self.description},
                request=httpx.Request("POST", url),
            )
            return response  # type: ignore[return-value]
        return await super().post(url, **kwargs)


async def test_genuine_400_on_retraction_still_fails_and_logs_a_safe_description(
    owner_resolver: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    token, _ = mint_token()
    http = _TelegramRefusal(
        _response(body={"data": {"status": "executed"}}),
        [{"presentation_id": _STALE_ID, "route_ref": "9001", "provider_ref": "55"}],
        description="Bad Request: message to edit not found",
    )
    http.refuse_text_edit_for = 55
    connector = _connector()
    connector._http_client = http  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING):
        assert await connector._maybe_handle_owner_delivery_callback(_update(token)) is True

    [(_, report)] = http.dashboard("/api/owner-delivery/retractions/")
    assert report == {"retracted": False, "reason": "provider_error"}
    assert "HTTP 400" in caplog.text
    assert "message to edit not found" in caplog.text
    assert "test-token" not in caplog.text


async def test_unsafe_400_description_is_not_logged_verbatim(
    owner_resolver: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    token, _ = mint_token()
    http = _TelegramRefusal(
        _response(body={"data": {"status": "executed"}}),
        [{"presentation_id": _STALE_ID, "route_ref": "9001", "provider_ref": "55"}],
        description="Bad Request: see https://api.telegram.org/bottest-token/x",
    )
    http.refuse_text_edit_for = 55
    connector = _connector()
    connector._http_client = http  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING):
        await connector._maybe_handle_owner_delivery_callback(_update(token))

    assert "test-token" not in caplog.text
    assert "api.telegram.org" not in caplog.text


async def test_token_shaped_400_description_is_not_logged(
    owner_resolver: AsyncMock, caplog: pytest.LogCaptureFixture
) -> None:
    """A phrase that passes the character filter but looks like a bot token is dropped."""
    leaked = "123456789:AAEhBOweik6ad9r_QXMENQjcrGbqCr4K-Ao"
    token, _ = mint_token()
    http = _TelegramRefusal(
        _response(body={"data": {"status": "executed"}}),
        [{"presentation_id": _STALE_ID, "route_ref": "9001", "provider_ref": "55"}],
        description=f"Bad Request: {leaked}",
    )
    http.refuse_text_edit_for = 55
    connector = _connector()
    connector._http_client = http  # type: ignore[assignment]

    with caplog.at_level(logging.WARNING):
        await connector._maybe_handle_owner_delivery_callback(_update(token))

    assert "HTTP 400" in caplog.text
    assert leaked not in caplog.text
    assert "AAEhBOweik6ad9r" not in caplog.text


_TAPPED_ID = "aaaaaaaa-0000-4000-8000-000000000044"


async def test_tapped_card_keeps_its_outcome_and_other_cards_still_retract(
    owner_resolver: AsyncMock,
) -> None:
    """The sweep lists the tapped card too; it must not overwrite the outcome it shows."""
    token, _ = mint_token()
    http = _Http(
        _response(body={"data": {"status": "executed"}}),
        [
            {"presentation_id": _TAPPED_ID, "route_ref": "9001", "provider_ref": "44"},
            {"presentation_id": _STALE_ID, "route_ref": "9001", "provider_ref": "55"},
        ],
    )
    connector = _connector()
    connector._http_client = http  # type: ignore[assignment]

    assert await connector._maybe_handle_owner_delivery_callback(_update(token)) is True

    edits = [(body["message_id"], body["text"]) for body in http.telegram("editMessageText")]
    assert [text for mid, text in edits if mid == 44] == ["✅ Approved"]
    assert [mid for mid, _ in edits if mid == 55] == [55]
    # The tapped card's own edit drops its buttons: no markup call at all.
    assert http.telegram("editMessageReplyMarkup") == []
    reports = {
        url.rsplit("/", 1)[-1]: body
        for url, body in http.dashboard("/api/owner-delivery/retractions/")
    }
    assert reports == {
        _TAPPED_ID: {"retracted": True, "reason": "outcome_shown"},
        _STALE_ID: {"retracted": True, "reason": None},
    }


async def test_apr1_tap_card_is_not_retracted_by_a_later_sweep(
    owner_resolver: AsyncMock,
) -> None:
    connector = _connector()
    http = _Http(
        _response(),
        [{"presentation_id": _TAPPED_ID, "route_ref": "9001", "provider_ref": "44"}],
    )
    connector._http_client = http  # type: ignore[assignment]

    await connector._edit_approval_callback_message(_update("x")["callback_query"], "approved")
    await connector._drain_owner_delivery_retractions()

    assert [b["text"] for b in http.telegram("editMessageText")] == ["✅ Approved"]
    assert http.telegram("editMessageReplyMarkup") == []


async def test_failed_tap_edit_leaves_the_card_to_the_sweep(owner_resolver: AsyncMock) -> None:
    http = _TelegramRefusal(
        _response(),
        [{"presentation_id": _TAPPED_ID, "route_ref": "9001", "provider_ref": "44"}],
        description="Bad Request: message to edit not found",
    )
    http.refuse_text_edit_for = 44
    connector = _connector()
    connector._http_client = http  # type: ignore[assignment]

    await connector._edit_approval_callback_message(_update("x")["callback_query"], "approved")
    await connector._drain_owner_delivery_retractions()

    [(_, report)] = http.dashboard("/api/owner-delivery/retractions/")
    assert report["reason"] != "outcome_shown"


async def test_tap_edit_waits_for_a_running_drain_so_the_outcome_lands_last(
    owner_resolver: AsyncMock,
) -> None:
    import asyncio

    http = _Http(
        _response(),
        [{"presentation_id": _TAPPED_ID, "route_ref": "9001", "provider_ref": "44"}],
    )
    connector = _connector()
    connector._http_client = http  # type: ignore[assignment]
    await connector._owner_delivery_drain_lock.acquire()  # a sweep is mid-drain

    edit = asyncio.create_task(
        connector._edit_approval_callback_message(_update("x")["callback_query"], "approved")
    )
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    assert http.telegram("editMessageText") == []
    connector._owner_delivery_drain_lock.release()
    await edit

    assert [b["text"] for b in http.telegram("editMessageText")] == ["✅ Approved"]
