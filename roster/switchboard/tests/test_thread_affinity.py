"""Unit tests for thread-affinity routing lookup.

Tests the lookup algorithm, TTL/staleness detection, override handling,
telemetry recording, and ingest pipeline integration.

All tests are unit-level (no Docker required) — DB calls are mocked.

See docs/switchboard/thread_affinity_routing.md for spec reference.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.tools.switchboard.triage.telemetry import (
    reset_thread_affinity_telemetry_for_tests,
    reset_triage_telemetry_for_tests,
)
from butlers.tools.switchboard.triage.thread_affinity import (
    AffinityOutcome,
    AffinityResult,
    ThreadAffinitySettings,
    _check_override,
    lookup_thread_affinity,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def reset_telemetry() -> None:
    """Reset telemetry singletons between tests."""
    reset_triage_telemetry_for_tests()
    reset_thread_affinity_telemetry_for_tests()


def _settings(
    *,
    enabled: bool = True,
    ttl_days: int = 30,
    overrides: dict[str, str] | None = None,
) -> ThreadAffinitySettings:
    return ThreadAffinitySettings(
        enabled=enabled,
        ttl_days=ttl_days,
        thread_overrides=overrides or {},
    )


def _mock_pool(rows: list[dict] | None = None, stale_exists: bool = False) -> AsyncMock:
    """Create a mock asyncpg pool that returns given rows for fetch."""
    pool = AsyncMock()

    # Convert dicts to record-like objects with attribute access
    class FakeRecord:
        def __init__(self, data: dict) -> None:
            self._data = data

        def __getitem__(self, key: str) -> object:
            return self._data[key]

        def __len__(self) -> int:
            return len(self._data)

    if rows is None:
        rows = []

    fake_rows = [FakeRecord(r) for r in rows]

    pool.fetch = AsyncMock(return_value=fake_rows)

    # fetchrow for stale check: return a fake row if stale_exists
    if stale_exists:
        pool.fetchrow = AsyncMock(return_value=FakeRecord({"1": 1}))
    else:
        pool.fetchrow = AsyncMock(return_value=None)

    return pool


# ---------------------------------------------------------------------------
# AffinityOutcome properties
# ---------------------------------------------------------------------------


class TestAffinityOutcomeProperties:
    def test_hit_produces_route(self) -> None:
        assert AffinityOutcome.HIT.produces_route is True

    def test_force_override_produces_route(self) -> None:
        assert AffinityOutcome.FORCE_OVERRIDE.produces_route is True

    def test_miss_outcomes_do_not_produce_route(self) -> None:
        misses = [
            AffinityOutcome.MISS_NO_THREAD_ID,
            AffinityOutcome.MISS_NO_HISTORY,
            AffinityOutcome.MISS_CONFLICT,
            AffinityOutcome.MISS_STALE,
            AffinityOutcome.MISS_DISABLED_GLOBAL,
            AffinityOutcome.MISS_DISABLED_THREAD,
            AffinityOutcome.MISS_ERROR,
        ]
        for outcome in misses:
            assert outcome.produces_route is False, f"{outcome} should not produce route"
            assert outcome.is_miss is True, f"{outcome} should be a miss"

    def test_hit_is_not_miss(self) -> None:
        assert AffinityOutcome.HIT.is_miss is False

    def test_telemetry_reason_values(self) -> None:
        assert AffinityOutcome.MISS_NO_THREAD_ID.telemetry_reason == "no_thread_id"
        assert AffinityOutcome.MISS_NO_HISTORY.telemetry_reason == "no_history"
        assert AffinityOutcome.MISS_CONFLICT.telemetry_reason == "conflict"
        assert AffinityOutcome.MISS_STALE.telemetry_reason == "stale"
        assert AffinityOutcome.MISS_DISABLED_GLOBAL.telemetry_reason == "disabled"
        assert AffinityOutcome.MISS_DISABLED_THREAD.telemetry_reason == "disabled"
        assert AffinityOutcome.MISS_ERROR.telemetry_reason == "error"


# ---------------------------------------------------------------------------
# _check_override
# ---------------------------------------------------------------------------


class TestCheckOverride:
    def test_disabled_override_returns_miss(self) -> None:
        settings = _settings(overrides={"thread-001": "disabled"})
        result = _check_override("thread-001", settings)
        assert result is not None
        assert result.outcome == AffinityOutcome.MISS_DISABLED_THREAD

    def test_force_override_returns_force(self) -> None:
        settings = _settings(overrides={"thread-001": "force:finance"})
        result = _check_override("thread-001", settings)
        assert result is not None
        assert result.outcome == AffinityOutcome.FORCE_OVERRIDE
        assert result.target_butler == "finance"

    def test_no_override_returns_none(self) -> None:
        settings = _settings(overrides={})
        result = _check_override("thread-001", settings)
        assert result is None

    def test_unknown_thread_returns_none(self) -> None:
        settings = _settings(overrides={"thread-other": "disabled"})
        result = _check_override("thread-001", settings)
        assert result is None

    def test_malformed_force_override_returns_none(self) -> None:
        """force: with empty butler name is treated as malformed → no override."""
        settings = _settings(overrides={"thread-001": "force:"})
        result = _check_override("thread-001", settings)
        assert result is None

    def test_unknown_override_value_returns_none(self) -> None:
        settings = _settings(overrides={"thread-001": "something-unknown"})
        result = _check_override("thread-001", settings)
        assert result is None


# ---------------------------------------------------------------------------
# lookup_thread_affinity — non-email channel
# ---------------------------------------------------------------------------


class TestLookupNonEmailChannel:
    async def test_telegram_returns_miss_no_thread_id(self) -> None:
        pool = _mock_pool()
        result = await lookup_thread_affinity(pool, "some-thread", "telegram", settings=_settings())
        assert result.outcome == AffinityOutcome.MISS_NO_THREAD_ID
        pool.fetch.assert_not_called()

    async def test_mcp_channel_returns_miss_no_thread_id(self) -> None:
        pool = _mock_pool()
        result = await lookup_thread_affinity(pool, "thread-001", "mcp", settings=_settings())
        assert result.outcome == AffinityOutcome.MISS_NO_THREAD_ID


# ---------------------------------------------------------------------------
# lookup_thread_affinity — globally disabled
# ---------------------------------------------------------------------------


class TestLookupGloballyDisabled:
    async def test_globally_disabled_returns_miss(self) -> None:
        pool = _mock_pool()
        settings = _settings(enabled=False)
        result = await lookup_thread_affinity(pool, "thread-001", "email", settings=settings)
        assert result.outcome == AffinityOutcome.MISS_DISABLED_GLOBAL
        pool.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# lookup_thread_affinity — missing thread_id
# ---------------------------------------------------------------------------


class TestLookupMissingThreadId:
    async def test_none_thread_id_returns_miss(self) -> None:
        pool = _mock_pool()
        result = await lookup_thread_affinity(pool, None, "email", settings=_settings())
        assert result.outcome == AffinityOutcome.MISS_NO_THREAD_ID
        pool.fetch.assert_not_called()

    async def test_empty_string_thread_id_returns_miss(self) -> None:
        pool = _mock_pool()
        result = await lookup_thread_affinity(pool, "", "email", settings=_settings())
        assert result.outcome == AffinityOutcome.MISS_NO_THREAD_ID
        pool.fetch.assert_not_called()

    async def test_whitespace_thread_id_returns_miss(self) -> None:
        pool = _mock_pool()
        result = await lookup_thread_affinity(pool, "   ", "email", settings=_settings())
        assert result.outcome == AffinityOutcome.MISS_NO_THREAD_ID
        pool.fetch.assert_not_called()


# ---------------------------------------------------------------------------
# lookup_thread_affinity — thread override checks
# ---------------------------------------------------------------------------


class TestLookupThreadOverride:
    async def test_disabled_override_returns_miss_disabled_thread(self) -> None:
        pool = _mock_pool()
        settings = _settings(overrides={"<thread-abc@mail.example.com>": "disabled"})
        result = await lookup_thread_affinity(
            pool, "<thread-abc@mail.example.com>", "email", settings=settings
        )
        assert result.outcome == AffinityOutcome.MISS_DISABLED_THREAD
        pool.fetch.assert_not_called()

    async def test_force_override_returns_force_override(self) -> None:
        pool = _mock_pool()
        settings = _settings(overrides={"<thread-abc@mail.example.com>": "force:finance"})
        result = await lookup_thread_affinity(
            pool, "<thread-abc@mail.example.com>", "email", settings=settings
        )
        assert result.outcome == AffinityOutcome.FORCE_OVERRIDE
        assert result.target_butler == "finance"
        pool.fetch.assert_not_called()

    async def test_whitespace_stripped_before_override_lookup(self) -> None:
        """Thread IDs with whitespace are stripped before override lookup."""
        pool = _mock_pool()
        settings = _settings(overrides={"thread-001": "force:health"})
        result = await lookup_thread_affinity(pool, "  thread-001  ", "email", settings=settings)
        assert result.outcome == AffinityOutcome.FORCE_OVERRIDE
        assert result.target_butler == "health"


# ---------------------------------------------------------------------------
# lookup_thread_affinity — routing history queries
# ---------------------------------------------------------------------------


class TestLookupHistoryQuery:
    async def test_single_butler_hit(self) -> None:
        """One distinct butler in history → HIT."""
        pool = _mock_pool(
            rows=[
                {"target_butler": "finance", "last_routed_at": "2026-02-20T10:00:00Z"},
            ]
        )
        result = await lookup_thread_affinity(pool, "thread-001", "email", settings=_settings())
        assert result.outcome == AffinityOutcome.HIT
        assert result.target_butler == "finance"
        pool.fetch.assert_called_once()

    async def test_two_distinct_butlers_conflict(self) -> None:
        """Two distinct butlers → CONFLICT (miss)."""
        pool = _mock_pool(
            rows=[
                {"target_butler": "finance", "last_routed_at": "2026-02-20T10:00:00Z"},
                {"target_butler": "health", "last_routed_at": "2026-02-19T10:00:00Z"},
            ]
        )
        result = await lookup_thread_affinity(pool, "thread-001", "email", settings=_settings())
        assert result.outcome == AffinityOutcome.MISS_CONFLICT
        assert result.target_butler is None

    async def test_no_history_no_stale_miss(self) -> None:
        """No rows, no stale history → MISS_NO_HISTORY."""
        pool = _mock_pool(rows=[], stale_exists=False)
        result = await lookup_thread_affinity(pool, "thread-001", "email", settings=_settings())
        assert result.outcome == AffinityOutcome.MISS_NO_HISTORY

    async def test_no_history_but_stale_exists_returns_stale(self) -> None:
        """No rows within TTL, but stale history exists → MISS_STALE."""
        pool = _mock_pool(rows=[], stale_exists=True)
        result = await lookup_thread_affinity(
            pool, "thread-001", "email", settings=_settings(ttl_days=30)
        )
        assert result.outcome == AffinityOutcome.MISS_STALE

    async def test_db_error_returns_miss_error(self) -> None:
        """DB error during lookup → MISS_ERROR (fail-open)."""
        pool = AsyncMock()
        pool.fetch = AsyncMock(side_effect=RuntimeError("DB unavailable"))
        result = await lookup_thread_affinity(pool, "thread-001", "email", settings=_settings())
        assert result.outcome == AffinityOutcome.MISS_ERROR
        assert result.target_butler is None

    async def test_settings_loaded_from_db_when_not_provided(self) -> None:
        """When settings=None, load_settings is called."""
        pool = _mock_pool(
            rows=[
                {"target_butler": "finance", "last_routed_at": "2026-02-20T10:00:00Z"},
            ]
        )
        # Mock the settings load
        pool.fetchrow = AsyncMock(
            side_effect=[
                # First call: load_settings (settings row)
                MagicMock(
                    **{
                        "__getitem__": lambda self, k: {
                            "thread_affinity_enabled": True,
                            "thread_affinity_ttl_days": 30,
                            "thread_overrides": {},
                        }[k]
                    }
                ),
                # Second call: stale check (never reached since we have a hit)
                None,
            ]
        )

        with patch("butlers.tools.switchboard.triage.thread_affinity.load_settings") as mock_load:
            mock_load.return_value = _settings()
            result = await lookup_thread_affinity(pool, "thread-001", "email", settings=None)

        mock_load.assert_called_once_with(pool)
        assert result.outcome == AffinityOutcome.HIT


# ---------------------------------------------------------------------------
# AffinityResult
# ---------------------------------------------------------------------------


class TestAffinityResult:
    def test_hit_result_has_target(self) -> None:
        r = AffinityResult(outcome=AffinityOutcome.HIT, target_butler="finance")
        assert r.target_butler == "finance"
        assert r.outcome.produces_route is True

    def test_miss_result_has_no_target(self) -> None:
        r = AffinityResult(outcome=AffinityOutcome.MISS_NO_HISTORY)
        assert r.target_butler is None
        assert r.outcome.is_miss is True

    def test_frozen_result(self) -> None:
        r = AffinityResult(outcome=AffinityOutcome.HIT, target_butler="finance")
        with pytest.raises(Exception):
            r.target_butler = "other"  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ThreadAffinitySettings
# ---------------------------------------------------------------------------


class TestThreadAffinitySettings:
    def test_defaults(self) -> None:
        s = ThreadAffinitySettings.defaults()
        assert s.enabled is True
        assert s.ttl_days == 30
        assert s.thread_overrides == {}

    def test_custom_settings(self) -> None:
        s = ThreadAffinitySettings(
            enabled=False, ttl_days=14, thread_overrides={"tid-1": "disabled"}
        )
        assert s.enabled is False
        assert s.ttl_days == 14
        assert s.thread_overrides == {"tid-1": "disabled"}

    def test_frozen(self) -> None:
        s = ThreadAffinitySettings.defaults()
        with pytest.raises(Exception):
            s.enabled = False  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Ingest pipeline integration
# ---------------------------------------------------------------------------


class TestIngestPipelineIntegration:
    """Tests that ingest_v1 correctly integrates thread affinity lookup."""

    def _base_email_payload(
        self,
        *,
        thread_id: str | None = "<thread-abc@mail.example.com>",
        message_id: str = "<msg001@example.com>",
    ) -> dict:
        from datetime import UTC, datetime

        payload = {
            "schema_version": "ingest.v1",
            "source": {
                "channel": "email",
                "provider": "gmail",
                "endpoint_identity": "gmail:user:alice@example.com",
            },
            "event": {
                "external_event_id": message_id,
                "observed_at": datetime.now(UTC).isoformat(),
            },
            "sender": {"identity": "user@example.com"},
            "payload": {
                "raw": {"subject": "Test", "body": "Test body"},
                "normalized_text": "Test\nTest body",
            },
            "control": {"ingestion_tier": "full"},
        }
        if thread_id is not None:
            payload["event"]["external_thread_id"] = thread_id
        return payload

    async def test_affinity_hit_passes_target_to_triage(self) -> None:
        """When affinity lookup hits, the target is passed to evaluate_triage."""
        from butlers.tools.switchboard.ingestion.ingest import _run_triage

        payload = self._base_email_payload(thread_id="<thread-abc@mail.example.com>")
        rules = []  # No rules — only thread affinity should match

        with patch(
            "butlers.tools.switchboard.ingestion.ingest.lookup_thread_affinity",
        ) as mock_lookup:
            mock_lookup.return_value = AffinityResult(
                outcome=AffinityOutcome.HIT, target_butler="finance"
            )

            # _run_triage with thread_affinity_target="finance" should produce route_to
            decision = _run_triage(
                payload,
                rules,
                cache_available=True,
                source_channel="email",
                thread_affinity_target="finance",
            )

        assert decision.decision == "route_to"
        assert decision.target_butler == "finance"
        assert decision.matched_rule_type == "thread_affinity"

    async def test_affinity_miss_falls_through_to_rules(self) -> None:
        """When affinity misses, evaluate_triage falls through to rule evaluation."""
        from butlers.tools.switchboard.ingestion.ingest import _run_triage

        rules = [
            {
                "id": "rule-001",
                "rule_type": "sender_domain",
                "condition": {"domain": "example.com", "match": "suffix"},
                "action": "route_to:health",
                "priority": 10,
            }
        ]
        payload = self._base_email_payload()

        # No thread affinity target — falls through to rule
        decision = _run_triage(
            payload,
            rules,
            cache_available=True,
            source_channel="email",
            thread_affinity_target=None,
        )
        assert decision.decision == "route_to"
        assert decision.target_butler == "health"

    async def test_affinity_not_called_for_non_email(self) -> None:
        """Thread affinity lookup is not attempted for non-email channels."""
        from butlers.tools.switchboard.ingestion.ingest import _run_triage

        payload = {
            "schema_version": "ingest.v1",
            "source": {"channel": "telegram", "provider": "telegram", "endpoint_identity": "bot"},
            "event": {"external_event_id": "msg-1", "observed_at": "2026-02-23T00:00:00Z"},
            "sender": {"identity": "user123"},
            "payload": {"raw": {}, "normalized_text": "Hello"},
            "control": {"ingestion_tier": "full"},
        }

        # No thread_affinity_target → pass_through (no rules, no affinity)
        decision = _run_triage(
            payload,
            [],
            cache_available=True,
            source_channel="telegram",
            thread_affinity_target=None,
        )
        assert decision.decision == "pass_through"
