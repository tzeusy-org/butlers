"""The Telegram connector retracts stale owner cards on a timer, not only after a tap."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from butlers.connectors import telegram_bot
from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig

pytestmark = pytest.mark.unit

_API = "http://dashboard-api:41200"


def _connector(*, api: str | None = _API, token: str | None = "test-only-token") -> Any:
    connector = TelegramBotConnector(
        TelegramBotConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            endpoint_identity="telegram:bot:1",
            telegram_token="test-token",
            internal_api_url=api,
            approval_callback_secret="test-only-secret",
            approval_callback_connector_token=token,
            owner_delivery_sweep_interval_s=0.01,
        ),
        db_pool=MagicMock(),
        cursor_pool=MagicMock(),
    )
    connector._mcp_client = AsyncMock()
    connector._http_client = AsyncMock()
    return connector


async def _until(predicate: Any) -> None:
    for _ in range(1000):
        if predicate():
            return
        await asyncio.sleep(0.005)
    raise AssertionError("condition never held")


async def test_sweep_keeps_retracting_without_any_tap_until_stopped() -> None:
    connector = _connector()
    drain = AsyncMock(return_value=0)
    connector._drain_owner_delivery_retractions = drain

    connector._start_owner_delivery_sweep()
    await _until(lambda: drain.await_count >= 3)
    await connector.stop()
    settled = drain.await_count
    await asyncio.sleep(0.05)

    assert drain.await_count == settled


async def test_sweep_survives_a_failed_drain() -> None:
    connector = _connector()
    drain = AsyncMock(side_effect=[RuntimeError("dashboard down"), 0, 0])
    connector._drain_owner_delivery_retractions = drain

    connector._start_owner_delivery_sweep()
    await _until(lambda: drain.await_count >= 2)
    await connector.stop()


@pytest.mark.parametrize(
    "api, token", [(None, "test-only-token"), (_API, None)], ids=["no api", "no credential"]
)
async def test_sweep_needs_the_dashboard_route_and_its_credential(api, token) -> None:
    connector = _connector(api=api, token=token)

    connector._start_owner_delivery_sweep()

    assert connector._owner_delivery_sweep_task is None
    await connector.stop()


async def test_starting_twice_runs_one_sweep() -> None:
    connector = _connector()
    connector._drain_owner_delivery_retractions = AsyncMock(return_value=0)

    connector._start_owner_delivery_sweep()
    first = connector._owner_delivery_sweep_task
    connector._start_owner_delivery_sweep()

    assert connector._owner_delivery_sweep_task is first
    await connector.stop()


async def test_a_tap_and_the_sweep_never_attempt_the_same_cards_at_once() -> None:
    """Overlapping drains would edit one card twice and receipt a false failure."""
    connector = _connector()
    inside = 0
    most = 0
    release = asyncio.Event()

    async def retract(_item: dict[str, Any]) -> tuple[bool, str | None]:
        nonlocal inside, most
        inside += 1
        most = max(most, inside)
        await release.wait()
        inside -= 1
        return True, None

    listing = MagicMock(status_code=200)
    listing.json.return_value = {"data": [{"presentation_id": "p1"}]}
    connector._http_client.get = AsyncMock(return_value=listing)
    connector._retract_owner_card = retract

    drains = [asyncio.create_task(connector._drain_owner_delivery_retractions()) for _ in range(2)]
    await _until(lambda: inside == 1)
    await asyncio.sleep(0.02)
    release.set()
    await asyncio.gather(*drains)

    assert most == 1


@pytest.mark.parametrize("value, expected", [(None, 60.0), ("15", 15.0)])
def test_sweep_interval_comes_from_the_environment(monkeypatch, value, expected) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
    monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "telegram:bot:1")
    if value is None:
        monkeypatch.delenv("CONNECTOR_OWNER_DELIVERY_SWEEP_INTERVAL_S", raising=False)
    else:
        monkeypatch.setenv("CONNECTOR_OWNER_DELIVERY_SWEEP_INTERVAL_S", value)

    assert TelegramBotConnectorConfig.from_env().owner_delivery_sweep_interval_s == expected


_CARD = {"presentation_id": "p1", "route_ref": "9001", "provider_ref": "55"}
_NOT_MODIFIED = {
    "ok": False,
    "error_code": 400,
    "description": "Bad Request: message is not modified: specified new message content and "
    "reply markup are exactly the same as a current content and reply markup of the message",
}
_GONE = {"ok": False, "error_code": 400, "description": "Bad Request: message to edit not found"}


def _provider(
    connector: Any, *, edit: Any = None, keyboard: Any = None, listing: int = 200
) -> list:
    """Real helpers over a real transport, so ``raise_for_status`` behaves as in production."""
    receipts: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/retractions"):
            return httpx.Response(listing, json={"data": [_CARD]})
        if "/retractions/" in url:
            receipts.append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"recorded": True}})
        body = edit if url.endswith("/editMessageText") else keyboard
        if body is None:
            return httpx.Response(200, json={"ok": True, "result": {}})
        return httpx.Response(400, json=body)

    connector._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return receipts


@pytest.mark.parametrize(
    "edit, keyboard",
    [
        pytest.param(None, _NOT_MODIFIED, id="the edit already dropped the keyboard"),
        pytest.param(_NOT_MODIFIED, None, id="a relisted card already shows the retracted text"),
        pytest.param(_NOT_MODIFIED, _NOT_MODIFIED, id="an earlier attempt finished the job"),
    ],
)
async def test_message_not_modified_is_a_retraction_that_worked(edit, keyboard) -> None:
    connector = _connector()
    receipts = _provider(connector, edit=edit, keyboard=keyboard)

    assert await connector._drain_owner_delivery_retractions() == 1

    assert receipts == [{"retracted": True, "reason": None}]


async def test_any_other_provider_refusal_is_receipted_and_logged_without_the_bot_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    connector = _connector()
    receipts = _provider(connector, edit=_GONE)

    with caplog.at_level(logging.WARNING):
        assert await connector._drain_owner_delivery_retractions() == 0

    assert receipts == [{"retracted": False, "reason": "provider_error"}]
    assert "p1" in caplog.text and "provider_error" in caplog.text and "400" in caplog.text
    assert "test-token" not in caplog.text


def _failing_provider(connector: Any, failure: int | Exception, cards: int = 2) -> dict[str, Any]:
    """Every Telegram call fails the same way, as in an outage; records what was attempted."""
    seen: dict[str, Any] = {"edits": 0, "receipts": [], "probes": 0, "listings": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/retractions"):
            seen["listings"] += 1
            data = [{**_CARD, "presentation_id": f"p{i}"} for i in range(cards)]
            return httpx.Response(200, json={"data": data})
        if "/retractions/" in url:
            seen["receipts"].append(json.loads(request.content))
            return httpx.Response(200, json={"data": {"recorded": True}})
        if url.endswith("/getMe"):
            seen["probes"] += 1
        else:
            seen["edits"] += 1
        if isinstance(failure, Exception):
            raise failure
        return httpx.Response(failure, json={"ok": False, "error_code": failure})

    connector._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return seen


@pytest.mark.parametrize(
    "failure",
    [
        pytest.param(500, id="provider 5xx"),
        pytest.param(502, id="provider gateway"),
        pytest.param(429, id="rate limited"),
        pytest.param(401, id="bot token rejected"),
        pytest.param(404, id="bot api path rejected"),
        pytest.param(httpx.ConnectError("no route"), id="unreachable"),
        pytest.param(httpx.ReadTimeout("slow"), id="timeout"),
    ],
)
async def test_an_outage_consumes_no_attempt_and_stops_the_drain(
    failure, caplog: pytest.LogCaptureFixture
) -> None:
    """Nothing about the card failed, so the bound on failed attempts must not move."""
    connector = _connector()
    seen = _failing_provider(connector, failure, cards=3)

    with caplog.at_level(logging.WARNING):
        assert await connector._drain_owner_delivery_retractions() == 0

    assert seen["receipts"] == []
    assert seen["edits"] == 2
    if isinstance(failure, int):
        assert str(failure) in caplog.text
    assert "test-token" not in caplog.text


@pytest.mark.parametrize("status", [400, 403])
async def test_a_refusal_of_one_card_is_receipted_and_the_drain_continues(status) -> None:
    connector = _connector()
    seen = _failing_provider(connector, status)

    assert await connector._drain_owner_delivery_retractions() == 0

    assert seen["receipts"] == [{"retracted": False, "reason": "provider_error"}] * 2


_OUTAGE = 500
_HEALTHY = 200
_NO_MESSAGE_ID = None


def _per_card_provider(
    connector: Any, answers: list[int | None], *, probe: int | Exception = _HEALTHY
) -> dict[str, Any]:
    """Each card gets its own Telegram answer; ``_NO_MESSAGE_ID`` cards never reach Telegram."""
    seen: dict[str, Any] = {"attempted": [], "receipts": {}, "probes": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url.endswith("/retractions"):
            data = [
                {
                    "presentation_id": f"p{i}",
                    "route_ref": "9001",
                    "provider_ref": None if answer is _NO_MESSAGE_ID else str(i),
                }
                for i, answer in enumerate(answers)
            ]
            return httpx.Response(200, json={"data": data})
        if "/retractions/" in url:
            seen["receipts"][url.rsplit("/", 1)[-1]] = json.loads(request.content)
            return httpx.Response(200, json={"data": {"recorded": True}})
        if url.endswith("/getMe"):
            seen["probes"] += 1
            if isinstance(probe, Exception):
                raise probe
            return httpx.Response(probe, json={"ok": probe == _HEALTHY, "result": {}})
        message_id = json.loads(request.content)["message_id"]
        if url.endswith("/editMessageText"):
            seen["attempted"].append(f"p{message_id}")
        status = answers[message_id]
        return httpx.Response(status, json={"ok": status == _HEALTHY, "result": {}})

    connector._http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return seen


async def test_one_card_answering_500_does_not_hold_up_the_card_behind_it() -> None:
    connector = _connector()
    seen = _per_card_provider(connector, [_OUTAGE, _HEALTHY])

    assert await connector._drain_owner_delivery_retractions() == 1

    assert seen["receipts"] == {"p1": {"retracted": True, "reason": None}}


@pytest.mark.parametrize(
    "probe",
    [
        pytest.param(500, id="health check 5xx"),
        pytest.param(429, id="health check rate limited"),
        pytest.param(401, id="health check token rejected"),
        pytest.param(httpx.ConnectError("no route"), id="health check unreachable"),
    ],
)
async def test_two_5xx_answers_stop_the_drain_when_telegram_is_really_down(probe) -> None:
    connector = _connector()
    seen = _per_card_provider(connector, [_OUTAGE, _OUTAGE, _HEALTHY], probe=probe)

    assert await connector._drain_owner_delivery_retractions() == 0

    assert seen["attempted"] == ["p0", "p1"]
    assert seen["probes"] == 1
    assert seen["receipts"] == {}


@pytest.mark.parametrize(
    "answers, probes",
    [
        pytest.param([_OUTAGE, _OUTAGE, _HEALTHY], 1, id="two bad cards at the head"),
        pytest.param([_OUTAGE, _OUTAGE, _OUTAGE, _OUTAGE, _HEALTHY], 2, id="four bad cards"),
    ],
)
async def test_adjacent_cards_answering_5xx_do_not_hold_up_the_cards_behind_them(
    answers, probes
) -> None:
    """Telegram answers its health check, so those 5xx concern those messages alone."""
    connector = _connector()
    seen = _per_card_provider(connector, answers)

    assert await connector._drain_owner_delivery_retractions() == 1

    assert seen["attempted"] == [f"p{i}" for i in range(len(answers))]
    assert seen["probes"] == probes
    # No receipts for the bad cards: an edit-only outage must still spare the bound.
    assert seen["receipts"] == {
        f"p{len(answers) - 1}": {"retracted": True, "reason": None},
    }


@pytest.mark.parametrize(
    "answers",
    [
        pytest.param([429, 429, _HEALTHY], id="rate limited"),
        pytest.param([_OUTAGE, 429, _HEALTHY], id="5xx then rate limited"),
        pytest.param([429, _OUTAGE, _HEALTHY], id="rate limited then 5xx"),
    ],
)
async def test_a_rate_limit_stops_the_drain_even_though_the_health_check_would_pass(
    answers,
) -> None:
    connector = _connector()
    seen = _per_card_provider(connector, answers)

    assert await connector._drain_owner_delivery_retractions() == 0

    assert seen["attempted"] == ["p0", "p1"]
    assert seen["probes"] == 0
    assert seen["receipts"] == {}


async def test_telegram_unreachable_is_not_excused_by_a_health_check() -> None:
    connector = _connector()
    seen = _failing_provider(connector, httpx.ConnectError("no route"), cards=3)

    assert await connector._drain_owner_delivery_retractions() == 0

    assert (seen["edits"], seen["probes"]) == (2, 0)


async def test_a_failed_health_check_never_logs_the_bot_token(
    caplog: pytest.LogCaptureFixture,
) -> None:
    connector = _connector()
    _per_card_provider(
        connector,
        [_OUTAGE, _OUTAGE],
        probe=httpx.ConnectError("https://api.telegram.org/bottest-token/getMe"),
    )

    with caplog.at_level(logging.DEBUG, logger="butlers.connectors.telegram_bot"):
        await connector._drain_owner_delivery_retractions()

    assert "test-token" not in caplog.text


_TOKEN_DRAINS = telegram_bot._RETRACTION_TOKEN_REJECTED_DRAINS_TO_STOP


@pytest.mark.parametrize("status", [401, 404])
async def test_a_rejected_token_stops_the_sweep_and_says_so_once(
    status, caplog: pytest.LogCaptureFixture
) -> None:
    connector = _connector()
    seen = _failing_provider(connector, status)

    with caplog.at_level(logging.ERROR):
        for _ in range(_TOKEN_DRAINS - 1):
            await connector._drain_owner_delivery_retractions()
        assert caplog.records == []
        await connector._drain_owner_delivery_retractions()
    assert seen["listings"] == _TOKEN_DRAINS

    with caplog.at_level(logging.ERROR):
        for _ in range(3):
            assert await connector._drain_owner_delivery_retractions() == 0

    assert seen["listings"] == _TOKEN_DRAINS
    assert seen["edits"] == 2 * _TOKEN_DRAINS
    assert seen["receipts"] == []
    errors = [record for record in caplog.records if record.levelno == logging.ERROR]
    assert len(errors) == 1
    assert "restart" in errors[0].getMessage()
    assert "test-token" not in caplog.text


async def test_a_real_telegram_answer_clears_the_rejected_token_count() -> None:
    connector = _connector()
    seen = _failing_provider(connector, 401)
    for _ in range(_TOKEN_DRAINS - 1):
        await connector._drain_owner_delivery_retractions()

    _provider(connector)
    assert await connector._drain_owner_delivery_retractions() == 1

    seen = _failing_provider(connector, 401)
    for _ in range(_TOKEN_DRAINS - 1):
        await connector._drain_owner_delivery_retractions()
    await connector._drain_owner_delivery_retractions()
    assert seen["listings"] == _TOKEN_DRAINS
    await connector._drain_owner_delivery_retractions()
    assert seen["listings"] == _TOKEN_DRAINS


async def test_a_drain_with_a_real_answer_does_not_count_against_the_token() -> None:
    connector = _connector()
    for _ in range(_TOKEN_DRAINS + 1):
        _per_card_provider(connector, [401, _HEALTHY])
        assert await connector._drain_owner_delivery_retractions() == 1


@pytest.mark.parametrize(
    "answers",
    [
        pytest.param([401, 401], id="the edits were refused but getMe passes"),
        pytest.param([_OUTAGE, _OUTAGE, 401], id="getMe passed earlier in the drain"),
    ],
)
async def test_a_token_telegram_accepts_never_stops_the_sweep(answers) -> None:
    connector = _connector()
    for _ in range(_TOKEN_DRAINS + 1):
        seen = _per_card_provider(connector, answers)
        await connector._drain_owner_delivery_retractions()
        assert seen["attempted"]


async def test_a_drain_with_nothing_to_retract_says_nothing_about_the_token() -> None:
    connector = _connector()
    _failing_provider(connector, 401)
    for _ in range(_TOKEN_DRAINS - 1):
        await connector._drain_owner_delivery_retractions()

    seen = _failing_provider(connector, 401, cards=0)
    for _ in range(3):
        await connector._drain_owner_delivery_retractions()
    assert seen["listings"] == 3

    _failing_provider(connector, 401)
    await connector._drain_owner_delivery_retractions()
    seen = _failing_provider(connector, 401)
    await connector._drain_owner_delivery_retractions()
    assert seen["listings"] == 0


async def test_a_real_answer_between_outages_keeps_the_drain_going() -> None:
    connector = _connector()
    seen = _per_card_provider(connector, [_OUTAGE, _HEALTHY, _OUTAGE, _HEALTHY])

    assert await connector._drain_owner_delivery_retractions() == 2

    assert seen["attempted"] == ["p0", "p1", "p2", "p3"]
    assert set(seen["receipts"]) == {"p1", "p3"}


async def test_a_refusal_between_outages_is_a_real_answer() -> None:
    connector = _connector()
    seen = _per_card_provider(connector, [_OUTAGE, 400, _OUTAGE, _HEALTHY])

    assert await connector._drain_owner_delivery_retractions() == 1

    assert seen["attempted"] == ["p0", "p1", "p2", "p3"]
    assert seen["receipts"]["p1"] == {"retracted": False, "reason": "provider_error"}


async def test_a_card_that_never_reached_telegram_is_no_answer_between_outages() -> None:
    connector = _connector()
    seen = _per_card_provider(
        connector, [_OUTAGE, _NO_MESSAGE_ID, _OUTAGE, _HEALTHY], probe=_OUTAGE
    )

    assert await connector._drain_owner_delivery_retractions() == 0

    assert seen["attempted"] == ["p0", "p2"]
    assert seen["receipts"] == {"p1": {"retracted": False, "reason": "provider_ref_missing"}}


async def test_an_unexpected_failure_is_receipted_and_logged_by_type_only(
    caplog: pytest.LogCaptureFixture,
) -> None:
    connector = _connector()
    seen = _failing_provider(connector, RuntimeError("test-token in the text"))

    with caplog.at_level(logging.WARNING):
        assert await connector._drain_owner_delivery_retractions() == 0

    assert seen["receipts"] == [{"retracted": False, "reason": "provider_error"}] * 2
    assert "RuntimeError" in caplog.text
    assert "test-token" not in caplog.text


async def test_recovery_after_an_outage_retracts_with_no_failure_on_record() -> None:
    connector = _connector()
    seen = _failing_provider(connector, 503, cards=1)
    assert await connector._drain_owner_delivery_retractions() == 0

    receipts = _provider(connector)
    assert await connector._drain_owner_delivery_retractions() == 1

    assert seen["receipts"] == []
    assert receipts == [{"retracted": True, "reason": None}]


async def test_a_connector_with_the_sweep_disabled_attempts_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Listing is not scoped to a bot, so a second bot's connector must be able to stand aside."""
    connector = _connector()
    connector._config.owner_delivery_sweep_enabled = False
    seen = _failing_provider(connector, 400)

    with caplog.at_level(logging.INFO):
        connector._start_owner_delivery_sweep()
    assert connector._owner_delivery_sweep_task is None
    assert "sweep disabled" in caplog.text
    assert await connector._drain_owner_delivery_retractions() == 0

    assert seen == {"edits": 0, "receipts": [], "probes": 0, "listings": 0}


@pytest.mark.parametrize(
    "value, expected",
    [(None, True), ("true", True), ("1", True), ("false", False), ("0", False), ("no", False)],
)
def test_the_sweep_can_be_disabled_from_the_environment(monkeypatch, value, expected) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
    monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "telegram:bot:1")
    if value is None:
        monkeypatch.delenv("CONNECTOR_OWNER_DELIVERY_SWEEP_ENABLED", raising=False)
    else:
        monkeypatch.setenv("CONNECTOR_OWNER_DELIVERY_SWEEP_ENABLED", value)

    assert TelegramBotConnectorConfig.from_env().owner_delivery_sweep_enabled is expected


async def test_a_refused_listing_is_logged_with_its_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    connector = _connector()
    receipts = _provider(connector, listing=503)

    with caplog.at_level(logging.WARNING):
        assert await connector._drain_owner_delivery_retractions() == 0

    assert receipts == []
    assert "503" in caplog.text


@pytest.mark.parametrize("value", ["0", "-5", "nan", "inf"])
def test_a_sweep_interval_that_would_spin_or_never_run_is_refused(monkeypatch, value) -> None:
    monkeypatch.setenv("SWITCHBOARD_MCP_URL", "http://localhost:41100/sse")
    monkeypatch.setenv("CONNECTOR_ENDPOINT_IDENTITY", "telegram:bot:1")
    monkeypatch.setenv("CONNECTOR_OWNER_DELIVERY_SWEEP_INTERVAL_S", value)

    with pytest.raises(ValueError, match="CONNECTOR_OWNER_DELIVERY_SWEEP_INTERVAL_S"):
        TelegramBotConnectorConfig.from_env()
