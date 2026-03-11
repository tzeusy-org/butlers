"""Integration tests for FilteredEventBuffer wiring in TelegramBotConnector.

Covers acceptance criteria from bu-6kvk.4:
- Telegram bot connector instantiates FilteredEventBuffer
- Filtered/errored events recorded at each connector's filter points
  (connector-scope rule block, global-scope rule skip)
- Error events recorded with status=error and error_detail
- Batch flush executes after each poll cycle
- Replay drain processes up to 10 pending items per cycle with FOR UPDATE SKIP LOCKED
- Successful replay transitions status to replay_complete
- Failed replay transitions status to replay_failed with error_detail
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.connectors.filtered_event_buffer import FilteredEventBuffer
from butlers.connectors.telegram_bot import TelegramBotConnector, TelegramBotConnectorConfig

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def bot_config() -> TelegramBotConnectorConfig:
    return TelegramBotConnectorConfig(
        switchboard_mcp_url="http://localhost:40100/sse",
        provider="telegram",
        channel="telegram",
        endpoint_identity="test_bot",
        telegram_token="test-token",
        poll_interval_s=0.1,
        max_inflight=4,
    )


@pytest.fixture
def mock_db_pool() -> MagicMock:
    """Mock asyncpg pool that tracks executemany/execute/fetch calls."""
    mock_conn = AsyncMock()
    mock_conn.executemany = AsyncMock(return_value=None)
    mock_conn.execute = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=[])

    # Support conn.transaction() as an async context manager (used by replay drain)
    mock_txn_ctx = AsyncMock()
    mock_txn_ctx.__aenter__ = AsyncMock(return_value=None)
    mock_txn_ctx.__aexit__ = AsyncMock(return_value=None)
    mock_conn.transaction = MagicMock(return_value=mock_txn_ctx)

    mock_ctx = AsyncMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    mock_pool = MagicMock()
    mock_pool.acquire = MagicMock(return_value=mock_ctx)
    mock_pool.execute = AsyncMock(return_value=None)
    return mock_pool


@pytest.fixture
def mock_cursor_pool() -> MagicMock:
    return MagicMock()


@pytest.fixture
def connector(
    bot_config: TelegramBotConnectorConfig,
    mock_db_pool: MagicMock,
    mock_cursor_pool: MagicMock,
) -> TelegramBotConnector:
    return TelegramBotConnector(bot_config, db_pool=mock_db_pool, cursor_pool=mock_cursor_pool)


def _make_update(
    *,
    update_id: int = 12345,
    chat_id: int = 987654321,
    from_id: int = 111222333,
    text: str = "Hello world",
) -> dict[str, Any]:
    """Build a minimal Telegram bot update dict."""
    return {
        "update_id": update_id,
        "message": {
            "message_id": 1,
            "from": {"id": from_id, "first_name": "Test", "username": "testuser"},
            "chat": {"id": chat_id, "type": "private"},
            "date": 1708012800,
            "text": text,
        },
    }


# ---------------------------------------------------------------------------
# Buffer initialisation
# ---------------------------------------------------------------------------


class TestBufferInitialisation:
    def test_connector_has_filtered_event_buffer(self, connector: TelegramBotConnector) -> None:
        assert hasattr(connector, "_filtered_event_buffer")
        assert isinstance(connector._filtered_event_buffer, FilteredEventBuffer)

    def test_buffer_starts_empty(self, connector: TelegramBotConnector) -> None:
        assert len(connector._filtered_event_buffer) == 0

    def test_db_pool_stored(self, connector: TelegramBotConnector, mock_db_pool: MagicMock) -> None:
        assert connector._db_pool is mock_db_pool


# ---------------------------------------------------------------------------
# Filter point 1: connector-scope rule block
# ---------------------------------------------------------------------------


class TestConnectorRuleRecording:
    async def test_connector_rule_block_records_event(
        self, connector: TelegramBotConnector
    ) -> None:
        """Update blocked by connector-scope ingestion rule is recorded in the buffer."""
        update = _make_update(update_id=10001)

        mock_block_decision = MagicMock()
        mock_block_decision.allowed = False
        mock_block_decision.reason = "chat_id match -> block"
        mock_block_decision.matched_rule_type = "chat_id"

        with patch.object(
            connector._ingestion_policy,
            "evaluate",
            return_value=mock_block_decision,
        ):
            await connector._process_update(update)

        assert len(connector._filtered_event_buffer) == 1
        row = connector._filtered_event_buffer._rows[0]
        # external_message_id is index 3
        assert row[3] == "10001"
        # filter_reason is index 7
        assert row[7] == "connector_rule:block:chat_id"
        # status is index 8
        assert row[8] == "filtered"
        # error_detail is index 10
        assert row[10] is None

    async def test_connector_rule_block_unknown_rule_type(
        self, connector: TelegramBotConnector
    ) -> None:
        """When matched_rule_type is None, uses 'unknown' as the rule_type component."""
        update = _make_update(update_id=10002)

        mock_block_decision = MagicMock()
        mock_block_decision.allowed = False
        mock_block_decision.reason = "block"
        mock_block_decision.matched_rule_type = None

        with patch.object(
            connector._ingestion_policy,
            "evaluate",
            return_value=mock_block_decision,
        ):
            await connector._process_update(update)

        row = connector._filtered_event_buffer._rows[0]
        assert row[7] == "connector_rule:block:unknown"


# ---------------------------------------------------------------------------
# Filter point 2: global-scope rule skip
# ---------------------------------------------------------------------------


class TestGlobalRuleRecording:
    async def test_global_rule_skip_records_event(self, connector: TelegramBotConnector) -> None:
        """Update skipped by global ingestion rule is recorded in the buffer."""
        update = _make_update(update_id=20001)

        mock_pass_decision = MagicMock()
        mock_pass_decision.allowed = True

        mock_skip_decision = MagicMock()
        mock_skip_decision.action = "skip"
        mock_skip_decision.reason = "sender_domain match -> skip"
        mock_skip_decision.matched_rule_type = "sender_domain"

        with (
            patch.object(
                connector._ingestion_policy,
                "evaluate",
                return_value=mock_pass_decision,
            ),
            patch.object(
                connector._global_ingestion_policy,
                "evaluate",
                return_value=mock_skip_decision,
            ),
        ):
            await connector._process_update(update)

        assert len(connector._filtered_event_buffer) == 1
        row = connector._filtered_event_buffer._rows[0]
        assert row[3] == "20001"
        assert row[7] == "global_rule:skip:sender_domain"
        assert row[8] == "filtered"


# ---------------------------------------------------------------------------
# Error recording
# ---------------------------------------------------------------------------


class TestErrorRecording:
    async def test_exception_records_error_event(self, connector: TelegramBotConnector) -> None:
        """Non-transient exception in _process_update records an error event."""
        update = _make_update(update_id=30001)

        mock_pass_decision = MagicMock()
        mock_pass_decision.allowed = True

        mock_pass_gp = MagicMock()
        mock_pass_gp.action = "pass_through"

        with (
            patch.object(
                connector._ingestion_policy,
                "evaluate",
                return_value=mock_pass_decision,
            ),
            patch.object(
                connector._global_ingestion_policy,
                "evaluate",
                return_value=mock_pass_gp,
            ),
            patch.object(
                connector,
                "_submit_to_ingest",
                new=AsyncMock(side_effect=RuntimeError("submission failed")),
            ),
        ):
            # Must not raise (non-transient errors are swallowed)
            await connector._process_update(update)

        assert len(connector._filtered_event_buffer) == 1
        row = connector._filtered_event_buffer._rows[0]
        assert row[3] == "30001"
        assert row[7] == FilteredEventBuffer.reason_submission_error()
        assert row[8] == "error"
        assert "submission failed" in (row[10] or "")

    async def test_error_event_has_error_detail(self, connector: TelegramBotConnector) -> None:
        """Error events carry the exception message in error_detail."""
        update = _make_update(update_id=30002)

        mock_pass = MagicMock()
        mock_pass.allowed = True
        mock_pass_gp = MagicMock()
        mock_pass_gp.action = "pass_through"

        with (
            patch.object(connector._ingestion_policy, "evaluate", return_value=mock_pass),
            patch.object(connector._global_ingestion_policy, "evaluate", return_value=mock_pass_gp),
            patch.object(
                connector,
                "_submit_to_ingest",
                new=AsyncMock(side_effect=ValueError("bad payload")),
            ),
        ):
            await connector._process_update(update)

        row = connector._filtered_event_buffer._rows[0]
        assert row[10] == "bad payload"


# ---------------------------------------------------------------------------
# No recording on successful ingest
# ---------------------------------------------------------------------------


class TestNoRecordingOnSuccess:
    async def test_successful_ingest_does_not_record_filtered_event(
        self, connector: TelegramBotConnector
    ) -> None:
        """Updates that pass all filters and submit successfully must NOT be buffered."""
        update = _make_update(update_id=40001)

        mock_pass = MagicMock()
        mock_pass.allowed = True
        mock_pass_gp = MagicMock()
        mock_pass_gp.action = "pass_through"

        with (
            patch.object(connector._ingestion_policy, "evaluate", return_value=mock_pass),
            patch.object(connector._global_ingestion_policy, "evaluate", return_value=mock_pass_gp),
            patch.object(connector, "_submit_to_ingest", new=AsyncMock(return_value=None)),
        ):
            await connector._process_update(update)

        assert len(connector._filtered_event_buffer) == 0


# ---------------------------------------------------------------------------
# Full payload shape
# ---------------------------------------------------------------------------


class TestFullPayloadShape:
    async def test_filter_point_full_payload_shape(self, connector: TelegramBotConnector) -> None:
        """full_payload in buffer row has expected ingest.v1 shape (no schema_version)."""
        update = _make_update(update_id=50001)

        mock_block = MagicMock()
        mock_block.allowed = False
        mock_block.reason = "block"
        mock_block.matched_rule_type = "chat_id"

        with patch.object(connector._ingestion_policy, "evaluate", return_value=mock_block):
            await connector._process_update(update)

        row = connector._filtered_event_buffer._rows[0]
        payload = json.loads(row[9])  # full_payload column
        assert "schema_version" not in payload
        assert "source" in payload
        assert "event" in payload
        assert "sender" in payload
        assert "payload" in payload
        assert "control" in payload
        assert payload["source"]["provider"] == "telegram"
        assert payload["event"]["external_event_id"] == "50001"


# ---------------------------------------------------------------------------
# Flush after poll cycle
# ---------------------------------------------------------------------------


class TestFlushAfterPollCycle:
    async def test_flush_and_drain_calls_buffer_flush(
        self, connector: TelegramBotConnector, mock_db_pool: MagicMock
    ) -> None:
        """_flush_and_drain flushes the buffer when db_pool is available."""
        # Record a filtered event to give flush something to do
        connector._filtered_event_buffer.record(
            external_message_id="flush-test",
            source_channel="telegram",
            sender_identity="111222333",
            subject_or_preview="Hello",
            filter_reason=FilteredEventBuffer.reason_policy_rule(
                "connector_rule", "block", "chat_id"
            ),
            full_payload=FilteredEventBuffer.full_payload(
                channel="telegram",
                provider="telegram",
                endpoint_identity="test_bot",
                external_event_id="flush-test",
                external_thread_id="987654321",
                observed_at="2026-03-11T10:00:00Z",
                sender_identity="111222333",
                raw={},
            ),
        )
        assert len(connector._filtered_event_buffer) == 1

        # Mock fetch to return no replay-pending rows
        conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[])

        await connector._flush_and_drain()

        # Buffer should be cleared after successful flush
        assert len(connector._filtered_event_buffer) == 0
        # executemany should have been called once for the flush
        conn.executemany.assert_awaited_once()

    async def test_flush_and_drain_noop_without_db_pool(
        self, bot_config: TelegramBotConnectorConfig
    ) -> None:
        """_flush_and_drain is a no-op when db_pool is None."""
        connector = TelegramBotConnector(bot_config, db_pool=None, cursor_pool=MagicMock())
        connector._filtered_event_buffer.record(
            external_message_id="noop-test",
            source_channel="telegram",
            sender_identity="111",
            subject_or_preview=None,
            filter_reason="connector_rule:block:chat_id",
            full_payload=FilteredEventBuffer.full_payload(
                channel="telegram",
                provider="telegram",
                endpoint_identity="test_bot",
                external_event_id="noop-test",
                external_thread_id=None,
                observed_at="2026-03-11T10:00:00Z",
                sender_identity="111",
                raw={},
            ),
        )
        # Should not raise, buffer stays unchanged
        await connector._flush_and_drain()
        # Buffer not flushed (no pool)
        assert len(connector._filtered_event_buffer) == 1


# ---------------------------------------------------------------------------
# Replay drain
# ---------------------------------------------------------------------------


class TestReplayDrain:
    def _make_row(
        self,
        *,
        row_id: str = "aaaaaaaa-0000-0000-0000-000000000001",
        received_at: str = "2026-03-11T10:00:00+00:00",
        external_message_id: str = "replay-msg-001",
        payload: dict | None = None,
    ) -> MagicMock:
        """Build a mock asyncpg row for connectors.filtered_events."""
        if payload is None:
            payload = FilteredEventBuffer.full_payload(
                channel="telegram",
                provider="telegram",
                endpoint_identity="test_bot",
                external_event_id=external_message_id,
                external_thread_id="987654321",
                observed_at="2026-03-11T10:00:00Z",
                sender_identity="111222333",
                raw={"update_id": external_message_id},
            )
        row = MagicMock()
        row.__getitem__ = lambda self, key: {
            "id": row_id,
            "received_at": received_at,
            "external_message_id": external_message_id,
            "full_payload": json.dumps(payload),
        }[key]
        return row

    async def test_replay_drain_calls_submit_for_pending_row(
        self, connector: TelegramBotConnector, mock_db_pool: MagicMock
    ) -> None:
        """Replay drain calls _submit_to_ingest for each replay_pending row."""
        row = self._make_row()
        conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[row])

        with patch.object(
            connector,
            "_submit_to_ingest",
            new=AsyncMock(return_value=None),
        ) as mock_submit:
            await connector._drain_replay_pending()

        mock_submit.assert_awaited_once()
        envelope_arg = mock_submit.call_args[0][0]
        assert envelope_arg["schema_version"] == "ingest.v1"
        assert envelope_arg["event"]["external_event_id"] == "replay-msg-001"

    async def test_replay_drain_marks_success_as_replay_complete(
        self, connector: TelegramBotConnector, mock_db_pool: MagicMock
    ) -> None:
        """Successful replay updates status to replay_complete."""
        row = self._make_row(row_id="row-success-id")
        conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[row])

        with patch.object(
            connector,
            "_submit_to_ingest",
            new=AsyncMock(return_value=None),
        ):
            await connector._drain_replay_pending()

        execute_calls = conn.execute.call_args_list
        assert len(execute_calls) >= 1
        last_call = execute_calls[-1]
        status_arg = last_call[0][1]
        assert status_arg == "replay_complete"

    async def test_replay_drain_marks_failure_as_replay_failed(
        self, connector: TelegramBotConnector, mock_db_pool: MagicMock
    ) -> None:
        """Failed replay submission updates status to replay_failed with error_detail."""
        row = self._make_row(row_id="row-fail-id", external_message_id="replay-fail-001")
        conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[row])

        with patch.object(
            connector,
            "_submit_to_ingest",
            new=AsyncMock(side_effect=RuntimeError("switchboard down")),
        ):
            await connector._drain_replay_pending()

        execute_calls = conn.execute.call_args_list
        assert len(execute_calls) >= 1
        last_call = execute_calls[-1]
        status_arg = last_call[0][1]
        error_detail_arg = last_call[0][2]
        assert status_arg == "replay_failed"
        assert "switchboard down" in error_detail_arg

    async def test_replay_drain_noop_when_no_pending_rows(
        self, connector: TelegramBotConnector, mock_db_pool: MagicMock
    ) -> None:
        """Replay drain does nothing when there are no replay_pending rows."""
        conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[])

        with patch.object(
            connector,
            "_submit_to_ingest",
            new=AsyncMock(return_value=None),
        ) as mock_submit:
            await connector._drain_replay_pending()

        mock_submit.assert_not_awaited()

    async def test_replay_drain_noop_without_db_pool(
        self, bot_config: TelegramBotConnectorConfig
    ) -> None:
        """Replay drain is a no-op when db_pool is None."""
        connector = TelegramBotConnector(bot_config, db_pool=None, cursor_pool=MagicMock())
        # Should not raise
        await connector._drain_replay_pending()

    async def test_replay_drain_uses_for_update_skip_locked(
        self, connector: TelegramBotConnector, mock_db_pool: MagicMock
    ) -> None:
        """Drain query must include FOR UPDATE SKIP LOCKED."""
        conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[])

        await connector._drain_replay_pending()

        conn.fetch.assert_awaited_once()
        sql_arg = conn.fetch.call_args[0][0]
        assert "FOR UPDATE SKIP LOCKED" in sql_arg

    async def test_replay_drain_filters_by_connector_identity(
        self, connector: TelegramBotConnector, mock_db_pool: MagicMock
    ) -> None:
        """Drain query passes connector_type and endpoint_identity as parameters."""
        conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[])

        await connector._drain_replay_pending()

        call_args = conn.fetch.call_args[0]
        # $1 = connector_type, $2 = endpoint_identity
        assert call_args[1] == "telegram"
        assert call_args[2] == "test_bot"

    async def test_replay_drain_handles_dict_payload(
        self, connector: TelegramBotConnector, mock_db_pool: MagicMock
    ) -> None:
        """Drain handles full_payload when asyncpg returns a dict (native JSONB codec)."""
        payload_dict = FilteredEventBuffer.full_payload(
            channel="telegram",
            provider="telegram",
            endpoint_identity="test_bot",
            external_event_id="dict-payload-001",
            external_thread_id=None,
            observed_at="2026-03-11T10:00:00Z",
            sender_identity="111222333",
            raw={},
        )
        row = MagicMock()
        row.__getitem__ = lambda self, key: {
            "id": "row-dict-id",
            "received_at": "2026-03-11T10:00:00+00:00",
            "external_message_id": "dict-payload-001",
            "full_payload": payload_dict,  # dict, not str
        }[key]

        conn = mock_db_pool.acquire.return_value.__aenter__.return_value
        conn.fetch = AsyncMock(return_value=[row])

        with patch.object(
            connector,
            "_submit_to_ingest",
            new=AsyncMock(return_value=None),
        ) as mock_submit:
            await connector._drain_replay_pending()

        mock_submit.assert_awaited_once()
        envelope = mock_submit.call_args[0][0]
        assert envelope["schema_version"] == "ingest.v1"
        assert envelope["event"]["external_event_id"] == "dict-payload-001"
