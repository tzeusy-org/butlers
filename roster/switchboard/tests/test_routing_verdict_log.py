"""Unit tests for the routing_verdict_log write path (bu-aga08, bead 1 of 7).

Covers the pure ``normalize_sender_key`` helper and the best-effort,
never-raising ``record_routing_verdict`` writer in isolation (mocked pool).
See ``tests/integration/test_switchboard_routing_verdict_log_migration.py``
for the migration + real-insert coverage, and
``tests/modules/test_module_pipeline.py::TestMessagePipelineRoutingVerdictLog``
for the pipeline write-hook wiring.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from butlers.tools.switchboard.routing.verdict_log import (
    VALID_VERDICT_ACTIONS,
    VALID_VERDICT_SOURCES,
    normalize_sender_key,
    record_routing_verdict,
)

pytestmark = pytest.mark.unit


class TestNormalizeSenderKey:
    def test_bare_email(self):
        assert normalize_sender_key("user@example.com") == "user@example.com"

    def test_uppercase_email_is_lowercased(self):
        assert normalize_sender_key("User@Example.COM") == "user@example.com"

    def test_display_name_wrapped_email(self):
        assert (
            normalize_sender_key("GitHub Notifications <notifications@github.com>")
            == "notifications@github.com"
        )

    def test_non_email_identity_falls_back_to_lowercased_raw(self):
        # e.g. a telegram chat id or phone-number-shaped identity
        assert normalize_sender_key("Telegram:123456789") == "telegram:123456789"

    def test_none_input(self):
        assert normalize_sender_key(None) == ""

    def test_blank_input(self):
        assert normalize_sender_key("   ") == ""


class TestRecordRoutingVerdict:
    async def test_pool_none_returns_none_without_raising(self):
        result = await record_routing_verdict(
            None,
            ingestion_event_id="00000000-0000-0000-0000-000000000001",
            sender_identity="user@example.com",
            source_channel="email",
            verdict_source="llm",
            verdict_action="route_to",
            verdict_target="finance",
        )
        assert result is None

    async def test_missing_ingestion_event_id_drops_row(self):
        pool = AsyncMock()
        result = await record_routing_verdict(
            pool,
            ingestion_event_id=None,
            sender_identity="user@example.com",
            source_channel="email",
            verdict_source="llm",
            verdict_action="route_to",
        )
        assert result is None
        pool.fetchval.assert_not_awaited()

    async def test_invalid_verdict_source_drops_row(self):
        pool = AsyncMock()
        result = await record_routing_verdict(
            pool,
            ingestion_event_id="00000000-0000-0000-0000-000000000001",
            sender_identity="user@example.com",
            source_channel="email",
            verdict_source="not-a-real-source",  # type: ignore[arg-type]
            verdict_action="route_to",
        )
        assert result is None
        pool.fetchval.assert_not_awaited()

    async def test_invalid_verdict_action_drops_row(self):
        pool = AsyncMock()
        result = await record_routing_verdict(
            pool,
            ingestion_event_id="00000000-0000-0000-0000-000000000001",
            sender_identity="user@example.com",
            source_channel="email",
            verdict_source="llm",
            verdict_action="not-a-real-action",  # type: ignore[arg-type]
        )
        assert result is None
        pool.fetchval.assert_not_awaited()

    async def test_successful_insert_returns_row_id_and_normalizes_sender_key(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(return_value="row-id-123")

        result = await record_routing_verdict(
            pool,
            ingestion_event_id="00000000-0000-0000-0000-000000000001",
            sender_identity="Billing <BILLING@Chase.com>",
            source_channel="email",
            verdict_source="rule",
            verdict_action="route_to",
            verdict_target="finance",
            matched_rule_id="11111111-1111-1111-1111-111111111111",
        )

        assert result == "row-id-123"
        pool.fetchval.assert_awaited_once()
        query, *params = pool.fetchval.await_args.args
        assert "INSERT INTO routing_verdict_log" in query
        assert params[0] == "00000000-0000-0000-0000-000000000001"
        assert params[1] == "billing@chase.com"  # normalized sender_key
        assert params[2] == "email"
        assert params[3] == "rule"
        assert params[4] == "route_to"
        assert params[5] == "finance"
        assert params[6] == "11111111-1111-1111-1111-111111111111"

    async def test_db_error_is_swallowed_and_returns_none(self):
        pool = AsyncMock()
        pool.fetchval = AsyncMock(side_effect=Exception("relation does not exist"))

        result = await record_routing_verdict(
            pool,
            ingestion_event_id="00000000-0000-0000-0000-000000000001",
            sender_identity="user@example.com",
            source_channel="email",
            verdict_source="llm",
            verdict_action="route_to",
            verdict_target="finance",
        )
        assert result is None

    def test_verdict_vocabularies_match_the_migration_check_constraints(self):
        # Keep this in sync with
        # roster/switchboard/migrations/019_switchboard_routing_verdict_log.py's
        # chk_routing_verdict_log_verdict_source / _verdict_action constraints.
        assert VALID_VERDICT_SOURCES == {"llm", "rule", "pinned", "spot_check"}
        assert VALID_VERDICT_ACTIONS == {
            "route_to",
            "skip",
            "metadata_only",
            "pass_through",
            "block",
        }
