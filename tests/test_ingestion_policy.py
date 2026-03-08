"""Unit tests for the unified IngestionPolicyEvaluator.

Covers:
- All 7 rule_type matchers (sender_domain, sender_address, header_condition,
  mime_type, substring, chat_id, channel_id)
- First-match-wins evaluation order
- No-match returns pass_through
- Catch-all wildcard conditions (from whitelist migration)
- Fail-open on DB error (retains stale cache)
- TTL-based background refresh scheduling
- Cache invalidation
- IngestionEnvelope and PolicyDecision dataclasses
- Scope-aware loading

Issue: bu-r55.3
"""

from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from butlers.ingestion_policy import (
    IngestionEnvelope,
    IngestionPolicyEvaluator,
    PolicyDecision,
    _match_channel_id,
    _match_chat_id,
    _match_header_condition,
    _match_mime_type,
    _match_sender_address,
    _match_sender_domain,
    _match_substring,
)

pytestmark = pytest.mark.unit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _rule(
    *,
    id: str = "00000000-0000-0000-0000-000000000001",
    rule_type: str = "sender_domain",
    condition: dict | None = None,
    action: str = "pass_through",
    priority: int = 10,
    name: str | None = None,
    created_at: str = "2026-02-01T00:00:00Z",
) -> dict[str, Any]:
    return {
        "id": id,
        "rule_type": rule_type,
        "condition": condition or {},
        "action": action,
        "priority": priority,
        "name": name,
        "created_at": created_at,
    }


def _email_envelope(
    *,
    sender: str = "user@example.com",
    headers: dict[str, str] | None = None,
    mime_parts: list[str] | None = None,
    raw_key: str = "",
) -> IngestionEnvelope:
    return IngestionEnvelope(
        sender_address=sender,
        source_channel="email",
        headers=headers or {},
        mime_parts=mime_parts or [],
        raw_key=raw_key,
    )


def _telegram_envelope(*, chat_id: str = "12345") -> IngestionEnvelope:
    return IngestionEnvelope(
        sender_address="",
        source_channel="telegram",
        raw_key=chat_id,
    )


def _discord_envelope(*, channel_id: str = "987654321098765432") -> IngestionEnvelope:
    return IngestionEnvelope(
        sender_address="",
        source_channel="discord",
        raw_key=channel_id,
    )


# ---------------------------------------------------------------------------
# IngestionEnvelope
# ---------------------------------------------------------------------------


class TestIngestionEnvelope:
    def test_frozen_dataclass(self) -> None:
        env = IngestionEnvelope(sender_address="a@b.com", source_channel="email")
        with pytest.raises(AttributeError):
            env.sender_address = "c@d.com"  # type: ignore[misc]

    def test_defaults(self) -> None:
        env = IngestionEnvelope()
        assert env.sender_address == ""
        assert env.source_channel == ""
        assert env.headers == {}
        assert env.mime_parts == []
        assert env.thread_id is None
        assert env.raw_key == ""


# ---------------------------------------------------------------------------
# PolicyDecision
# ---------------------------------------------------------------------------


class TestPolicyDecision:
    def test_bypasses_llm_for_block(self) -> None:
        d = PolicyDecision(action="block")
        assert d.bypasses_llm is True

    def test_bypasses_llm_false_for_pass_through(self) -> None:
        d = PolicyDecision(action="pass_through")
        assert d.bypasses_llm is False

    def test_bypasses_llm_for_skip(self) -> None:
        d = PolicyDecision(action="skip")
        assert d.bypasses_llm is True

    def test_allowed_true_for_pass_through(self) -> None:
        d = PolicyDecision(action="pass_through")
        assert d.allowed is True

    def test_allowed_false_for_block(self) -> None:
        d = PolicyDecision(action="block")
        assert d.allowed is False

    def test_allowed_true_for_skip(self) -> None:
        d = PolicyDecision(action="skip")
        assert d.allowed is True


# ---------------------------------------------------------------------------
# sender_domain matcher
# ---------------------------------------------------------------------------


class TestMatchSenderDomain:
    def test_exact_match(self) -> None:
        env = _email_envelope(sender="alerts@chase.com")
        assert _match_sender_domain(env, {"domain": "chase.com", "match": "exact"})

    def test_exact_no_match_subdomain(self) -> None:
        env = _email_envelope(sender="alerts@mail.chase.com")
        assert not _match_sender_domain(env, {"domain": "chase.com", "match": "exact"})

    def test_suffix_matches_exact(self) -> None:
        env = _email_envelope(sender="alerts@chase.com")
        assert _match_sender_domain(env, {"domain": "chase.com", "match": "suffix"})

    def test_suffix_matches_subdomain(self) -> None:
        env = _email_envelope(sender="alerts@mail.chase.com")
        assert _match_sender_domain(env, {"domain": "chase.com", "match": "suffix"})

    def test_suffix_no_false_positive(self) -> None:
        env = _email_envelope(sender="user@notchase.com")
        assert not _match_sender_domain(env, {"domain": "chase.com", "match": "suffix"})

    def test_case_insensitive(self) -> None:
        env = _email_envelope(sender="Alerts@CHASE.COM")
        assert _match_sender_domain(env, {"domain": "chase.com", "match": "exact"})

    def test_empty_domain_no_match(self) -> None:
        env = _email_envelope(sender="alerts@chase.com")
        assert not _match_sender_domain(env, {"domain": "", "match": "exact"})

    def test_catchall_any_match(self) -> None:
        env = _email_envelope(sender="anyone@anything.com")
        assert _match_sender_domain(env, {"domain": "*", "match": "any"})

    def test_catchall_wildcard_domain(self) -> None:
        env = _email_envelope(sender="anyone@anything.com")
        assert _match_sender_domain(env, {"domain": "*", "match": "exact"})


# ---------------------------------------------------------------------------
# sender_address matcher
# ---------------------------------------------------------------------------


class TestMatchSenderAddress:
    def test_exact_match(self) -> None:
        env = _email_envelope(sender="alerts@chase.com")
        assert _match_sender_address(env, {"address": "alerts@chase.com"})

    def test_case_insensitive(self) -> None:
        env = _email_envelope(sender="ALERTS@CHASE.COM")
        assert _match_sender_address(env, {"address": "alerts@chase.com"})

    def test_no_match(self) -> None:
        env = _email_envelope(sender="other@chase.com")
        assert not _match_sender_address(env, {"address": "alerts@chase.com"})

    def test_empty_target_no_match(self) -> None:
        env = _email_envelope(sender="alerts@chase.com")
        assert not _match_sender_address(env, {"address": ""})

    def test_catchall_wildcard(self) -> None:
        env = _email_envelope(sender="anyone@anything.com")
        assert _match_sender_address(env, {"address": "*"})


# ---------------------------------------------------------------------------
# header_condition matcher
# ---------------------------------------------------------------------------


class TestMatchHeaderCondition:
    def test_present_op_matches(self) -> None:
        env = _email_envelope(headers={"List-Unsubscribe": "<mailto:unsub@x.com>"})
        assert _match_header_condition(env, {"header": "List-Unsubscribe", "op": "present"})

    def test_present_op_no_match(self) -> None:
        env = _email_envelope(headers={})
        assert not _match_header_condition(env, {"header": "List-Unsubscribe", "op": "present"})

    def test_header_key_case_insensitive(self) -> None:
        env = _email_envelope(headers={"List-Unsubscribe": "yes"})
        assert _match_header_condition(env, {"header": "list-unsubscribe", "op": "present"})

    def test_equals_op(self) -> None:
        env = _email_envelope(headers={"Precedence": "bulk"})
        assert _match_header_condition(
            env, {"header": "Precedence", "op": "equals", "value": "bulk"}
        )

    def test_equals_op_no_match(self) -> None:
        env = _email_envelope(headers={"Precedence": "list"})
        assert not _match_header_condition(
            env, {"header": "Precedence", "op": "equals", "value": "bulk"}
        )

    def test_equals_with_whitespace_trimming(self) -> None:
        env = _email_envelope(headers={"Precedence": " bulk "})
        assert _match_header_condition(
            env, {"header": "Precedence", "op": "equals", "value": "bulk"}
        )

    def test_contains_op(self) -> None:
        env = _email_envelope(headers={"X-Spam-Status": "YES, score=8.0"})
        assert _match_header_condition(
            env, {"header": "X-Spam-Status", "op": "contains", "value": "YES"}
        )

    def test_contains_op_no_match(self) -> None:
        env = _email_envelope(headers={"X-Spam-Status": "NO, score=1.0"})
        assert not _match_header_condition(
            env, {"header": "X-Spam-Status", "op": "contains", "value": "YES"}
        )

    def test_missing_op_no_match(self) -> None:
        env = _email_envelope(headers={"Foo": "bar"})
        assert not _match_header_condition(env, {"header": "Foo"})

    def test_equals_with_none_value(self) -> None:
        env = _email_envelope(headers={"Foo": "bar"})
        assert not _match_header_condition(env, {"header": "Foo", "op": "equals", "value": None})


# ---------------------------------------------------------------------------
# mime_type matcher
# ---------------------------------------------------------------------------


class TestMatchMimeType:
    def test_exact_match(self) -> None:
        env = _email_envelope(mime_parts=["text/plain", "text/calendar"])
        assert _match_mime_type(env, {"type": "text/calendar"})

    def test_wildcard_subtype(self) -> None:
        env = _email_envelope(mime_parts=["image/jpeg"])
        assert _match_mime_type(env, {"type": "image/*"})

    def test_wildcard_no_match_different_type(self) -> None:
        env = _email_envelope(mime_parts=["text/plain"])
        assert not _match_mime_type(env, {"type": "image/*"})

    def test_no_match_empty_parts(self) -> None:
        env = _email_envelope(mime_parts=[])
        assert not _match_mime_type(env, {"type": "text/calendar"})

    def test_case_insensitive(self) -> None:
        env = _email_envelope(mime_parts=["TEXT/CALENDAR"])
        assert _match_mime_type(env, {"type": "text/calendar"})

    def test_empty_pattern_no_match(self) -> None:
        env = _email_envelope(mime_parts=["text/plain"])
        assert not _match_mime_type(env, {"type": ""})


# ---------------------------------------------------------------------------
# substring matcher
# ---------------------------------------------------------------------------


class TestMatchSubstring:
    def test_case_insensitive_match(self) -> None:
        env = IngestionEnvelope(raw_key="Hello World from alice@EXAMPLE.COM")
        assert _match_substring(env, {"pattern": "alice@example.com"})

    def test_no_match(self) -> None:
        env = IngestionEnvelope(raw_key="hello world")
        assert not _match_substring(env, {"pattern": "foobar"})

    def test_empty_pattern_no_match(self) -> None:
        env = IngestionEnvelope(raw_key="hello")
        assert not _match_substring(env, {"pattern": ""})

    def test_catchall_wildcard(self) -> None:
        env = IngestionEnvelope(raw_key="anything")
        assert _match_substring(env, {"pattern": "*"})


# ---------------------------------------------------------------------------
# chat_id matcher
# ---------------------------------------------------------------------------


class TestMatchChatId:
    def test_exact_match(self) -> None:
        env = _telegram_envelope(chat_id="12345")
        assert _match_chat_id(env, {"chat_id": "12345"})

    def test_no_match(self) -> None:
        env = _telegram_envelope(chat_id="12345")
        assert not _match_chat_id(env, {"chat_id": "99999"})

    def test_negative_chat_id(self) -> None:
        env = _telegram_envelope(chat_id="-100987654321")
        assert _match_chat_id(env, {"chat_id": "-100987654321"})

    def test_empty_target_no_match(self) -> None:
        env = _telegram_envelope(chat_id="12345")
        assert not _match_chat_id(env, {"chat_id": ""})

    def test_catchall_wildcard(self) -> None:
        env = _telegram_envelope(chat_id="12345")
        assert _match_chat_id(env, {"chat_id": "*"})


# ---------------------------------------------------------------------------
# channel_id matcher
# ---------------------------------------------------------------------------


class TestMatchChannelId:
    def test_exact_match(self) -> None:
        env = _discord_envelope(channel_id="987654321098765432")
        assert _match_channel_id(env, {"channel_id": "987654321098765432"})

    def test_no_match(self) -> None:
        env = _discord_envelope(channel_id="987654321098765432")
        assert not _match_channel_id(env, {"channel_id": "111111111111111111"})

    def test_empty_target_no_match(self) -> None:
        env = _discord_envelope(channel_id="987654321098765432")
        assert not _match_channel_id(env, {"channel_id": ""})

    def test_catchall_wildcard(self) -> None:
        env = _discord_envelope(channel_id="987654321098765432")
        assert _match_channel_id(env, {"channel_id": "*"})


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator.evaluate() — no rules
# ---------------------------------------------------------------------------


class TestEvaluateNoRules:
    def test_no_rules_returns_pass_through(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        # Simulate loaded (empty rules)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = []

        decision = evaluator.evaluate(_email_envelope())
        assert decision.action == "pass_through"
        assert decision.matched_rule_id is None
        assert decision.matched_rule_type is None

    def test_pass_through_bypasses_llm_is_false(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        decision = evaluator.evaluate(_email_envelope())
        assert decision.bypasses_llm is False

    def test_pass_through_allowed_is_true(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        decision = evaluator.evaluate(_email_envelope())
        assert decision.allowed is True


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator.evaluate() — first-match-wins
# ---------------------------------------------------------------------------


class TestEvaluateFirstMatchWins:
    def test_lower_priority_rule_wins(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                id="id-a",
                rule_type="sender_domain",
                condition={"domain": "chase.com", "match": "exact"},
                action="skip",
                priority=5,
            ),
            _rule(
                id="id-b",
                rule_type="sender_domain",
                condition={"domain": "chase.com", "match": "suffix"},
                action="route_to:finance",
                priority=10,
            ),
        ]
        decision = evaluator.evaluate(_email_envelope(sender="alerts@chase.com"))
        assert decision.action == "skip"
        assert decision.matched_rule_id == "id-a"

    def test_first_matching_rule_wins(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                id="id-first",
                rule_type="sender_domain",
                condition={"domain": "example.com", "match": "exact"},
                action="metadata_only",
                priority=1,
            ),
            _rule(
                id="id-second",
                rule_type="sender_domain",
                condition={"domain": "example.com", "match": "exact"},
                action="route_to:general",
                priority=2,
            ),
        ]
        decision = evaluator.evaluate(_email_envelope(sender="user@example.com"))
        assert decision.matched_rule_id == "id-first"
        assert decision.action == "metadata_only"

    def test_non_matching_rule_skipped(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                id="id-a",
                rule_type="sender_address",
                condition={"address": "specific@example.com"},
                action="skip",
                priority=1,
            ),
            _rule(
                id="id-b",
                rule_type="sender_domain",
                condition={"domain": "example.com", "match": "exact"},
                action="route_to:general",
                priority=2,
            ),
        ]
        decision = evaluator.evaluate(_email_envelope(sender="other@example.com"))
        assert decision.matched_rule_id == "id-b"
        assert decision.action == "route_to"
        assert decision.target_butler == "general"


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator.evaluate() — all action types
# ---------------------------------------------------------------------------


class TestEvaluateActionTypes:
    def test_route_to_parses_target(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "paypal.com", "match": "suffix"},
                action="route_to:finance",
            )
        ]
        decision = evaluator.evaluate(_email_envelope(sender="service@paypal.com"))
        assert decision.action == "route_to"
        assert decision.target_butler == "finance"
        assert decision.bypasses_llm is True

    def test_block_action(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="connector:gmail:gmail:user:dev", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "spam.com", "match": "exact"},
                action="block",
            )
        ]
        decision = evaluator.evaluate(_email_envelope(sender="bad@spam.com"))
        assert decision.action == "block"
        assert decision.allowed is False
        assert decision.bypasses_llm is True

    def test_skip_action(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "Auto-Submitted", "op": "equals", "value": "auto-generated"},
                action="skip",
            )
        ]
        decision = evaluator.evaluate(_email_envelope(headers={"Auto-Submitted": "auto-generated"}))
        assert decision.action == "skip"
        assert decision.bypasses_llm is True

    def test_metadata_only_action(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "List-Unsubscribe", "op": "present"},
                action="metadata_only",
            )
        ]
        decision = evaluator.evaluate(_email_envelope(headers={"List-Unsubscribe": "yes"}))
        assert decision.action == "metadata_only"

    def test_low_priority_queue_action(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="header_condition",
                condition={"header": "Precedence", "op": "equals", "value": "bulk"},
                action="low_priority_queue",
            )
        ]
        decision = evaluator.evaluate(_email_envelope(headers={"Precedence": "bulk"}))
        assert decision.action == "low_priority_queue"

    def test_explicit_pass_through_action(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_address",
                condition={"address": "vip@example.com"},
                action="pass_through",
            )
        ]
        decision = evaluator.evaluate(_email_envelope(sender="vip@example.com"))
        assert decision.action == "pass_through"
        assert decision.bypasses_llm is False


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator.evaluate() — unknown rule_type handling
# ---------------------------------------------------------------------------


class TestEvaluateUnknownRuleType:
    def test_unknown_rule_type_skipped(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                id="id-bad",
                rule_type="totally_unknown",
                condition={"foo": "bar"},
                action="skip",
                priority=1,
            ),
            _rule(
                id="id-good",
                rule_type="sender_domain",
                condition={"domain": "example.com", "match": "exact"},
                action="route_to:general",
                priority=2,
            ),
        ]
        decision = evaluator.evaluate(_email_envelope(sender="user@example.com"))
        assert decision.matched_rule_id == "id-good"
        assert decision.action == "route_to"


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator.evaluate() — error handling in rule evaluation
# ---------------------------------------------------------------------------


class TestEvaluateErrorHandling:
    def test_exception_in_matcher_skips_rule(self) -> None:
        """If a matcher raises, the rule is skipped and evaluation continues."""
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                id="id-bad",
                rule_type="sender_domain",
                condition=None,  # type: ignore[arg-type]
                action="skip",
                priority=1,
            ),
            _rule(
                id="id-good",
                rule_type="sender_address",
                condition={"address": "user@example.com"},
                action="route_to:general",
                priority=2,
            ),
        ]
        # The first rule has condition=None which becomes {}, and should not crash
        decision = evaluator.evaluate(_email_envelope(sender="user@example.com"))
        assert decision.matched_rule_id == "id-good"


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator.evaluate() — connector-scoped evaluation
# ---------------------------------------------------------------------------


class TestConnectorScopedEvaluation:
    def test_connector_block_rule(self) -> None:
        evaluator = IngestionPolicyEvaluator(
            scope="connector:gmail:gmail:user:alice@example.com", db_pool=None
        )
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "spam.com", "match": "suffix"},
                action="block",
            )
        ]
        decision = evaluator.evaluate(_email_envelope(sender="spammer@spam.com"))
        assert decision.action == "block"
        assert decision.allowed is False

    def test_connector_pass_through_allows(self) -> None:
        evaluator = IngestionPolicyEvaluator(
            scope="connector:gmail:gmail:user:alice@example.com", db_pool=None
        )
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_address",
                condition={"address": "vip@example.com"},
                action="pass_through",
                priority=1,
            ),
            _rule(
                rule_type="sender_domain",
                condition={"domain": "*", "match": "any"},
                action="block",
                priority=1000,
            ),
        ]
        # VIP passes through
        decision = evaluator.evaluate(_email_envelope(sender="vip@example.com"))
        assert decision.action == "pass_through"
        assert decision.allowed is True

    def test_connector_catchall_block(self) -> None:
        evaluator = IngestionPolicyEvaluator(
            scope="connector:gmail:gmail:user:alice@example.com", db_pool=None
        )
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="sender_address",
                condition={"address": "vip@example.com"},
                action="pass_through",
                priority=1,
            ),
            _rule(
                rule_type="sender_domain",
                condition={"domain": "*", "match": "any"},
                action="block",
                priority=1000,
            ),
        ]
        # Non-VIP hits catch-all block
        decision = evaluator.evaluate(_email_envelope(sender="random@other.com"))
        assert decision.action == "block"
        assert decision.allowed is False

    def test_telegram_chat_id_block(self) -> None:
        evaluator = IngestionPolicyEvaluator(
            scope="connector:telegram-bot:telegram-bot:my-bot", db_pool=None
        )
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="chat_id",
                condition={"chat_id": "-100987654321"},
                action="block",
            )
        ]
        decision = evaluator.evaluate(_telegram_envelope(chat_id="-100987654321"))
        assert decision.action == "block"
        assert decision.allowed is False

    def test_discord_channel_id_block(self) -> None:
        evaluator = IngestionPolicyEvaluator(
            scope="connector:discord:discord:my-server", db_pool=None
        )
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                rule_type="channel_id",
                condition={"channel_id": "987654321098765432"},
                action="block",
            )
        ]
        decision = evaluator.evaluate(_discord_envelope(channel_id="987654321098765432"))
        assert decision.action == "block"
        assert decision.allowed is False


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator — DB loading
# ---------------------------------------------------------------------------


class TestEvaluatorDBLoading:
    async def test_ensure_loaded_calls_load_rules(self) -> None:
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=mock_pool)
        await evaluator.ensure_loaded()

        mock_pool.fetch.assert_called_once()
        # Verify the query includes scope filter
        call_args = mock_pool.fetch.call_args
        assert "scope = $1" in call_args[0][0]
        assert call_args[0][1] == "global"

    async def test_ensure_loaded_idempotent(self) -> None:
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(return_value=[])

        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=mock_pool)
        await evaluator.ensure_loaded()
        await evaluator.ensure_loaded()

        # Only one DB call despite two ensure_loaded() calls
        assert mock_pool.fetch.call_count == 1

    async def test_no_pool_skips_load(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        await evaluator.ensure_loaded()
        assert evaluator._last_loaded_at is not None
        assert evaluator._rules == []

    async def test_db_error_retains_stale_cache(self) -> None:
        mock_pool = AsyncMock()
        stale_rules = [
            _rule(
                rule_type="sender_domain",
                condition={"domain": "stale.com", "match": "exact"},
                action="skip",
            )
        ]

        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=mock_pool)
        evaluator._rules = stale_rules
        evaluator._last_loaded_at = 0.0  # force stale

        # Make DB fetch raise
        mock_pool.fetch = AsyncMock(side_effect=Exception("connection refused"))

        await evaluator._load_rules()

        # Stale rules preserved
        assert evaluator._rules == stale_rules
        # Timestamp updated to prevent hammering
        assert evaluator._last_loaded_at is not None
        assert evaluator._last_loaded_at > 0.0

    async def test_invalid_rows_skipped_during_load(self) -> None:
        """Rules with non-dict condition or unknown rule_type are skipped."""
        mock_pool = AsyncMock()
        mock_pool.fetch = AsyncMock(
            return_value=[
                # Valid rule
                {
                    "id": "valid-1",
                    "rule_type": "sender_domain",
                    "condition": {"domain": "good.com", "match": "exact"},
                    "action": "skip",
                    "priority": 1,
                    "name": None,
                    "created_at": "2026-01-01T00:00:00Z",
                },
                # Invalid: bad condition type
                {
                    "id": "invalid-1",
                    "rule_type": "sender_domain",
                    "condition": "not-a-dict",
                    "action": "skip",
                    "priority": 2,
                    "name": None,
                    "created_at": "2026-01-01T00:00:00Z",
                },
                # Invalid: unknown rule_type
                {
                    "id": "invalid-2",
                    "rule_type": "totally_bogus",
                    "condition": {"foo": "bar"},
                    "action": "skip",
                    "priority": 3,
                    "name": None,
                    "created_at": "2026-01-01T00:00:00Z",
                },
            ]
        )

        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=mock_pool)
        await evaluator._load_rules()

        # Only the valid rule should be loaded
        assert len(evaluator._rules) == 1
        assert evaluator._rules[0]["id"] == "valid-1"


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator — TTL refresh
# ---------------------------------------------------------------------------


class TestEvaluatorTTLRefresh:
    def test_no_refresh_when_fresh(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None, refresh_interval_s=60)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = []

        with patch.object(asyncio, "create_task") as mock_create_task:
            evaluator.evaluate(_email_envelope())
            mock_create_task.assert_not_called()

    def test_refresh_scheduled_when_stale(self) -> None:
        mock_pool = MagicMock()
        evaluator = IngestionPolicyEvaluator(
            scope="global", db_pool=mock_pool, refresh_interval_s=60
        )
        # Set last loaded to far in the past
        evaluator._last_loaded_at = time.monotonic() - 120
        evaluator._rules = []

        def _consume_coro_and_return_mock(coro):
            """Close the coroutine to avoid RuntimeWarning, return a mock task."""
            coro.close()
            mock_task = MagicMock()
            mock_task.done.return_value = False
            return mock_task

        with patch.object(
            asyncio, "create_task", side_effect=_consume_coro_and_return_mock
        ) as mock_create_task:
            evaluator.evaluate(_email_envelope())
            mock_create_task.assert_called_once()

    def test_no_stacking_refresh_tasks(self) -> None:
        mock_pool = MagicMock()
        evaluator = IngestionPolicyEvaluator(
            scope="global", db_pool=mock_pool, refresh_interval_s=60
        )
        evaluator._last_loaded_at = time.monotonic() - 120

        # Simulate existing running task
        mock_task = MagicMock()
        mock_task.done.return_value = False
        evaluator._background_refresh_task = mock_task

        with patch.object(asyncio, "create_task") as mock_create_task:
            evaluator.evaluate(_email_envelope())
            mock_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator — invalidate
# ---------------------------------------------------------------------------


class TestEvaluatorInvalidate:
    def test_invalidate_sets_timestamp_to_zero(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()

        evaluator.invalidate()

        assert evaluator._last_loaded_at == 0.0

    def test_invalidate_triggers_refresh_on_next_evaluate(self) -> None:
        mock_pool = MagicMock()
        evaluator = IngestionPolicyEvaluator(
            scope="global", db_pool=mock_pool, refresh_interval_s=60
        )
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = []

        evaluator.invalidate()

        def _consume_coro_and_return_mock(coro):
            coro.close()
            mock_task = MagicMock()
            mock_task.done.return_value = False
            return mock_task

        with patch.object(
            asyncio, "create_task", side_effect=_consume_coro_and_return_mock
        ) as mock_create_task:
            evaluator.evaluate(_email_envelope())
            mock_create_task.assert_called_once()


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator — scope property
# ---------------------------------------------------------------------------


class TestEvaluatorScope:
    def test_scope_property(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="connector:gmail:gmail:user:dev", db_pool=None)
        assert evaluator.scope == "connector:gmail:gmail:user:dev"

    def test_rules_property_empty_by_default(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        assert evaluator.rules == []


# ---------------------------------------------------------------------------
# IngestionPolicyEvaluator — mixed rule_types in one scope
# ---------------------------------------------------------------------------


class TestMixedRuleTypes:
    def test_global_scope_with_multiple_rule_types(self) -> None:
        evaluator = IngestionPolicyEvaluator(scope="global", db_pool=None)
        evaluator._last_loaded_at = time.monotonic()
        evaluator._rules = [
            _rule(
                id="r1",
                rule_type="header_condition",
                condition={"header": "X-Priority", "op": "equals", "value": "1"},
                action="skip",
                priority=1,
            ),
            _rule(
                id="r2",
                rule_type="sender_domain",
                condition={"domain": "bank.com", "match": "exact"},
                action="route_to:finance",
                priority=5,
            ),
            _rule(
                id="r3",
                rule_type="mime_type",
                condition={"type": "text/calendar"},
                action="route_to:calendar",
                priority=10,
            ),
        ]

        # Test: header match takes priority
        d1 = evaluator.evaluate(
            _email_envelope(
                sender="x@bank.com",
                headers={"X-Priority": "1"},
                mime_parts=["text/calendar"],
            )
        )
        assert d1.matched_rule_id == "r1"
        assert d1.action == "skip"

        # Test: no header match -> falls through to domain match
        d2 = evaluator.evaluate(
            _email_envelope(sender="alerts@bank.com", mime_parts=["text/calendar"])
        )
        assert d2.matched_rule_id == "r2"
        assert d2.action == "route_to"
        assert d2.target_butler == "finance"

        # Test: no header/domain match -> falls through to mime match
        d3 = evaluator.evaluate(
            _email_envelope(sender="user@other.com", mime_parts=["text/calendar"])
        )
        assert d3.matched_rule_id == "r3"
        assert d3.action == "route_to"
        assert d3.target_butler == "calendar"

        # Test: no match at all -> pass_through
        d4 = evaluator.evaluate(_email_envelope(sender="user@other.com", mime_parts=["text/plain"]))
        assert d4.action == "pass_through"
