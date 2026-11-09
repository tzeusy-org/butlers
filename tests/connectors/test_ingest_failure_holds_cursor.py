"""A failed ingest never moves a connector past the item (bu-dhloi1.149, slice 1).

For Gmail backfill, Google Calendar sync, ActivityWatch live and the Telegram
user client: a failed ingest keeps the cursor, window or checkpoint before the
item, so the next cycle reads it again. Each item gets a bounded number of
attempts (``IngestRetryBudget``). When they are spent, the item is parked as a
replayable ``filtered_events`` error row carrying the exact envelope it tried,
idempotency key included, and only then does the connector move on.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.filtered_event_buffer import FilteredEventBuffer
from butlers.connectors.ingest_retry import IngestRetryBudget

# row tuple layout: see FilteredEventBuffer.record
_ID, _STATUS, _REASON, _PAYLOAD = 3, 8, 7, 9


# ── The budget and the stored payload ─────────────────────────────────────


def test_the_budget_parks_on_the_last_attempt_and_forgets_it() -> None:
    budget = IngestRetryBudget(3)
    assert [budget.failed("a"), budget.failed("a")] == [False, False]
    assert budget.attempts("a") == 2
    assert budget.failed("a") is True
    assert budget.attempts("a") == 0 and len(budget) == 0
    budget.failed("b")
    budget.succeeded("b")
    assert len(budget) == 0
    with pytest.raises(ValueError):
        IngestRetryBudget(0)


def test_full_payload_keeps_the_idempotency_key_for_replay() -> None:
    stored = FilteredEventBuffer.full_payload(
        channel="email",
        provider="gmail",
        endpoint_identity="gmail:user:a@example.test",
        external_event_id="m1",
        external_thread_id=None,
        observed_at="2026-01-01T00:00:00+00:00",
        sender_identity="a@example.test",
        raw={},
        idempotency_key="gmail:m1",
    )
    assert stored["control"]["idempotency_key"] == "gmail:m1"


def test_a_stored_envelope_is_the_submission_minus_its_schema_version() -> None:
    envelope = {
        "schema_version": "ingest.v1",
        "source": {"channel": "email"},
        "control": {"idempotency_key": "k", "policy_tier": "default"},
    }
    stored = FilteredEventBuffer.stored_envelope(envelope)
    assert stored == {"source": {"channel": "email"}, "control": envelope["control"]}
    assert "schema_version" in envelope  # the caller's envelope is untouched


# ── Gmail backfill ──────────────────────────────────────────────────────────


def _gmail_runtime(submit: AsyncMock) -> Any:
    from butlers.connectors.gmail import GmailConnectorConfig, GmailConnectorRuntime

    config = GmailConnectorConfig(
        switchboard_mcp_url="http://localhost:41100/sse",
        connector_provider="gmail",
        connector_channel="email",
        connector_endpoint_identity="gmail:user:test@example.com",
        connector_max_inflight=4,
        gmail_client_id="test-client-id",
        gmail_client_secret="test-client-secret",
        gmail_refresh_token="test-refresh-token",
        gmail_watch_renew_interval_s=3600,
        gmail_poll_interval_s=5,
    )
    runtime = GmailConnectorRuntime(config, cursor_pool=MagicMock())
    runtime._fetch_message = AsyncMock(side_effect=lambda mid: {"id": mid})  # type: ignore[method-assign]
    runtime._build_ingestion_envelope = MagicMock(  # type: ignore[method-assign]
        return_value=SimpleNamespace(known_contact=None)
    )
    runtime._known_contact_signal = MagicMock(return_value=None)  # type: ignore[method-assign]
    runtime._get_from_header = MagicMock(return_value="a@example.test")  # type: ignore[method-assign]
    runtime._ingestion_policy = MagicMock()
    runtime._ingestion_policy.evaluate = MagicMock(return_value=SimpleNamespace(allowed=True))
    runtime._global_ingestion_policy = MagicMock()
    runtime._global_ingestion_policy.evaluate = MagicMock(
        return_value=SimpleNamespace(action="pass_through")
    )
    runtime._apply_global_action_tier = MagicMock(side_effect=lambda p, _g: p)  # type: ignore[method-assign]

    async def build(message_data: dict, **_kw: Any) -> dict:
        mid = message_data["id"]
        return {
            "schema_version": "ingest.v1",
            "source": {"channel": "email", "provider": "gmail"},
            "event": {"external_event_id": mid},
            "sender": {"identity": "a@example.test"},
            "payload": {"raw": {"id": mid}, "normalized_text": f"text {mid}"},
            "control": {"idempotency_key": f"gmail:{mid}"},
        }

    runtime._build_ingest_envelope = AsyncMock(side_effect=build)  # type: ignore[method-assign]
    runtime._submit_to_ingest_api = submit  # type: ignore[method-assign]
    runtime._mcp_client = MagicMock()
    runtime._mcp_client.call_tool = AsyncMock(return_value={"status": "active"})
    return runtime


def _reports(runtime: Any) -> list[dict]:
    return [args for name, args in (c.args for c in runtime._mcp_client.call_tool.await_args_list)]


@pytest.fixture
def evaluated():
    with patch(
        "butlers.connectors.gmail.evaluate_message_policy",
        return_value=SimpleNamespace(should_ingest=True, filter_reason=None),
    ):
        yield


def _job(cursor: dict | None = None) -> Any:
    from butlers.connectors.gmail import BackfillJob

    return BackfillJob(job_id="job-1", date_from="2020-01-01", date_to="2020-12-31", cursor=cursor)


async def test_gmail_backfill_holds_its_page_on_a_failed_ingest(evaluated) -> None:
    failures = {"m2": 1}

    async def submit(envelope: dict) -> None:
        mid = envelope["event"]["external_event_id"]
        if failures.get(mid, 0) > 0:
            failures[mid] -= 1
            raise RuntimeError("switchboard down")

    runtime = _gmail_runtime(AsyncMock(side_effect=submit))
    runtime._fetch_backfill_message_page = AsyncMock(  # type: ignore[method-assign]
        return_value=([{"id": "m1"}, {"id": "m2"}, {"id": "m3"}], "p2")
    )

    await runtime._execute_backfill_job(_job())

    # Stopped at m2: m3 untried, the job not completed, the cursor still on page 1.
    submitted = [
        c.args[0]["event"]["external_event_id"]
        for c in runtime._submit_to_ingest_api.await_args_list
    ]
    assert submitted == ["m1", "m2"]
    reports = _reports(runtime)
    assert all(r.get("status") != "completed" for r in reports)
    assert reports[-1]["cursor"] == {"page_token": ""}
    assert reports[-1]["rows_skipped"] == 0
    assert len(runtime._filtered_event_buffer) == 0

    # The next poll resumes the same page; m2 goes through this time.
    runtime._fetch_backfill_message_page = AsyncMock(  # type: ignore[method-assign]
        side_effect=[([{"id": "m1"}, {"id": "m2"}, {"id": "m3"}], None)]
    )
    await runtime._execute_backfill_job(_job(reports[-1]["cursor"]))
    assert _reports(runtime)[-1]["status"] == "completed"
    assert runtime._backfill_ingest_retry.attempts("m2") == 0


async def test_gmail_backfill_parks_an_item_whose_retries_are_spent(evaluated) -> None:
    async def submit(envelope: dict) -> None:
        if envelope["event"]["external_event_id"] == "m2":
            raise RuntimeError("switchboard down")

    runtime = _gmail_runtime(AsyncMock(side_effect=submit))
    for _ in range(runtime._backfill_ingest_retry.max_attempts):
        runtime._fetch_backfill_message_page = AsyncMock(  # type: ignore[method-assign]
            return_value=([{"id": "m1"}, {"id": "m2"}, {"id": "m3"}], None)
        )
        await runtime._execute_backfill_job(_job())

    assert _reports(runtime)[-1]["status"] == "completed"
    assert len(runtime._filtered_event_buffer) == 1
    row = runtime._filtered_event_buffer._rows[0]
    assert (row[_ID], row[_STATUS], row[_REASON]) == ("m2", "error", "submission_error")
    assert row[_PAYLOAD]["control"]["idempotency_key"] == "gmail:m2"
    assert row[_PAYLOAD]["payload"]["normalized_text"] == "text m2"


# ── Google Calendar sync ──────────────────────────────────────────────────


def _calendar_runtime(submit: AsyncMock) -> Any:
    from butlers.connectors.google_calendar import CalendarAccountConfig, CalendarConnectorRuntime

    runtime = CalendarConnectorRuntime(
        CalendarAccountConfig(
            email="test@example.com",
            client_id="client-id",
            client_secret="client-secret",
            refresh_token="refresh-token",
            switchboard_mcp_url="http://localhost:41100/sse",
            poll_interval_s=60,
            starting_soon_lead_minutes=15,
            starting_soon_window_hours=2,
        )
    )
    runtime._running = True
    runtime._sync_token = "tok-1"
    runtime._ingestion_policy = MagicMock()
    runtime._ingestion_policy.evaluate = MagicMock(return_value=None)
    runtime._global_ingestion_policy = MagicMock()
    runtime._global_ingestion_policy.evaluate = MagicMock(return_value=None)
    runtime._submit_to_ingest_api = submit  # type: ignore[method-assign]
    runtime._save_sync_token = AsyncMock()  # type: ignore[method-assign]
    runtime._flush_filtered_events = AsyncMock()  # type: ignore[method-assign]
    runtime._check_starting_soon = AsyncMock()  # type: ignore[method-assign]
    return runtime


def _calendar_event(event_id: str) -> dict[str, Any]:
    return {
        "id": event_id,
        "status": "confirmed",
        "summary": f"Meeting {event_id}",
        "start": {"dateTime": "2026-06-01T10:00:00Z"},
        "end": {"dateTime": "2026-06-01T11:00:00Z"},
        "created": "2026-01-01T00:00:00Z",
        "updated": "2026-01-02T00:00:00Z",
        "organizer": {"email": "org@example.com"},
    }


async def test_calendar_keeps_its_sync_token_on_a_failed_ingest() -> None:
    failures = {"e2": 1}

    async def submit(envelope: dict) -> None:
        eid = envelope["event"]["external_event_id"]
        if failures.get(eid, 0) > 0:
            failures[eid] -= 1
            raise RuntimeError("switchboard down")

    runtime = _calendar_runtime(AsyncMock(side_effect=submit))
    events = [_calendar_event("e1"), _calendar_event("e2"), _calendar_event("e3")]
    runtime._incremental_sync = AsyncMock(return_value=(events, "tok-2"))  # type: ignore[method-assign]

    await runtime._run_one_poll_cycle()
    assert runtime._sync_token == "tok-1"
    runtime._save_sync_token.assert_not_awaited()
    assert len(runtime._filtered_event_buffer) == 0
    submitted = [
        c.args[0]["event"]["external_event_id"]
        for c in runtime._submit_to_ingest_api.await_args_list
    ]
    assert submitted == ["e1", "e2"]  # stopped at the failed event

    await runtime._run_one_poll_cycle()
    assert runtime._sync_token == "tok-2"
    runtime._save_sync_token.assert_awaited_once_with("tok-2")


async def test_calendar_parks_an_event_whose_retries_are_spent() -> None:
    async def submit(envelope: dict) -> None:
        if envelope["event"]["external_event_id"] == "e2":
            raise RuntimeError("switchboard down")

    runtime = _calendar_runtime(AsyncMock(side_effect=submit))
    events = [_calendar_event("e1"), _calendar_event("e2"), _calendar_event("e3")]
    runtime._incremental_sync = AsyncMock(return_value=(events, "tok-2"))  # type: ignore[method-assign]

    for _ in range(runtime._ingest_retry.max_attempts):
        await runtime._run_one_poll_cycle()

    assert runtime._sync_token == "tok-2"
    assert len(runtime._filtered_event_buffer) == 1
    row = runtime._filtered_event_buffer._rows[0]
    assert (row[_ID], row[_STATUS]) == ("e2", "error")
    key = row[_PAYLOAD]["control"]["idempotency_key"]
    assert key == "gcal:google_calendar:user:test@example.com:e2:2026-01-02T00:00:00Z"
    assert row[_PAYLOAD]["payload"]["raw"]["id"] == "e2"


async def test_calendar_full_sync_keeps_the_old_token_on_a_failed_ingest() -> None:
    async def submit(envelope: dict) -> None:
        raise RuntimeError("switchboard down")

    runtime = _calendar_runtime(AsyncMock(side_effect=submit))
    runtime._gcal_api_get = AsyncMock(  # type: ignore[method-assign]
        return_value={"items": [_calendar_event("e1")], "nextSyncToken": "tok-full"}
    )
    await runtime._perform_full_sync(ingest_events=True)
    assert runtime._sync_token == "tok-1"
    runtime._save_sync_token.assert_not_awaited()


# ── ActivityWatch live ──────────────────────────────────────────────────────

_AW_TS = datetime(2026, 7, 5, 10, 0, 0, tzinfo=UTC)
_AW_BUCKET = "aw-watcher-window_desktop"


def _aw_connector(call_tool: AsyncMock) -> Any:
    from butlers.connectors.activitywatch import (
        ActivityWatchConnector,
        ActivityWatchConnectorConfig,
    )

    connector = ActivityWatchConnector(
        ActivityWatchConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse", machine_id="desktop"
        )
    )
    connector._http_client = MagicMock()
    connector._last_checkpoint_ts = _AW_TS - timedelta(minutes=1)
    connector._mcp_client.call_tool = call_tool  # type: ignore[method-assign]
    connector._save_checkpoint = AsyncMock()  # type: ignore[method-assign]
    return connector


def _aw_events() -> list[dict[str, Any]]:
    return [
        {
            "timestamp": (_AW_TS + timedelta(minutes=i)).isoformat(),
            "duration": 120,
            "data": {"app": "Code", "title": f"window {i}"},
        }
        for i in range(3)
    ]


async def _aw_cycle(connector: Any) -> None:
    with (
        patch(
            "butlers.connectors.activitywatch.fetch_buckets",
            new=AsyncMock(return_value={_AW_BUCKET: {"type": "currentwindow"}}),
        ),
        patch(
            "butlers.connectors.activitywatch.fetch_events",
            new=AsyncMock(return_value=_aw_events()),
        ),
    ):
        await connector._execute_poll_cycle()


def _aw_submitted_ts(call_tool: AsyncMock) -> list[str]:
    return [c.args[1]["event"]["external_event_id"] for c in call_tool.await_args_list]


async def test_activitywatch_checkpoint_stays_before_a_failed_event() -> None:
    failing = f"{_AW_BUCKET}:{(_AW_TS + timedelta(minutes=1)).isoformat()}"
    failures = {failing: 1}

    async def call_tool(_name: str, envelope: dict) -> dict:
        eid = envelope["event"]["external_event_id"]
        if failures.get(eid, 0) > 0:
            failures[eid] -= 1
            raise RuntimeError("switchboard down")
        return {"status": "accepted"}

    tool = AsyncMock(side_effect=call_tool)
    connector = _aw_connector(tool)
    await _aw_cycle(connector)

    # Checkpoint moved to the first event only, never past the failed second one.
    connector._save_checkpoint.assert_awaited_once_with(_AW_TS)
    assert len(_aw_submitted_ts(tool)) == 2
    assert len(connector._filtered_event_buffer) == 0

    connector._last_checkpoint_ts = _AW_TS
    await _aw_cycle(connector)
    connector._save_checkpoint.assert_awaited_with(_AW_TS + timedelta(minutes=2))


async def test_activitywatch_holds_at_a_timestamp_shared_with_an_ingested_event() -> None:
    """A failed event sharing its timestamp with an ingested one holds at that timestamp."""
    events = [
        {"timestamp": _AW_TS.isoformat(), "duration": 120, "data": {"app": "Code", "title": t}}
        for t in ("first", "second")
    ] + [
        {
            "timestamp": (_AW_TS + timedelta(minutes=1)).isoformat(),
            "duration": 120,
            "data": {"app": "Code", "title": "later"},
        }
    ]
    tool = AsyncMock(side_effect=[{"status": "accepted"}, RuntimeError("switchboard down")])
    connector = _aw_connector(tool)
    with (
        patch(
            "butlers.connectors.activitywatch.fetch_buckets",
            new=AsyncMock(return_value={_AW_BUCKET: {"type": "currentwindow"}}),
        ),
        patch("butlers.connectors.activitywatch.fetch_events", new=AsyncMock(return_value=events)),
    ):
        await connector._execute_poll_cycle()

    # The checkpoint stays at the shared timestamp, where the next cycle's
    # ``start`` fetch begins, and never reaches the later event.
    connector._save_checkpoint.assert_awaited_once_with(_AW_TS)
    assert tool.await_count == 2
    assert len(connector._filtered_event_buffer) == 0


async def test_activitywatch_parks_an_event_whose_retries_are_spent() -> None:
    failing = f"{_AW_BUCKET}:{(_AW_TS + timedelta(minutes=1)).isoformat()}"

    async def call_tool(_name: str, envelope: dict) -> dict:
        if envelope["event"]["external_event_id"] == failing:
            raise RuntimeError("switchboard down")
        return {"status": "accepted"}

    connector = _aw_connector(AsyncMock(side_effect=call_tool))
    for _ in range(connector._ingest_retry.max_attempts):
        await _aw_cycle(connector)

    connector._save_checkpoint.assert_awaited_with(_AW_TS + timedelta(minutes=2))
    assert len(connector._filtered_event_buffer) == 1
    row = connector._filtered_event_buffer._rows[0]
    assert (row[_ID], row[_STATUS]) == (failing, "error")
    key = row[_PAYLOAD]["control"]["idempotency_key"]
    assert (
        key == f"activitywatch:desktop:{_AW_BUCKET}:{(_AW_TS + timedelta(minutes=1)).isoformat()}"
    )


# ── Telegram user client ──────────────────────────────────────────────────


def _telegram_connector() -> Any:
    from butlers.connectors.telegram_user_client import (
        TelegramUserClientConnector,
        TelegramUserClientConnectorConfig,
    )

    connector = TelegramUserClientConnector(
        TelegramUserClientConnectorConfig(
            switchboard_mcp_url="http://localhost:41100/sse",
            provider="telegram",
            channel="telegram_user_client",
            endpoint_identity="telegram_user_client:telegram:user123",
        ),
        cursor_pool=MagicMock(),
    )
    allow = SimpleNamespace(allowed=True, action="pass_through", reason="", matched_rule_type=None)
    connector._ingestion_policy.evaluate = MagicMock(return_value=allow)  # type: ignore[method-assign]
    connector._global_ingestion_policy.evaluate = MagicMock(return_value=allow)  # type: ignore[method-assign]
    connector._discretion_dispatcher = None
    connector._flush_and_drain = AsyncMock()  # type: ignore[method-assign]
    connector._save_checkpoint = AsyncMock()  # type: ignore[method-assign]
    return connector


def _telegram_message(msg_id: int, chat_id: int = 100) -> MagicMock:
    msg = MagicMock()
    msg.id = msg_id
    msg.chat_id = chat_id
    msg.sender_id = 999
    msg.message = f"hello {msg_id}"
    msg.media = None
    msg.reply_to = None
    msg.to_dict = lambda: {"id": msg_id, "message": f"hello {msg_id}"}
    return msg


async def test_telegram_user_holds_its_checkpoint_and_retries_a_failed_message() -> None:
    connector = _telegram_connector()
    failures = {"5": 1}

    async def submit(envelope: dict) -> None:
        eid = envelope["event"]["external_event_id"]
        if failures.get(eid, 0) > 0:
            failures[eid] -= 1
            raise RuntimeError("switchboard down")

    connector._submit_to_ingest = AsyncMock(side_effect=submit)  # type: ignore[method-assign]
    await connector._process_message(_telegram_message(4))
    await connector._process_message(_telegram_message(5))
    await connector._process_message(_telegram_message(6))

    # 6 went through, but the checkpoint stays below the pending 5.
    assert connector._last_message_id == 4
    assert set(connector._retry_pending) == {"100:5"}
    assert len(connector._filtered_event_buffer) == 0

    await connector._retry_pending_messages()
    assert connector._retry_pending == {}
    assert connector._last_message_id == 6


async def test_telegram_user_parks_a_message_whose_retries_are_spent() -> None:
    connector = _telegram_connector()

    async def submit(envelope: dict) -> None:
        if envelope["event"]["external_event_id"] == "5":
            raise RuntimeError("switchboard down")

    connector._submit_to_ingest = AsyncMock(side_effect=submit)  # type: ignore[method-assign]
    await connector._process_message(_telegram_message(5))
    for _ in range(connector._ingest_retry.max_attempts - 1):
        await connector._retry_pending_messages()

    assert connector._retry_pending == {}
    assert connector._last_message_id == 5  # parked, so no longer holding the checkpoint
    assert len(connector._filtered_event_buffer) == 1
    row = connector._filtered_event_buffer._rows[0]
    assert (row[_ID], row[_STATUS]) == ("5", "error")
    assert row[_PAYLOAD]["control"]["idempotency_key"] == "tg:100:5"
    assert row[_PAYLOAD]["payload"]["raw"] == {"id": 5, "message": "hello 5"}
