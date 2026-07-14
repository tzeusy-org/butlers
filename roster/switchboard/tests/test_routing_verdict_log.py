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

    # bu-jxsew regression pins: channel-scoped ids must pass through the
    # wrapper BYTE-IDENTICAL (lowercased-whole), never through the shared
    # email-only normalizer. These are the real forms in the live verdict log;
    # normalize_email_sender would parseaddr-strip their colon prefixes
    # (owntracks:th -> "th", home_assistant:...:443 -> "443" — a COLLISION),
    # mangling ~75% of keys. If a future PR "simplifies" the wrapper to the bare
    # shared helper, these trip loudly instead of silently corrupting history.
    @pytest.mark.parametrize(
        "channel_id",
        [
            "owntracks:th",
            "telegram:bot:@bigbutlerbot",
            "steam:user:76561198037633688",
            "home_assistant:v-on-shenton.parrot-hen.ts.net:443",
            "spotify:tzeusii",
            "dashboard:web:019e2246-7f41-754e-a991-63fc7adf334b",
        ],
    )
    def test_channel_scoped_ids_pass_through_byte_identical(self, channel_id):
        assert normalize_sender_key(channel_id) == channel_id

    def test_channel_id_case_is_lowercased_but_prefix_preserved(self):
        # A mixed-case channel id lowercases whole; the prefix is NOT stripped.
        assert normalize_sender_key("Home_Assistant:HOST:443") == "home_assistant:host:443"

    def test_email_branch_delegates_to_shared_normalizer(self):
        # bu-jxsew convergence: the email branch reuses the shared canonical
        # email normalizer, so its output matches it exactly (and stays
        # byte-identical to the pre-convergence local lowercase for real forms).
        from butlers.identity import normalize_email_sender

        for raw in ("User@Example.COM", "GitHub <notifications@github.com>"):
            assert normalize_sender_key(raw) == normalize_email_sender(raw)

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
        assert "INSERT INTO switchboard.routing_verdict_log" in query
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
