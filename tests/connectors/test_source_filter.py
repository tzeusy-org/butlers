"""Unit tests for src/butlers/connectors/source_filter.py.

Covers:
- Domain blacklist blocks / passes
- Sender_address whitelist blocks / passes
- Substring matching
- Mixed mode (blacklist + whitelist)
- Unknown source_key_type is skipped (one-time WARNING)
- TTL refresh schedules a background task without blocking evaluate()
- Fail-open on DB error: previous cache retained, WARNING logged
- GmailConnector integration: evaluator instantiated, ensure_loaded called,
  filter gate sits between label filter and submission
- extract_gmail_filter_key helper

Issue: bu-qbq.4
"""

from __future__ import annotations

import asyncio
import logging
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from butlers.connectors.source_filter import (
    FilterResult,
    SourceFilterEvaluator,
    SourceFilterSpec,
    _matches_pattern,
    extract_gmail_filter_key,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _spec(
    *,
    id: str = "aaaaaaaa-0000-0000-0000-000000000001",
    name: str = "test-filter",
    filter_mode: str = "blacklist",
    source_key_type: str = "domain",
    patterns: list[str] | None = None,
    priority: int = 0,
) -> SourceFilterSpec:
    return SourceFilterSpec(
        id=id,
        name=name,
        filter_mode=filter_mode,
        source_key_type=source_key_type,
        patterns=patterns or [],
        priority=priority,
    )


def _evaluator_with_filters(
    filters: list[SourceFilterSpec],
    endpoint_identity: str = "gmail:user:test@example.com",
) -> SourceFilterEvaluator:
    """Create an evaluator with a pre-loaded filter cache (no DB needed)."""
    ev = SourceFilterEvaluator(
        connector_type="gmail",
        endpoint_identity=endpoint_identity,
        db_pool=None,
        refresh_interval_s=300,
    )
    ev._filters = filters
    ev._last_loaded_at = time.monotonic()
    return ev


# ---------------------------------------------------------------------------
# _matches_pattern unit tests
# ---------------------------------------------------------------------------


class TestMatchesPattern:
    def test_domain_exact(self) -> None:
        assert _matches_pattern("example.com", "example.com", "domain") is True

    def test_domain_suffix(self) -> None:
        assert _matches_pattern("sub.example.com", "example.com", "domain") is True

    def test_domain_no_match(self) -> None:
        assert _matches_pattern("other.com", "example.com", "domain") is False

    def test_domain_does_not_match_partial_prefix(self) -> None:
        # 'notexample.com' must NOT match 'example.com'
        assert _matches_pattern("notexample.com", "example.com", "domain") is False

    def test_sender_address_exact(self) -> None:
        assert _matches_pattern("alice@example.com", "alice@example.com", "sender_address") is True

    def test_sender_address_case_insensitive(self) -> None:
        assert _matches_pattern("Alice@Example.COM", "alice@example.com", "sender_address") is True

    def test_sender_address_no_match(self) -> None:
        assert _matches_pattern("bob@example.com", "alice@example.com", "sender_address") is False

    def test_substring_present(self) -> None:
        assert _matches_pattern("some SPAM text", "spam", "substring") is True

    def test_substring_absent(self) -> None:
        assert _matches_pattern("hello world", "spam", "substring") is False

    def test_chat_id_match(self) -> None:
        assert _matches_pattern("12345", "12345", "chat_id") is True

    def test_chat_id_no_match(self) -> None:
        assert _matches_pattern("12345", "99999", "chat_id") is False

    def test_unknown_key_type_returns_false(self) -> None:
        assert _matches_pattern("anything", "pattern", "bogus_type") is False


# ---------------------------------------------------------------------------
# extract_gmail_filter_key
# ---------------------------------------------------------------------------


class TestExtractGmailFilterKey:
    def test_domain_extracts_domain(self) -> None:
        assert extract_gmail_filter_key("Alice <alice@example.com>", "domain") == "example.com"

    def test_sender_address_normalizes(self) -> None:
        assert (
            extract_gmail_filter_key("Alice <Alice@Example.COM>", "sender_address")
            == "alice@example.com"
        )

    def test_substring_returns_raw(self) -> None:
        raw = "Alice <alice@example.com>"
        assert extract_gmail_filter_key(raw, "substring") == raw

    def test_domain_plain_address(self) -> None:
        assert extract_gmail_filter_key("alice@example.com", "domain") == "example.com"

    def test_domain_no_at_sign(self) -> None:
        # Graceful degradation: no '@' → empty string
        assert extract_gmail_filter_key("no-at-sign", "domain") == ""


# ---------------------------------------------------------------------------
# SourceFilterEvaluator composition rules
# ---------------------------------------------------------------------------


class TestEvaluatorNoFilters:
    def test_no_filters_allows(self) -> None:
        ev = _evaluator_with_filters([])
        result = ev.evaluate("anything@example.com")
        assert result.allowed is True
        assert result.reason == "no_filters"
        assert result.filter_name is None


class TestDomainBlacklist:
    def test_blacklist_blocks_exact_domain(self) -> None:
        ev = _evaluator_with_filters(
            [_spec(filter_mode="blacklist", source_key_type="domain", patterns=["spam.com"])]
        )
        result = ev.evaluate("Spammer <spammer@spam.com>")
        assert result.allowed is False
        assert "blacklist_match" in result.reason
        assert result.filter_name == "test-filter"

    def test_blacklist_blocks_subdomain(self) -> None:
        ev = _evaluator_with_filters(
            [_spec(filter_mode="blacklist", source_key_type="domain", patterns=["spam.com"])]
        )
        result = ev.evaluate("sender@mail.spam.com")
        assert result.allowed is False

    def test_blacklist_passes_non_matching_domain(self) -> None:
        ev = _evaluator_with_filters(
            [_spec(filter_mode="blacklist", source_key_type="domain", patterns=["spam.com"])]
        )
        result = ev.evaluate("sender@legit.com")
        assert result.allowed is True
        assert result.reason == "passed"

    def test_domain_filter_with_display_name_from_header(self) -> None:
        """Domain filter correctly extracts domain from 'Display Name <addr>' headers.

        Regression test for bu-7w4: evaluate() now accepts a raw From header and
        extracts the key per-filter type, so domain blacklists work correctly even
        when the header includes a display name.
        """
        ev = _evaluator_with_filters(
            [_spec(filter_mode="blacklist", source_key_type="domain", patterns=["spam.com"])]
        )
        result = ev.evaluate("Spammer Name <spammer@spam.com>")
        assert result.allowed is False
        assert "blacklist_match" in result.reason


class TestSenderAddressWhitelist:
    def test_whitelist_allows_listed_address(self) -> None:
        ev = _evaluator_with_filters(
            [
                _spec(
                    filter_mode="whitelist",
                    source_key_type="sender_address",
                    patterns=["alice@example.com"],
                )
            ]
        )
        result = ev.evaluate("alice@example.com")
        assert result.allowed is True
        assert result.reason == "passed"

    def test_whitelist_blocks_unlisted_address(self) -> None:
        ev = _evaluator_with_filters(
            [
                _spec(
                    filter_mode="whitelist",
                    source_key_type="sender_address",
                    patterns=["alice@example.com"],
                )
            ]
        )
        result = ev.evaluate("mallory@evil.com")
        assert result.allowed is False
        assert result.reason == "whitelist_no_match"
        assert result.filter_name is None


class TestSubstringFilter:
    def test_substring_blacklist_blocks(self) -> None:
        ev = _evaluator_with_filters(
            [_spec(filter_mode="blacklist", source_key_type="substring", patterns=["SPAM"])]
        )
        result = ev.evaluate("this is a spam message header")
        assert result.allowed is False

    def test_substring_blacklist_passes(self) -> None:
        ev = _evaluator_with_filters(
            [_spec(filter_mode="blacklist", source_key_type="substring", patterns=["SPAM"])]
        )
        result = ev.evaluate("completely clean header")
        assert result.allowed is True


class TestMixedMode:
    """Blacklist wins over whitelist; mixed-mode evaluation order."""

    def test_blacklist_wins_even_when_whitelist_matches(self) -> None:
        """A message matching a blacklist AND a whitelist should be BLOCKED."""
        ev = _evaluator_with_filters(
            [
                _spec(
                    id="id-bl",
                    name="block-filter",
                    filter_mode="blacklist",
                    source_key_type="domain",
                    patterns=["bad.com"],
                    priority=0,
                ),
                _spec(
                    id="id-wl",
                    name="allow-filter",
                    filter_mode="whitelist",
                    source_key_type="domain",
                    patterns=["bad.com"],
                    priority=1,
                ),
            ]
        )
        result = ev.evaluate("sender@bad.com")
        assert result.allowed is False
        assert "blacklist_match" in result.reason

    def test_whitelist_only_blocks_non_match(self) -> None:
        """With only a whitelist and no blacklist, non-listed key is blocked."""
        ev = _evaluator_with_filters(
            [
                _spec(
                    filter_mode="whitelist",
                    source_key_type="domain",
                    patterns=["good.com"],
                )
            ]
        )
        result = ev.evaluate("sender@other.com")
        assert result.allowed is False
        assert result.reason == "whitelist_no_match"

    def test_blacklist_no_match_whitelist_match_allows(self) -> None:
        """Blacklist misses, whitelist matches → allow."""
        ev = _evaluator_with_filters(
            [
                _spec(
                    id="id-bl",
                    name="block-bad",
                    filter_mode="blacklist",
                    source_key_type="domain",
                    patterns=["bad.com"],
                    priority=0,
                ),
                _spec(
                    id="id-wl",
                    name="allow-good",
                    filter_mode="whitelist",
                    source_key_type="domain",
                    patterns=["good.com"],
                    priority=1,
                ),
            ]
        )
        result = ev.evaluate("sender@good.com")
        assert result.allowed is True
        assert result.reason == "passed"

    def test_blacklist_no_match_whitelist_no_match_blocks(self) -> None:
        """Blacklist misses, whitelist active but no match → block."""
        ev = _evaluator_with_filters(
            [
                _spec(
                    id="id-bl",
                    name="block-bad",
                    filter_mode="blacklist",
                    source_key_type="domain",
                    patterns=["bad.com"],
                    priority=0,
                ),
                _spec(
                    id="id-wl",
                    name="allow-good",
                    filter_mode="whitelist",
                    source_key_type="domain",
                    patterns=["good.com"],
                    priority=1,
                ),
            ]
        )
        result = ev.evaluate("sender@random.com")
        assert result.allowed is False
        assert result.reason == "whitelist_no_match"


class TestUnknownKeyType:
    def test_unknown_key_type_skipped_with_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        filter_id = "ffffffff-dead-beef-0000-000000000001"
        ev = _evaluator_with_filters(
            [
                _spec(
                    id=filter_id,
                    name="unknown-type-filter",
                    filter_mode="blacklist",
                    source_key_type="fax_number",  # unknown
                    patterns=["12345"],
                )
            ]
        )
        with caplog.at_level(logging.WARNING, logger="butlers.connectors.source_filter"):
            result = ev.evaluate("12345")

        # Filter is skipped → no match → allow (only this filter exists)
        assert result.allowed is True
        # Warning was emitted
        assert any("unknown source_key_type" in r.message for r in caplog.records)

    def test_unknown_key_type_one_time_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        """Second call for the same filter_id does NOT emit another warning.

        Each SourceFilterEvaluator instance tracks warned filter_ids independently;
        a fresh evaluator starts with an empty warned set.
        """
        filter_id = "ffffffff-dead-beef-0000-000000000002"
        ev = _evaluator_with_filters(
            [
                _spec(
                    id=filter_id,
                    name="unknown-type-filter-2",
                    filter_mode="blacklist",
                    source_key_type="telex",
                    patterns=["abc"],
                )
            ]
        )
        with caplog.at_level(logging.WARNING, logger="butlers.connectors.source_filter"):
            ev.evaluate("abc")
            warning_count_first = sum(
                1 for r in caplog.records if "unknown source_key_type" in r.message
            )
            ev.evaluate("abc")
            warning_count_second = sum(
                1 for r in caplog.records if "unknown source_key_type" in r.message
            )

        assert warning_count_first == 1
        assert warning_count_second == 1  # no new warning on second call

    def test_unknown_key_type_warning_isolated_per_evaluator(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Two different evaluator instances track warned filter_ids independently."""
        filter_id = "ffffffff-dead-beef-0000-000000000003"
        spec = _spec(
            id=filter_id,
            name="isolated-unknown-filter",
            filter_mode="blacklist",
            source_key_type="carrier_pigeon",  # unknown
            patterns=["coo"],
        )
        ev1 = _evaluator_with_filters([spec])
        ev2 = _evaluator_with_filters([spec])

        with caplog.at_level(logging.WARNING, logger="butlers.connectors.source_filter"):
            ev1.evaluate("coo")
            # ev1 has now warned; ev2 starts fresh and should also warn
            ev2.evaluate("coo")
            warning_count = sum(1 for r in caplog.records if "unknown source_key_type" in r.message)

        # Both evaluators emit a warning (each has its own fresh warned set)
        assert warning_count == 2


# ---------------------------------------------------------------------------
# TTL refresh
# ---------------------------------------------------------------------------


class TestTTLRefresh:
    async def test_refresh_scheduled_when_stale(self) -> None:
        """evaluate() schedules a background task when TTL has elapsed."""
        ev = SourceFilterEvaluator(
            connector_type="gmail",
            endpoint_identity="test@example.com",
            db_pool=None,
            refresh_interval_s=0,  # immediately stale
        )
        ev._filters = []
        ev._last_loaded_at = time.monotonic() - 1  # already past TTL

        with patch.object(ev, "_load_filters", new_callable=AsyncMock) as mock_load:
            ev.evaluate("anything")
            # Allow the event loop to schedule the task
            await asyncio.sleep(0)

        # Background task was created (may still be running)
        assert mock_load.called or (ev._background_refresh_task is not None), (
            "expected background refresh task to be scheduled"
        )

    async def test_refresh_does_not_stack_up(self) -> None:
        """A second evaluate() does not create a second task if one is pending."""
        ev = SourceFilterEvaluator(
            connector_type="gmail",
            endpoint_identity="test@example.com",
            db_pool=None,
            refresh_interval_s=0,
        )
        ev._filters = []
        ev._last_loaded_at = time.monotonic() - 1

        blocker_started = asyncio.Event()
        blocker_gate = asyncio.Event()

        async def _blocking_load() -> None:
            ev._last_loaded_at = time.monotonic()
            blocker_started.set()
            await blocker_gate.wait()

        with patch.object(ev, "_load_filters", side_effect=_blocking_load):
            ev.evaluate("key1")
            await blocker_started.wait()

            # Now the background task is running but not done.
            # A second evaluate() should NOT create another task.
            ev.evaluate("key2")
            task_before = ev._background_refresh_task
            ev.evaluate("key3")
            task_after = ev._background_refresh_task

        blocker_gate.set()
        assert task_before is task_after, "should not create a new task while one is pending"

    async def test_evaluate_does_not_block_on_refresh(self) -> None:
        """evaluate() returns immediately even if background load is in progress."""
        ev = SourceFilterEvaluator(
            connector_type="gmail",
            endpoint_identity="test@example.com",
            db_pool=None,
            refresh_interval_s=0,
        )
        ev._filters = []
        ev._last_loaded_at = time.monotonic() - 1

        gate = asyncio.Event()

        async def _slow_load() -> None:
            ev._last_loaded_at = time.monotonic()
            await gate.wait()

        with patch.object(ev, "_load_filters", side_effect=_slow_load):
            start = time.monotonic()
            result = ev.evaluate("anything")
            elapsed = time.monotonic() - start

        gate.set()
        # evaluate() must return well under 1 s (it's synchronous except for task scheduling)
        assert elapsed < 0.1
        # Result should still be from existing cache
        assert isinstance(result, FilterResult)


# ---------------------------------------------------------------------------
# Fail-open on DB error
# ---------------------------------------------------------------------------


class TestFailOpen:
    async def test_db_error_retains_previous_cache(self, caplog: pytest.LogCaptureFixture) -> None:
        """On DB error, _load_filters retains the previous cache and logs WARNING."""
        mock_pool = AsyncMock()
        mock_pool.fetch.side_effect = RuntimeError("connection refused")

        ev = SourceFilterEvaluator(
            connector_type="gmail",
            endpoint_identity="test@example.com",
            db_pool=mock_pool,
            refresh_interval_s=300,
        )
        # Seed a pre-existing filter cache
        existing_filter = _spec(
            filter_mode="blacklist",
            source_key_type="domain",
            patterns=["cached.com"],
        )
        ev._filters = [existing_filter]
        ev._last_loaded_at = time.monotonic()

        with caplog.at_level(logging.WARNING, logger="butlers.connectors.source_filter"):
            await ev._load_filters()

        # Cache is retained
        assert ev._filters == [existing_filter]
        # WARNING was logged
        assert any("failed to load filters" in r.message for r in caplog.records)

    async def test_db_error_evaluate_still_uses_cache(self) -> None:
        """evaluate() works correctly from stale cache after DB error."""
        mock_pool = AsyncMock()
        mock_pool.fetch.side_effect = RuntimeError("timeout")

        ev = SourceFilterEvaluator(
            connector_type="gmail",
            endpoint_identity="test@example.com",
            db_pool=mock_pool,
            refresh_interval_s=300,
        )
        existing_filter = _spec(
            filter_mode="blacklist",
            source_key_type="domain",
            patterns=["blocked.com"],
        )
        ev._filters = [existing_filter]
        ev._last_loaded_at = time.monotonic()

        # DB error during reload — should not affect current evaluation
        await ev._load_filters()

        result = ev.evaluate("sender@blocked.com")
        assert result.allowed is False

    async def test_ensure_loaded_with_pool(self) -> None:
        """ensure_loaded() calls _load_filters once."""
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        ev = SourceFilterEvaluator(
            connector_type="gmail",
            endpoint_identity="test@example.com",
            db_pool=mock_pool,
            refresh_interval_s=300,
        )
        await ev.ensure_loaded()
        assert ev._last_loaded_at is not None
        mock_pool.fetch.assert_awaited_once()

    async def test_ensure_loaded_idempotent(self) -> None:
        """ensure_loaded() does not reload if already loaded."""
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        ev = SourceFilterEvaluator(
            connector_type="gmail",
            endpoint_identity="test@example.com",
            db_pool=mock_pool,
            refresh_interval_s=300,
        )
        await ev.ensure_loaded()
        await ev.ensure_loaded()
        # fetch should only be called once (second ensure_loaded is a no-op)
        assert mock_pool.fetch.await_count == 1

    async def test_ensure_loaded_no_pool(self) -> None:
        """ensure_loaded() with no DB pool sets last_loaded_at and uses empty filters."""
        ev = SourceFilterEvaluator(
            connector_type="gmail",
            endpoint_identity="test@example.com",
            db_pool=None,
        )
        await ev.ensure_loaded()
        assert ev._last_loaded_at is not None
        assert ev._filters == []


# ---------------------------------------------------------------------------
# GmailConnector integration smoke tests
# ---------------------------------------------------------------------------


class TestGmailConnectorIntegration:
    """Smoke tests for the GmailConnector source filter integration."""

    @pytest.fixture()
    def gmail_config(self, tmp_path: Path):
        from butlers.connectors.gmail import GmailConnectorConfig

        return GmailConnectorConfig(
            switchboard_mcp_url="http://localhost:40100/sse",
            connector_provider="gmail",
            connector_channel="email",
            connector_endpoint_identity="gmail:user:test@example.com",
            connector_cursor_path=tmp_path / "cursor.json",
            connector_max_inflight=4,
            gmail_client_id="test-client-id",
            gmail_client_secret="test-client-secret",
            gmail_refresh_token="test-refresh-token",
        )

    def test_evaluator_instantiated(self, gmail_config) -> None:
        """GmailConnectorRuntime creates a SourceFilterEvaluator on __init__."""
        from butlers.connectors.gmail import GmailConnectorRuntime

        runtime = GmailConnectorRuntime(gmail_config)
        assert hasattr(runtime, "_source_filter_evaluator")
        assert isinstance(runtime._source_filter_evaluator, SourceFilterEvaluator)

    def test_evaluator_uses_connector_identity(self, gmail_config) -> None:
        """Evaluator endpoint_identity matches connector config."""
        from butlers.connectors.gmail import GmailConnectorRuntime

        runtime = GmailConnectorRuntime(gmail_config)
        assert runtime._source_filter_evaluator._endpoint_identity == "gmail:user:test@example.com"

    async def test_ensure_loaded_called_before_ingestion(self, gmail_config) -> None:
        """start() calls ensure_loaded() before the ingestion loop begins."""
        from butlers.connectors.gmail import GmailConnectorRuntime

        runtime = GmailConnectorRuntime(gmail_config)

        ensure_loaded_called = []

        async def _mock_ensure_loaded() -> None:
            ensure_loaded_called.append(True)

        async def _mock_run_ingestion_loop() -> None:
            # Ensure ensure_loaded was called before this
            assert ensure_loaded_called, "ensure_loaded must be called before ingestion loop"
            raise asyncio.CancelledError()

        with (
            patch.object(
                runtime._source_filter_evaluator,
                "ensure_loaded",
                side_effect=_mock_ensure_loaded,
            ),
            patch.object(runtime, "_start_health_server"),
            patch.object(runtime, "_start_heartbeat"),
            patch.object(runtime, "_ensure_cursor_file", new_callable=AsyncMock),
            patch(
                "butlers.connectors.gmail.wait_for_switchboard_ready",
                new_callable=AsyncMock,
            ),
            patch.object(
                runtime,
                "_run_ingestion_loop",
                side_effect=_mock_run_ingestion_loop,
            ),
        ):
            try:
                await runtime.start()
            except (asyncio.CancelledError, Exception):
                pass

        assert ensure_loaded_called, "ensure_loaded() was not called"

    async def test_source_filter_blocks_message_before_submission(self, gmail_config) -> None:
        """When source filter blocks, message is not submitted to Switchboard."""
        from butlers.connectors.gmail import GmailConnectorRuntime

        runtime = GmailConnectorRuntime(gmail_config)
        runtime._http_client = AsyncMock()

        # Seed blocked filter
        blocked_filter = _spec(
            filter_mode="blacklist",
            source_key_type="sender_address",
            patterns=["blocked@spam.com"],
        )
        runtime._source_filter_evaluator._filters = [blocked_filter]
        runtime._source_filter_evaluator._last_loaded_at = time.monotonic()

        # Message data with a blocked sender
        message_data = {
            "id": "msg001",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "blocked@spam.com"},
                    {"name": "To", "value": "user@example.com"},
                    {"name": "Subject", "value": "Test"},
                ]
            },
        }

        submit_called = []

        async def _mock_fetch_message(msg_id: str) -> dict:
            return message_data

        async def _mock_submit(envelope: dict) -> None:
            submit_called.append(envelope)

        with (
            patch.object(runtime, "_fetch_message", side_effect=_mock_fetch_message),
            patch.object(runtime, "_submit_to_ingest_api", side_effect=_mock_submit),
        ):
            await runtime._ingest_single_message("msg001")

        assert not submit_called, "message should not be submitted when source filter blocks"

    async def test_source_filter_allows_message_proceeds_to_submission(self, gmail_config) -> None:
        """When source filter allows, message proceeds to Switchboard submission."""
        from butlers.connectors.gmail import GmailConnectorRuntime

        runtime = GmailConnectorRuntime(gmail_config)
        runtime._http_client = AsyncMock()

        # No filters → allow all
        runtime._source_filter_evaluator._filters = []
        runtime._source_filter_evaluator._last_loaded_at = time.monotonic()

        message_data = {
            "id": "msg002",
            "labelIds": ["INBOX"],
            "payload": {
                "headers": [
                    {"name": "From", "value": "alice@example.com"},
                    {"name": "To", "value": "user@example.com"},
                    {"name": "Subject", "value": "Hello"},
                ],
                "mimeType": "text/plain",
                "body": {"data": ""},
                "parts": [],
            },
        }

        submit_called = []

        async def _mock_fetch_message(msg_id: str) -> dict:
            return message_data

        async def _mock_submit(envelope: dict) -> None:
            submit_called.append(envelope)

        async def _mock_build_envelope(msg_data, *, policy_result) -> dict:
            return {"id": msg_data["id"]}

        with (
            patch.object(runtime, "_fetch_message", side_effect=_mock_fetch_message),
            patch.object(runtime, "_submit_to_ingest_api", side_effect=_mock_submit),
            patch.object(runtime, "_build_ingest_envelope", side_effect=_mock_build_envelope),
        ):
            await runtime._ingest_single_message("msg002")

        assert submit_called, "message should be submitted when source filter allows"
